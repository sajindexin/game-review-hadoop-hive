# -*- coding: utf-8 -*-
"""
把标注集转换成 RBT3 训练格式（review_text, aspect, sentiment）

输入两种来源：
  1) annotation_tasks.csv 经人工填写 gold_aspects_json 后（推荐）
  2) 直接用规则弱标注结果 topic_sentiment_detail.csv 造银标（冷启动演示用）

gold_aspects_json 格式：
  [{"aspect":"content","sentiment":"positive"},{"aspect":"multiplayer","sentiment":"negative"}]

输出：rbt3_train.csv / rbt3_eval.csv（按 8:2 划分），列名 review_text,aspect,sentiment
"""
from __future__ import annotations
import os
import csv
import json
import random
import argparse
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
ANN_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
RESULT_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "result"))


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["gold", "rule"], default="rule",
                    help="gold=人工标注集, rule=规则弱标注(银标)")
    ap.add_argument("--ratio", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def main():
    args = parse_args()
    random.seed(args.seed)
    triples = []

    if args.source == "gold":
        src = os.path.join(ANN_DIR, "annotation_tasks.csv")
        with open(src, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                gj = (r.get("gold_aspects_json") or "").strip()
                if not gj:
                    continue
                try:
                    aspects = json.loads(gj)
                except Exception:
                    continue
                for a in aspects:
                    triples.append((r["review_text"], a["aspect"], a["sentiment"]))
    else:
        src = os.path.join(RESULT_DIR, "topic_sentiment_detail.csv")
        # 读必要列
        tbl = pacsv.read_csv(src)
        d = tbl.to_pydict()
        n = len(d["review_id"])
        # 用 review_id+aspect 关联回原文：从 review_summary 拿不到 text，
        # 所以这里从原始 parquet 按 review_id 回填（小数据集演示场景下直接在明细里带 text 更好）
        # 简化：读明细时若含 evidence，用 evidence 作为弱监督文本（子句级，足够冷启动）
        for j in range(n):
            text = d.get("evidence", [""] * n)[j] or ""
            aspect = d["aspect"][j]
            sent = d["sentiment"][j]
            if text and sent in ("positive", "negative"):  # 银标去掉 neutral 降低噪声
                triples.append((text, aspect, sent))

    random.shuffle(triples)
    split = int(len(triples) * args.ratio)
    train, ev = triples[:split], triples[split:]

    out_train = os.path.join(ANN_DIR, "rbt3_train.csv")
    out_eval = os.path.join(ANN_DIR, "rbt3_eval.csv")
    for path, rows in [(out_train, train), (out_eval, ev)]:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["review_text", "aspect", "sentiment"])
            w.writerows(rows)

    print(f"来源: {args.source}, 总三元组: {len(triples)}")
    print(f"训练: {len(train)} -> {out_train}")
    print(f"验证: {len(ev)} -> {out_eval}")
    print("提示: gold 模式需先在 annotation_tasks.csv 里填好 gold_aspects_json 列。")


if __name__ == "__main__":
    main()
