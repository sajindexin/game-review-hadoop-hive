# -*- coding: utf-8 -*-
"""
在 240 条语义金标上做三方法对比：
  1) baseline_voted_up : 把整体情感赋给所有提及主题（朴素基线）
  2) rule_absm         : 规则方面级打标
  3) rbt3              : 金标微调后的 RBT3（aspect 层）
输出方面级 micro/macro P/R/F1，可直接填进效果对比方案.md。
"""
from __future__ import annotations
import os, csv, json, sys, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rule_labeler import label_review

ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
TASKS = os.path.join(ANN, "annotation_tasks.csv")


def load_gold():
    """从标注集 gold_aspects_json 读取主题层金标（与训练数据同一来源）。

    merge_gold.py 合并进来的新增标注会同时进入训练与评估，避免两者口径不一致。
    """
    gold = {}
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            gj = (r.get("gold_aspects_json") or "").strip()
            if not gj:
                continue
            try:
                items = json.loads(gj)
            except Exception:
                continue
            pairs = [(a["aspect"], a["sentiment"]) for a in items
                     if a.get("aspect") and a.get("sentiment") in ("positive", "negative", "neutral")]
            # 空数组 [] 表示"该评论已标注、但无可标注主题"，也是一条金标评论，
            # 需保留（旧 GOLD 字典同样含空列表条目），否则训练评论数会少 4 条。
            gold[int(r["review_id"])] = pairs
    return gold


def to_set(aspects):
    return {(a["aspect"], a["sentiment"]) for a in aspects if a.get("sentiment") != "neutral"}


def prf(preds, golds):
    tp = fp = fn = 0
    for p, g in zip(preds, golds):
        tp += len(p & g); fp += len(p - g); fn += len(g - p)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def macro_prf(preds, golds):
    """按 aspect 分组算 F1 再平均（macro，避免大主题主导）。"""
    from collections import defaultdict
    per = defaultdict(lambda: [0, 0, 0])  # aspect -> [tp, fp, fn]
    for p, g in zip(preds, golds):
        pa = {a for a, s in p}; ga = {a for a, s in g}
        for a in pa & ga:
            per[a][0] += int((a, "positive") in p and (a, "positive") in g) or \
                         int((a, "negative") in p and (a, "negative") in g)
        # 更精确：按 (aspect,sentiment) 三元组归类
    # 重新按 (aspect,sentiment) 累加
    per2 = defaultdict(lambda: [0, 0, 0])
    for p, g in zip(preds, golds):
        for t in p - g: per2[t[0]][1] += 1
        for t in g - p: per2[t[0]][2] += 1
        for t in p & g: per2[t[0]][0] += 1
    f1s = []
    for a, (tp, fp, fn) in per2.items():
        pr = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        f = 2 * pr * rc / (pr + rc) if pr + rc else 0
        f1s.append(f)
    return sum(f1s) / len(f1s) if f1s else 0.0


