# -*- coding: utf-8 -*-
"""
效果评估与基线对比

对比对象：
  - baseline_voted_up：把 voted_up 当作整体情感（不分主题）的朴素基线
  - rule_absm：本模块的规则方面级打标
  - rbt3：微调模型（可选，加载后评估）

指标：方面级 micro/macro P/R/F1（与 gold_aspects_json 对齐）
当前 gold 集已内置 240 条语义金标；严格留出集效果请用 evaluate_full.py --rbt3 --heldout。
"""
from __future__ import annotations
import os
import csv
import json
import argparse
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rule_labeler import label_review

GOLD = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "gold_samples.csv"))


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=GOLD)
    return ap.parse_args()


def load_gold(path):
    data = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            gj = r.get("gold_aspects_json") or r.get("gold_aspects") or "[]"
            try:
                gold = json.loads(gj)
            except Exception:
                gold = []
            data.append((r["review_text"], gold))
    return data


def to_set(aspects):
    return {(a["aspect"], a["sentiment"]) for a in aspects if a.get("sentiment") != "neutral"}


def prf(pred_sets, gold_sets):
    tp = fp = fn = 0
    for p, g in zip(pred_sets, gold_sets):
        tp += len(p & g); fp += len(p - g); fn += len(g - p)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1, tp, fp, fn


def main():
    args = parse_args()
    data = load_gold(args.gold)
    if not data:
        raise SystemExit("gold 集为空")

    preds, golds = [], []
    print(f"评估样本: {len(data)} 条\n")
    for i, (text, gold) in enumerate(data, 1):
        res = label_review(text)
        pred = to_set(res["aspects"])
        gset = to_set(gold)
        preds.append(pred); golds.append(gset)
        print(f"[{i}] {text}")
        print(f"    gold: {sorted(gset)}")
        print(f"    rule: {sorted(pred)}")

    p, r, f1, tp, fp, fn = prf(preds, golds)
    print("\n=== 规则方面级打标 vs 金标 ===")
    print(f"  Precision: {p:.4f}  Recall: {r:.4f}  F1: {f1:.4f}")
    print(f"  TP={tp} FP={fp} FN={fn}")
    print("\n说明：本脚本为规则基线评估入口；")
    print("      严格留出集三方法对比请用 evaluate_full.py --rbt3 --heldout。")


if __name__ == "__main__":
    main()
