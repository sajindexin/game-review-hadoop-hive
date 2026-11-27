# -*- coding: utf-8 -*-
"""
把本地标注工具导出的 gold_labels_*.csv 合并回 annotation_tasks.csv

本地标注工具导出列：review_id, game_name, review_text, gold_aspects_json,
                gold_items_json, annotator, annotate_note
按 review_id 匹配，把 gold_* 两列写回标注集，供 prepare_rbt3_data_v2.py --source gold 使用。

用法：
  python merge_gold.py --gold ../data/annotation/gold_labels_2026-11-23.csv
"""
from __future__ import annotations
import os
import csv
import shutil
import argparse
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
TASKS = os.path.join(ANN, "annotation_tasks.csv")


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True, help="本地标注工具导出的 gold_labels_*.csv 路径")
    return ap.parse_args()


def main():
    args = parse_args()
    if not os.path.exists(args.gold):
        raise SystemExit(f"找不到标注结果文件: {args.gold}")

    gold = {}
    with open(args.gold, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            gold[r["review_id"]] = r
    print(f"标注结果: {len(gold)} 条")

    with open(TASKS, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        tasks = list(reader)

    merged = 0
    for t in tasks:
        if t["review_id"] in gold:
            g = gold[t["review_id"]]
            t["gold_aspects_json"] = g.get("gold_aspects_json", "")
            t["gold_items_json"] = g.get("gold_items_json", "")
            t["annotator"] = g.get("annotator", "human")
            t["annotate_note"] = g.get("annotate_note", "")
            merged += 1

    # 备份后写回
    backup = TASKS + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(TASKS, backup)
    with open(TASKS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(tasks)

    n_layer1 = sum(1 for t in tasks if t.get("gold_aspects_json"))
    n_layer2 = sum(1 for t in tasks if t.get("gold_items_json"))
    print(f"合并: {merged} 条 -> {TASKS}")
    print(f"  第一层已标注: {n_layer1} 条, 第二层已标注: {n_layer2} 条")
    print(f"原文件已备份: {backup}")
    print("\n下一步: python prepare_rbt3_data_v2.py --source gold")


if __name__ == "__main__":
    main()