def load_data():
    gold_all = load_gold()
    data = []
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = int(r["review_id"])
            if rid not in gold_all:
                continue
            data.append({"id": rid, "text": r["review_text"],
                         "voted_up": r.get("voted_up", ""),
                         "gold": set(gold_all[rid])})
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rbt3", action="store_true", help="是否评估 RBT3 模型")
    ap.add_argument("--rbt3-model", default=os.path.normpath(os.path.join(HERE, "..", "data", "rbt3_model_aspect")))
    ap.add_argument("--heldout", action="store_true",
                    help="严格口径：只在模型训练时未见过的 eval 评论上评估三个方法")
    ap.add_argument("--eval-ids", default="",
                    help="留出集 ID JSON；默认读取模型目录的 holdout_ids.json，再回退标注集 gold_eval_ids.json")
    args = ap.parse_args()

    data = load_data()
    if args.heldout:
        ids_path = args.eval_ids
        if not ids_path:
            model_dir = args.rbt3_model if os.path.isdir(args.rbt3_model) else os.path.dirname(os.path.abspath(args.rbt3_model))
            model_ids = os.path.join(model_dir, "holdout_ids.json")
            ids_path = model_ids if os.path.exists(model_ids) else os.path.join(ANN, "gold_eval_ids.json")
        with open(ids_path, encoding="utf-8") as f:
            eval_ids = set(json.load(f))
        data = [d for d in data if d["id"] in eval_ids]
        print(f"严格留出集评估：模型未见过的 {len(data)} 条评论（三方法口径一致）")
        print(f"留出集来源: {ids_path}\n")
    else:
        print(f"语义金标样本: {len(data)} 条\n")

    # ---- 1. baseline_voted_up ----
    # 整体情感正/负 -> 赋给该评论提及的每个主题
    up_preds, up_golds = [], []
    for d in data:
        overall = "positive" if str(d["voted_up"]).lower() in ("true", "1", "yes") else "negative"
        aspects = {a for a, s in d["gold"]}
        up_preds.append({(a, overall) for a in aspects})
        up_golds.append(d["gold"])
    p, r, f1 = prf(up_preds, up_golds)
    mf1 = macro_prf(up_preds, up_golds)
    print("=== ① baseline_voted_up（整体情感当主题情感）===")
    print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
    print(f"  macro-F1:     {mf1:.4f}\n")

    # ---- 2. rule_absm ----
    rule_preds, rule_golds = [], []
    for d in data:
        res = label_review(d["text"])
        rule_preds.append(to_set(res["aspects"]))
        rule_golds.append(d["gold"])
    p, r, f1 = prf(rule_preds, rule_golds)
    mf1 = macro_prf(rule_preds, rule_golds)
    print("=== ② rule_absm（规则方面级打标）===")
    print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
    print(f"  macro-F1:     {mf1:.4f}\n")

    # ---- 3. rbt3 ----
    rbt3_failed = False
    if args.rbt3:
        try:
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            from lexicon import ASPECTS
            tok = AutoTokenizer.from_pretrained(args.rbt3_model)
            model = AutoModelForSequenceClassification.from_pretrained(args.rbt3_model)
            model.eval()
            id2sent = {0: "negative", 1: "neutral", 2: "positive"}
            asp_ids = list(ASPECTS.keys())
            prompts = [f"评价方面：{ASPECTS[a]['name_zh']}" for a in asp_ids]

            rbt3_preds, rbt3_golds = [], []
            with torch.no_grad():
                for d in data:
                    pred = set()
                    # 模型对全部 11 主题三分类，不依赖规则门控
                    for i, a in enumerate(asp_ids):
                        enc = tok(prompts[i], d["text"], truncation=True, max_length=160, return_tensors="pt")
                        out = model(**enc)
                        sid = int(torch.argmax(out.logits, dim=-1))
                        sent = id2sent.get(sid, "neutral")
                        if sent != "neutral":
                            pred.add((a, sent))
                    rbt3_preds.append(pred)
                    rbt3_golds.append(d["gold"])
            p, r, f1 = prf(rbt3_preds, rbt3_golds)
            mf1 = macro_prf(rbt3_preds, rbt3_golds)
            print("=== ③ rbt3（金标微调，全主题三分类，无规则门控）===")
            print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
            print(f"  macro-F1:     {mf1:.4f}\n")
        except Exception as e:
            rbt3_failed = True
            print(f"RBT3 评估失败: {e}\n", file=sys.stderr)

    if args.heldout:
        if rbt3_failed:
            print("说明：本次仅完成 ① voted_up 与 ② rule_absm 两个基线；RBT3 评估失败，")
            print("      三项对比尚未完成，不能据此写'三方法均已评估'的结论。")
        else:
            print(f"说明：严格留出集口径。三方法都在 RBT3 未见过的 {len(data)} 条 eval 评论上评估，")
            print("      留出集以模型目录 holdout_ids.json（或 --eval-ids）为准，可直接写进报告。")
    else:
        print(f"说明：在全体 {len(data)} 条语义金标上评估（含 RBT3 训练用过的评论，数字偏乐观）。")
        print("      写报告请用 --heldout 的严格留出集数字。")

    # 显式要求 --rbt3 却没跑成：返回非零退出码，避免自动验收把失败当成成功
    if args.rbt3 and rbt3_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
