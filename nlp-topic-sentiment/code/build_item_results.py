# -*- coding: utf-8 -*-
"""
批量具体条目打标：读取清洗后 parquet，对中文评论做两层规则打标，
输出「具体更新条目」层的结果表（明细 + 分布 + 版本窗口对齐）。

与 build_topic_results.py 并行运行，共用 rule_labeler.label_review(text, appid)。

输出：
  data/result/item_sentiment_detail.csv   条目情感明细（一评论一条目一行）
  data/result/item_distribution.csv       条目 x 情感 分布
  data/result/item_patch_summary.csv      条目 x 版本窗口 统计（更新前后对比）

用法：
  python build_item_results.py --limit 50000     # 小样本
  python build_item_results.py                    # 全量中文
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
from item_lexicon import ITEMS, ITEM2ASPECT, APPID_GAME

COLS = ["appid", "game_name", "review_id", "language_std", "review_text",
        "created_date", "game_version", "voted_up"]

# 版本窗口：patch_id -> {appid, release_date, window_start, window_end}
PATCHES = {}
_tax = os.path.normpath(os.path.join(HERE, "..", "data", "taxonomy", "patches.json"))
if os.path.exists(_tax):
    with open(_tax, encoding="utf-8") as f:
        for p in json.load(f):
            PATCHES[p["patch_id"]] = p


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=DEFAULT_PARQUET)
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条中文评论，0=全部")
    ap.add_argument("--chunk-rows", type=int, default=200000)
    return ap.parse_args()


def flush(rows, path, first):
    tbl = pa.Table.from_pylist(rows)
    opts = pacsv.WriteOptions(include_header=first)
    sink = pa.OSFile(path, "ab" if not first else "wb")
    try:
        pacsv.write_csv(tbl, sink, write_options=opts)
    finally:
        sink.close()


def window_bucket(date_str, patch):
    """判断评论日期相对版本窗口的位置：pre14 / release / post14 / out。"""
    if not date_str or not patch:
        return ""
    d = str(date_str)[:10]
    ws, we, rd = patch.get("window_start", ""), patch.get("window_end", ""), patch.get("release_date", "")
    if not (ws and we and rd):
        return ""
    if ws <= d < rd:
        return "pre_14d"
    if d == rd:
        return "release_day"
    if rd < d <= we:
        return "post_14d"
    return "out_of_window"


def main():
    args = parse_args()
    os.makedirs(OUT, exist_ok=True)
    pf = pq.ParquetFile(args.parquet)

    detail_buf: list[dict] = []
    review_with_item = 0
    n_zh = 0
    n_item_labels = 0
    item_counter: dict[tuple[str, str], int] = {}  # (item_id, sentiment) -> count
    # 版本窗口统计：item_id -> {bucket: count}
    patch_stat: dict[str, dict[str, int]] = {}
    t0 = time.time()

    detail_path = os.path.join(OUT, "item_sentiment_detail.csv")
    first = True

    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(rg, columns=COLS).to_pydict()
        m = len(t["appid"])
        for j in range(m):
            if args.limit and n_zh >= args.limit:
                break
            if t["language_std"][j] not in ("zh-Hans", "zh-Hant"):
                continue
            n_zh += 1
            text = t["review_text"][j] or ""
            if not text.strip():
                continue
            appid = t["appid"][j]
            res = label_review(text, appid=appid)
            if res["items"]:
                review_with_item += 1
            # 找该游戏在窗口内的版本（同 appid，评论日期落在窗口内）
            date = t["created_date"][j]
            hit_patch = None
            for pid, p in PATCHES.items():
                if str(p.get("appid")) != str(appid):
                    continue
                ws, we = p.get("window_start", ""), p.get("window_end", "")
                if ws and we and ws <= str(date)[:10] <= we:
                    hit_patch = p
                    break
            bucket = window_bucket(date, hit_patch)

            for it in res["items"]:
                iid = it["item_id"]
                n_item_labels += 1
                detail_buf.append({
                    "review_id": t["review_id"][j],
                    "appid": appid,
                    "game_name": t["game_name"][j] or APPID_GAME.get(str(appid), ""),
                    "created_date": date,
                    "game_version": t["game_version"][j] or "",
                    "patch_id": hit_patch["patch_id"] if hit_patch else "",
                    "window_bucket": bucket,
                    "item_id": iid,
                    "item_title": it["title"],
                    "aspect": ITEM2ASPECT.get(iid, ""),
                    "sentiment": it["sentiment"],
                    "confidence": it["confidence"],
                    "matched_term": it["matched_term"],
                    "evidence": it["evidence"],
                })
                key = (iid, it["sentiment"])
                item_counter[key] = item_counter.get(key, 0) + 1
                if bucket:
                    patch_stat.setdefault(iid, {})
                    patch_stat[iid][bucket] = patch_stat[iid].get(bucket, 0) + 1

        if len(detail_buf) >= args.chunk_rows:
            flush(detail_buf, detail_path, first)
            first = False
            detail_buf = []
            print(f"  ...已扫描 {rg+1}/{pf.metadata.num_row_groups} 组，中文 {n_zh}，"
                  f"条目标注 {n_item_labels}，耗时 {time.time()-t0:.0f}s", flush=True)

        if args.limit and n_zh >= args.limit:
            break

    if detail_buf:
        flush(detail_buf, detail_path, first)

    # 分布表
    dist_rows = []
    for iid, meta in ITEMS.items():
        for sent in ("positive", "negative", "neutral"):
            n = item_counter.get((iid, sent), 0)
            if n:
                dist_rows.append({
                    "item_id": iid, "item_title": meta.get("title", ""),
                    "appid": meta.get("appid", ""),
                    "game": APPID_GAME.get(str(meta.get("appid")), ""),
                    "aspect": ITEM2ASPECT.get(iid, ""),
                    "sentiment": sent, "count": n,
                })
    dist_rows.sort(key=lambda x: -x["count"])
    dist_path = os.path.join(OUT, "item_distribution.csv")
    pacsv.write_csv(pa.Table.from_pylist(dist_rows), dist_path)

    # 版本窗口统计表
    patch_rows = []
    for iid, buckets in patch_stat.items():
        row = {"item_id": iid, "item_title": ITEMS[iid].get("title", ""), **buckets}
        patch_rows.append(row)
    patch_out = os.path.join(OUT, "item_patch_summary.csv")
    pacsv.write_csv(pa.Table.from_pylist(patch_rows), patch_out)

    print("\n=== 完成 ===")
    print(f"中文评论扫描: {n_zh}, 命中条目的评论: {review_with_item} "
          f"({review_with_item/max(n_zh,1)*100:.1f}%)")
    print(f"条目-情感标注条数: {n_item_labels}")
    print(f"耗时: {time.time()-t0:.1f}s")
    print("\n条目命中 TOP 15:")
    for r in dist_rows[:15]:
        print(f"  {r['item_title']:<18} {r['sentiment']:<9} {r['count']}")
    print("\n输出文件:")
    for p in (detail_path, dist_path, patch_out):
        print(f"  {p}  ({os.path.getsize(p)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
