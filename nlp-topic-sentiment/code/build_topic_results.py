# -*- coding: utf-8 -*-
"""
批量主题情感打标：读取清洗后的 parquet，对评论做规则弱标注，
输出主题情感结果表（明细 + 评论汇总 + 主题分布）。

用法：
  python build_topic_results.py --limit 20000        # 小样本试跑
  python build_topic_results.py                       # 全量中文评论
  python build_topic_results.py --zh-only            # 仅简体（默认简体+繁体）
  python build_topic_results.py --lang en            # 全量英文评论（输出 *_en.csv）
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "result"))
DEFAULT_PARQUET = r"D:\QQFiles\team_package_v1\team_package_v1\游戏评论交付\releases\v1\data\cleaned_reviews.parquet"

sys.path.insert(0, HERE)
from rule_labeler import label_review
from lexicon import ASPECTS

COLS = ["appid", "game_name", "review_id", "language_std", "review_text",
        "created_date", "game_version", "voted_up"]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=DEFAULT_PARQUET)
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条评论，0=全部")
    ap.add_argument("--zh-only", action="store_true", help="只处理简体中文")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh",
                    help="zh=中文（默认，输出原文件名）；en=英文（输出 *_en.csv）")
    ap.add_argument("--chunk-rows", type=int, default=200000)
    return ap.parse_args()


def main():
    args = parse_args()
    os.makedirs(OUT, exist_ok=True)
    pf = pq.ParquetFile(args.parquet)
    lang = args.lang
    if lang == "en":
        lang_set = {"en"}
        suffix = "_en"
    else:
        lang_set = {"zh-Hans"} if args.zh_only else {"zh-Hans", "zh-Hant"}
        suffix = ""

    # 输出缓冲
    detail_rows: list[dict] = []   # 主题情感明细（一条评论一个 aspect 一行）
    review_rows: list[dict] = []   # 评论级汇总
    topic_counter: dict[tuple[str, str], int] = {}  # (aspect, sentiment) -> 计数
    n_zh = 0
    n_tagged = 0
    t0 = time.time()

    def flush_detail_to_disk(rows, path, first):
        tbl = pa.Table.from_pylist(rows)
        # WriteOptions 控制表头：首批写表头，后续追加不写
        opts = pacsv.WriteOptions(include_header=first)
        sink = pa.OSFile(path, "ab" if not first else "wb")
        try:
            pacsv.write_csv(tbl, sink, write_options=opts)
        finally:
            sink.close()

    detail_path = os.path.join(OUT, f"topic_sentiment_detail{suffix}.csv")
    review_path = os.path.join(OUT, f"topic_sentiment_review_summary{suffix}.csv")
    first_detail = True

    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(rg, columns=COLS).to_pydict()
        m = len(t["appid"])
        batch_detail: list[dict] = []
        for j in range(m):
            if args.limit and n_zh >= args.limit:
                break
            if t["language_std"][j] not in lang_set:
                continue
            n_zh += 1
            text = t["review_text"][j] or ""
            if not text.strip():
                continue
            res = label_review(text, lang=lang)
            n_tagged += 1
            review_rows.append({
                "review_id": t["review_id"][j],
                "appid": t["appid"][j],
                "game_name": t["game_name"][j],
                "created_date": t["created_date"][j],
                "game_version": t["game_version"][j] or "",
                "voted_up": t["voted_up"][j],
                "n_aspects": res["n_aspects"],
                "pos_aspects": res["pos_aspects"],
                "neg_aspects": res["neg_aspects"],
                "primary_sentiment": res["primary_sentiment"],
                "aspects_json": json.dumps(res["aspects"], ensure_ascii=False),
            })
            for a in res["aspects"]:
                batch_detail.append({
                    "review_id": t["review_id"][j],
                    "appid": t["appid"][j],
                    "game_name": t["game_name"][j],
                    "created_date": t["created_date"][j],
                    "game_version": t["game_version"][j] or "",
                    "aspect": a["aspect"],
                    "name_zh": a["name_zh"],
                    "sentiment": a["sentiment"],
                    "confidence": a["confidence"],
                    "sentiment_word": a["sentiment_word"],
                    "evidence": a["evidence"],
                })
                key = (a["aspect"], a["sentiment"])
                topic_counter[key] = topic_counter.get(key, 0) + 1

        if batch_detail:
            detail_rows.extend(batch_detail)
        # 分批落盘，控制内存
        if len(detail_rows) >= args.chunk_rows:
            flush_detail_to_disk(detail_rows, detail_path, first_detail)
            first_detail = False
            detail_rows = []
            print(f"  ...已扫描 {rg+1}/{pf.metadata.num_row_groups} 组，"
                  f"{'英文' if lang=='en' else '中文'} {n_zh}，"
                  f"打标 {n_tagged}，耗时 {time.time()-t0:.0f}s", flush=True)

        if args.limit and n_zh >= args.limit:
            break

    if detail_rows:
        flush_detail_to_disk(detail_rows, detail_path, first_detail)

    pacsv.write_csv(pa.Table.from_pylist(review_rows), review_path)

    # 主题分布统计表
    dist_rows = []
    for aid in ASPECTS:
        for sent in ("positive", "negative", "neutral"):
            dist_rows.append({
                "aspect": aid, "name_zh": ASPECTS[aid]["name_zh"],
                "sentiment": sent, "count": topic_counter.get((aid, sent), 0),
            })
    dist_path = os.path.join(OUT, f"topic_distribution{suffix}.csv")
    pacsv.write_csv(pa.Table.from_pylist(dist_rows), dist_path)

    # 单机可复现的效果说明
    print("\n=== 完成 ===")
    print(f"{'英文' if lang=='en' else '中文'}评论扫描: {n_zh}, 实际打标: {n_tagged}")
    print(f"主题-情感标注条数: {sum(topic_counter.values())}")
    print(f"耗时: {time.time()-t0:.1f}s")
    print("\n主题分布:")
    for r in dist_rows:
        if r["count"]:
            print(f"  {r['name_zh']:<10} {r['sentiment']:<9} {r['count']}")
    print("\n输出文件:")
    for p in (detail_path, review_path, dist_path):
        print(f"  {p}  ({os.path.getsize(p)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
