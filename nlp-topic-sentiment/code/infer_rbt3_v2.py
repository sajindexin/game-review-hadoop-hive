# -*- coding: utf-8 -*-
"""
RBT3 两层标签推理（v2）：通用主题(11) + 具体更新条目(75)

模型：分别加载 rbt3_model_aspect / rbt3_model_item
条目层只遍历该 appid 的候选条目（APPID_ITEMS 约束），避免跨游戏误判。
未训练时可 --rule-fallback 用规则引擎输出，保证随时可演示。

用法：
  python infer_rbt3_v2.py --text "姜岛探索很好玩，但是八人联机老是掉线" --appid 413150 --rule-fallback
  python infer_rbt3_v2.py --text "..." --appid 413150   # 用训练好的模型
  python infer_rbt3_v2.py --input reviews.txt --output pred.jsonl
"""
from __future__ import annotations
import os
import sys
import json
import argparse

# 国内网络：默认走 hf-mirror 镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lexicon import ASPECTS, ASPECT_IDS
from item_lexicon import ITEMS, APPID_ITEMS, ITEM2ASPECT

ASPECT_PROMPT = {
    "content": "内容玩法", "level_design": "地图关卡设计", "multiplayer": "联机服务器",
    "graphics": "画质美术", "performance": "性能优化", "story": "剧情叙事",
    "controls": "操作手感", "monetization": "付费氪金", "cheating": "外挂反作弊",
    "audio": "音效音乐", "community": "运营社区",
}
ID2SENT = {0: "negative", 1: "neutral", 2: "positive"}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aspect-model", default=os.path.join(HERE, "..", "data", "rbt3_model_aspect"))
    ap.add_argument("--item-model", default=os.path.join(HERE, "..", "data", "rbt3_model_item"))
    ap.add_argument("--text", default="")
    ap.add_argument("--appid", default="", help="游戏 appid（条目层必需）")
    ap.add_argument("--input", default="", help="每行 JSON: {text, appid}")
    ap.add_argument("--output", default="")
    ap.add_argument("--rule-fallback", action="store_true")
    ap.add_argument("--no-rule-gate", action="store_true",
                    help="关闭条目层规则召回门控（金标补齐负样本后可用）")
    ap.add_argument("--threshold", type=float, default=0.5)
    return ap.parse_args()


def item_prompt(item_id):
    meta = ITEMS.get(item_id, {})
    title = meta.get("title", item_id)
    desc = (meta.get("description") or "")[:60]
    return f"这条评论是否在讨论该游戏更新：{title}。{desc}"


def rule_predict(text, appid):
    from rule_labeler import label_review
    res = label_review(text, appid=appid or None)
    aspects = [{"aspect": a["aspect"], "name_zh": a["name_zh"],
                "sentiment": a["sentiment"], "confidence": a["confidence"]}
               for a in res["aspects"]]
    items = [{"item_id": it["item_id"], "title": it["title"],
              "aspect": it["aspect"], "sentiment": it["sentiment"],
              "confidence": it["confidence"]}
             for it in res["items"]]
    return aspects, items, "rule"


def load_model(path):
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    if not os.path.exists(os.path.join(path, "config.json")):
        return None
    tok = AutoTokenizer.from_pretrained(path)
    mdl = AutoModelForSequenceClassification.from_pretrained(path).eval()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mdl.to(dev)
    return tok, mdl, dev


def classify_pairs(tok, mdl, dev, pairs, threshold):
    """pairs = [(prompt, text), ...] -> [(sentiment, conf), ...]"""
    import torch
    out = []
    with torch.no_grad():
        for prompt, text in pairs:
            enc = tok(prompt, text, truncation=True, max_length=160, return_tensors="pt").to(dev)
            probs = torch.softmax(mdl(**enc).logits, -1)[0].tolist()
            p_neg, p_neu, p_pos = probs
            if p_pos >= threshold and p_pos >= p_neg:
                out.append(("positive", p_pos))
            elif p_neg >= threshold and p_neg >= p_pos:
                out.append(("negative", p_neg))
            else:
                out.append(("neutral", max(p_neg, p_neu, p_pos)))
    return out


