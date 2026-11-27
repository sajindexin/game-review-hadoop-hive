# -*- coding: utf-8 -*-
"""把助手语义理解的金标(GOLD)合并进 annotation_tasks.csv 的 gold_aspects_json 列。"""
import os, csv, json, shutil
from datetime import datetime
from gold_annotations import GOLD

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "annotation_tasks.csv"))


def main():
    backup = TASKS + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(TASKS, backup)

    with open(TASKS, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    n = 0
    for r in rows:
        rid = int(r["review_id"])
        if rid in GOLD:
            g = [{"aspect": a, "sentiment": s} for a, s in GOLD[rid]]
            r["gold_aspects_json"] = json.dumps(g, ensure_ascii=False)
            r["annotator"] = "semantic_gold"
            n += 1

    with open(TASKS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # 统计
    total_aspects = sum(len(v) for v in GOLD.values())
    from collections import Counter
    cc = Counter()
    for v in GOLD.values():
        for a, s in v:
            cc[(a, s)] += 1
    print(f"合并金标 {n} 条, 共 {total_aspects} 个主题-情感标注")
    print(f"备份: {backup}")
    print("分布(TOP10):")
    for (a, s), c in cc.most_common(10):
        print(f"  {a:14s} {s:9s} {c}")


if __name__ == "__main__":
    main()
