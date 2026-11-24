# -*- coding: utf-8 -*-
"""
RBT3 主题情感推理：加载微调模型，对评论预测各主题情感。
未训练时可加 --rule-fallback 用规则引擎输出（保证随时可演示）。

用法：
  python infer_rbt3.py --text "新关卡好玩，但联机老掉线" --model ../data/rbt3_model
  python infer_rbt3.py --input reviews.txt --model ../data/rbt3_model --output pred.jsonl
  python infer_rbt3.py --text "..." --rule-fallback   # 无模型时用规则
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

ASPECT_PROMPT = {
    "content": "内容玩法", "level_design": "地图关卡设计", "multiplayer": "联机服务器",
    "graphics": "画质美术", "performance": "性能优化", "story": "剧情叙事",
    "controls": "操作手感", "monetization": "付费氪金", "cheating": "外挂反作弊",
    "audio": "音效音乐", "community": "运营社区",
}
ID2SENT = {0: "negative", 1: "neutral", 2: "positive"}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(HERE, "..", "data", "rbt3_model"))
    ap.add_argument("--text", default="", help="单条评论")
    ap.add_argument("--input", default="", help="文本文件，每行一条评论")
    ap.add_argument("--output", default="", help="预测结果 jsonl")
    ap.add_argument("--rule-fallback", action="store_true", help="无模型时用规则引擎")
    ap.add_argument("--threshold", type=float, default=0.5, help="正/负判定阈值(neutral 忽略)")
    return ap.parse_args()


def rule_predict(text):
    from rule_labeler import label_review
    res = label_review(text)
    return [{"aspect": a["aspect"], "name_zh": a["name_zh"],
             "sentiment": a["sentiment"], "confidence": a["confidence"]}
            for a in res["aspects"]], "rule"


def load_model(model_path):
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    tok = AutoTokenizer.from_pretrained(model_path)
    mdl = AutoModelForSequenceClassification.from_pretrained(model_path).eval()
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mdl.to(dev)
    return tok, mdl, dev


def model_predict(text, tok, mdl, dev, threshold):
    import torch
    results = []
    with torch.no_grad():
        for aid in ASPECT_IDS:
            prompt = f"评价方面：{ASPECT_PROMPT[aid]}"
            enc = tok(prompt, text, truncation=True, max_length=128,
                      return_tensors="pt").to(dev)
            probs = torch.softmax(mdl(**enc).logits, -1)[0]
            p_neg, p_neu, p_pos = probs.tolist()
            if p_pos >= threshold and p_pos >= p_neg:
                sent, conf = "positive", p_pos
            elif p_neg >= threshold and p_neg >= p_pos:
                sent, conf = "negative", p_neg
            else:
                continue  # neutral 或不确定，不输出
            results.append({"aspect": aid, "name_zh": ASPECTS[aid]["name_zh"],
                            "sentiment": sent, "confidence": round(conf, 4)})
    return results, "rbt3"


def main():
    args = parse_args()
    texts = []
    if args.text:
        texts = [args.text]
    elif args.input:
        with open(args.input, encoding="utf-8") as f:
            texts = [ln.strip() for ln in f if ln.strip()]
    if not texts:
        raise SystemExit("请用 --text 或 --input 提供评论")

    use_model = os.path.exists(os.path.join(args.model, "config.json"))
    tok = mdl = dev = None
    if use_model and not args.rule_fallback:
        tok, mdl, dev = load_model(args.model)
        print(f"使用 RBT3 模型: {args.model}")
    else:
        print("使用规则引擎（未找到模型或指定了 --rule-fallback）")

    outputs = []
    for text in texts:
        if tok:
            aspects, src = model_predict(text, tok, mdl, dev, args.threshold)
        else:
            aspects, src = rule_predict(text)
        rec = {"text": text, "source": src, "aspects": aspects}
        outputs.append(rec)
        print(f"\n原文: {text}")
        for a in aspects:
            print(f"  {a['name_zh']}({a['aspect']}): {a['sentiment']} conf={a['confidence']}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for rec in outputs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"\n结果已保存: {args.output}")


if __name__ == "__main__":
    main()