def model_predict(text, appid, asp_model, item_model, threshold, rule_gate=True):
    """RBT3 两层推理。

    rule_gate（条目层规则召回门控，默认开）：
      银标训练数据只含"命中条目"的评论，缺乏"未提及"负样本，RBT3 容易对所有条目
      都判"相关"。因此先用规则引擎(appid 约束 + 官方别名)筛出真正命中的条目，
      再交给 RBT3 判情感。这样结合规则的高精确率召回 + RBT3 的情感泛化能力。
      金标补齐"未提及"负样本重训后，可加 --no-rule-gate 让模型独立判别。
    """
    aspects, items = [], []
    if asp_model:
        # 主题层也用规则召回门控：银标只含命中主题的评论、缺"未提及"负样本，
        # RBT3 易对所有主题判相关/正向。用规则先筛出真正命中的主题，再让 RBT3 判情感。
        gate_aspects = None
        if rule_gate:
            from rule_labeler import label_review as _rule
            gate_aspects = {a["aspect"] for a in _rule(text, appid=appid)["aspects"]}
        pairs = [(f"评价方面：{ASPECT_PROMPT[a]}", text) for a in ASPECT_IDS]
        for aid, (sent, conf) in zip(ASPECT_IDS, classify_pairs(*asp_model, pairs, threshold)):
            if gate_aspects is not None and aid not in gate_aspects:
                continue
            if sent != "neutral":
                aspects.append({"aspect": aid, "name_zh": ASPECTS[aid]["name_zh"],
                                "sentiment": sent, "confidence": round(conf, 4),
                                "gate": "rule+rbt3" if gate_aspects is not None else "rbt3"})

    cand = APPID_ITEMS.get(str(appid), []) if appid else []
    if item_model and cand:
        # 规则召回门控：只保留规则确认命中的条目
        gate_ids = None
        if rule_gate:
            from rule_labeler import split_clauses
            from item_matcher import match_items
            hits = match_items(split_clauses(text), appid)
            gate_ids = {h["item_id"] for h in hits}
            if not gate_ids:
                gate_ids = set()  # 规则无命中 -> 条目层不输出
        pairs = [(item_prompt(iid), text) for iid in cand]
        results = classify_pairs(*item_model, pairs, threshold)
        for iid, (sent, conf) in zip(cand, results):
            if gate_ids is not None and iid not in gate_ids:
                continue
            if sent != "neutral":
                meta = ITEMS[iid]
                items.append({"item_id": iid, "title": meta.get("title", ""),
                              "aspect": ITEM2ASPECT.get(iid, ""),
                              "sentiment": sent, "confidence": round(conf, 4),
                              "gate": "rule+rbt3" if gate_ids is not None else "rbt3"})
    return aspects, items, "rbt3"


def main():
    args = parse_args()
    texts = []
    if args.text:
        texts = [{"text": args.text, "appid": args.appid}]
    elif args.input:
        with open(args.input, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    texts.append(json.loads(ln))
                except Exception:
                    texts.append({"text": ln, "appid": args.appid})
    if not texts:
        raise SystemExit("请用 --text 或 --input 提供评论")

    use_model = not args.rule_fallback
    asp_model = item_model = None
    if use_model:
        asp_model = load_model(args.aspect_model)
        item_model = load_model(args.item_model)
        print(f"主题层模型: {'已加载' if asp_model else '未找到 -> 该层跳过'}")
        print(f"条目层模型: {'已加载' if item_model else '未找到 -> 该层跳过'}")
        if not asp_model and not item_model:
            print("未找到任何模型，自动回退规则引擎")
            use_model = False

    outputs = []
    for rec in texts:
        text, appid = rec.get("text", ""), rec.get("appid", "")
        if use_model and (asp_model or item_model):
            aspects, items, src = model_predict(text, appid, asp_model, item_model,
                                                args.threshold, rule_gate=not args.no_rule_gate)
        else:
            aspects, items, src = rule_predict(text, appid)
        out = {"text": text, "appid": appid, "source": src,
               "aspects": aspects, "items": items}
        outputs.append(out)
        print(f"\n原文: {text} | appid={appid} | source={src}")
        for a in aspects:
            print(f"  [主题] {a['name_zh']}({a['aspect']}): {a['sentiment']} conf={a['confidence']}")
        for it in items:
            print(f"  [条目] {it['title']}({it['item_id']}): {it['sentiment']} conf={it['confidence']}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for rec in outputs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n结果已保存: {args.output}")


if __name__ == "__main__":
    main()
