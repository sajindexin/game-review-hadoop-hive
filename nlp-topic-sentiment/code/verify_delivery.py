# -*- coding: utf-8 -*-
"""
交付物完整性校验：检查两层标签体系所有产物的存在性、行数、关键字段、跨游戏约束。
"""
from __future__ import annotations
import os
import sys
import json
import csv

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

def check(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond

def main():
    ok = True
    print("=== 1. 两层标签体系 ===")
    from item_lexicon import ITEMS, APPID_ITEMS, ITEM2ASPECT, APPID_GAME
    ok &= check(len(ITEMS) == 75, f"条目数=75 (实际 {len(ITEMS)})")
    ok &= check(len(APPID_ITEMS) == 12, f"游戏数=12 (实际 {len(APPID_ITEMS)})")
    ok &= check(len(ITEM2ASPECT) == 75, f"条目->主题映射=75 (实际 {len(ITEM2ASPECT)})")
    ok &= check(all(v.startswith(("content","level_design","multiplayer","graphics",
                                   "performance","story","controls","monetization",
                                   "cheating","audio","community")) for v in ITEM2ASPECT.values()),
                 "所有条目归属合法通用主题")

    print("\n=== 2. taxonomy 文件 ===")
    tax = os.path.join(ROOT, "data", "taxonomy")
    for f in ("items_enriched.json", "patches.json", "appid_game_map.json"):
        p = os.path.join(tax, f)
        ok &= check(os.path.exists(p) and os.path.getsize(p) > 100, f"{f} 存在且非空")

    print("\n=== 3. 规则引擎两层打标 ===")
    from rule_labeler import label_review
    r1 = label_review("新关卡好玩，但联机老掉线")
    ok &= check(len(r1["aspects"]) == 2 and
                {a["aspect"] for a in r1["aspects"]} == {"content", "multiplayer"},
                "第一层：一句多主题情感相反")
    r2 = label_review("姜岛探索很好玩，但是八人联机老是掉线", appid=413150)
    ok &= check(len(r2["items"]) == 2, f"第二层：两条目命中 (实际 {len(r2['items'])})")
    r3 = label_review("姜岛探索很好玩", appid=730)
    ok &= check(len(r3["items"]) == 0, "跨游戏约束：CS2下姜岛不命中")

    print("\n=== 4. 结果表 ===")
    res = os.path.join(ROOT, "data", "result")
    specs = [("topic_sentiment_detail.csv", 500000), ("topic_sentiment_review_summary.csv", 1000000),
             ("topic_distribution.csv", 20), ("item_sentiment_detail.csv", 100),
             ("item_distribution.csv", 10), ("item_patch_summary.csv", 5)]
    for f, min_rows in specs:
        p = os.path.join(res, f)
        exists = os.path.exists(p)
        if exists:
            with open(p, encoding="utf-8-sig") as fh:
                n = sum(1 for _ in fh) - 1
            ok &= check(n >= min_rows, f"{f}: {n} 行")
        else:
            ok &= check(False, f"{f} 不存在")

    print("\n=== 5. 标注集与文档 ===")
    ann = os.path.join(ROOT, "data", "annotation")
    tasks_p = os.path.join(ann, "annotation_tasks.csv")
    with open(tasks_p, encoding="utf-8-sig") as f:
        cols = next(csv.reader(f))
    ok &= check("gold_aspects_json" in cols and "gold_items_json" in cols,
                "标注集含两层 gold 列")
    for rel in ("data/result/topic_sentiment_detail.csv.gz",
                "data/result/topic_sentiment_review_summary.csv.gz"):
        ok &= check(os.path.exists(os.path.join(ROOT, *rel.split("/"))),
                    f"结果压缩包 {rel} 存在")
    for f in ("两层标签体系说明.md", "标注规范.md", "结果表字段说明.md", "效果对比方案.md"):
        ok &= check(os.path.exists(os.path.join(ROOT, "docs", f)), f"文档 {f} 存在")
    sql = open(os.path.join(ROOT, "sql", "hive_metrics.sql"), encoding="utf-8").read()
    ok &= check("item_sentiment_detail" in sql and "item_dict" in sql,
                "Hive SQL 含第二层建表与指标")

    print("\n=== 6. 脚本可导入 ===")
    for m in ("item_matcher", "build_item_results", "build_item_taxonomy",
              "prepare_rbt3_data_v2", "train_rbt3_v2", "infer_rbt3_v2", "merge_gold"):
        try:
            __import__(m)
            ok &= check(True, f"{m}.py 导入成功")
        except Exception as e:
            ok &= check(False, f"{m}.py 导入失败: {e}")

    print("\n" + "=" * 50)
    print("总体:", "全部通过 ✓" if ok else "存在失败项 ✗")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
