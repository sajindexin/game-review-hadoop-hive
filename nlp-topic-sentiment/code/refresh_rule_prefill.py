# -*- coding: utf-8 -*-
"""就地刷新标注集的规则预填列，把旧口径的 'none' 统一为 'neutral'。

只改 rule_aspects_json / rule_items_json / rule_primary 三列，
保留 gold_aspects_json / gold_items_json / 标注者信息等人工结果不变。
（不重跑 build_annotation_set.py，避免抽样变化或丢失已合并金标。）

用法：python refresh_rule_prefill.py [--dry-run]
"""
from __future__ import annotations
import os, csv, json, argparse, shutil
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "annotation_tasks.csv"))


def normalize(obj):
    """递归把情感字段里的 'none' 改成 'neutral'。"""
    if isinstance(obj, dict):
        return {k: ("neutral" if k == "sentiment" and v == "none" else normalize(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(x) for x in obj]
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只统计改动，不写回")
    args = ap.parse_args()

    with open(TASKS, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    n_json = n_primary = 0
    for r in rows:
        for col in ("rule_aspects_json", "rule_items_json"):
            v = (r.get(col) or "").strip()
            if not v or "none" not in v:
                continue
            try:
                obj = json.loads(v)
            except Exception:
                continue
            new = normalize(obj)
            new_v = json.dumps(new, ensure_ascii=False)
            if new_v != v:
                r[col] = new_v
                n_json += 1
        if (r.get("rule_primary") or "").strip() == "none":
            r["rule_primary"] = "neutral"
            n_primary += 1

    print(f"刷新: rule JSON {n_json} 条, rule_primary {n_primary} 条")
    if args.dry_run or (n_json == 0 and n_primary == 0):
        return
    backup = TASKS + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(TASKS, backup)
    with open(TASKS, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"已写回: {TASKS}")
    print(f"原文件备份: {backup}（.bak_* 已被 .gitignore 排除）")


if __name__ == "__main__":
    main()
