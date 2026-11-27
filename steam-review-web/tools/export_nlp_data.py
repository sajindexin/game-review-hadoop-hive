# -*- coding: utf-8 -*-
"""
把 NLP 模块（nlp-topic-sentiment）的结果导出成前端可直接加载的数据包。

输入（来自 feat/nlp-topic-sentiment 分支的 nlp-topic-sentiment/data/result/）：
    topic_sentiment_detail.csv.gz      中文主题情感明细（535,623 行）
    topic_sentiment_detail_en.csv.gz   英文主题情感明细（1,120,567 行）
    item_sentiment_detail.csv          官方改动情感明细（1,593 行，中文为主）
    topic_distribution.csv             11 类主题全局分布

输出：
    data/nlp/topics.js   主题情感：语种 × 阶段 × 版本/游戏 四维切片
    data/nlp/items.js    75 项官方改动的正/负/中性统计

用法（仓库根目录）：
    python steam-review-web/tools/export_nlp_data.py \
        --nlp nlp-topic-sentiment \
        --patches repo/game-update-feedback-analysis/黄静雯_全组版本与改动 \
        --out steam-review-web/data/nlp
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

SENTIMENTS = ("positive", "negative", "neutral")
PHASES = ("before", "release", "after")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_csv(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def phase_of(date: str, release: str, start: str, end: str) -> str | None:
    """把评论日期归到版本窗口内的阶段；窗口外返回 None。"""
    if date < start or date > end:
        return None
    if date < release:
        return "before"
    if date == release:
        return "release"
    return "after"


def blank() -> dict:
    return {s: 0 for s in SENTIMENTS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nlp", required=True, help="nlp-topic-sentiment 目录")
    ap.add_argument("--patches", required=True, help="含 patches/patches.jsonl 与 items/items.jsonl 的目录")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    nlp_dir = Path(args.nlp) / "data" / "result"
    patch_dir = Path(args.patches)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    patches = load_jsonl(patch_dir / "patches" / "patches.jsonl")
    items_all = load_jsonl(patch_dir / "items" / "items.jsonl")

    # appid → 该游戏的版本列表（已按窗口起点排序，来源已验证无重叠）
    patches_by_app: dict[str, list[dict]] = defaultdict(list)
    for p in patches:
        patches_by_app[str(p["appid"])].append(p)
    for v in patches_by_app.values():
        v.sort(key=lambda x: x["window_start"])

    patch_meta = {p["patch_id"]: p for p in patches}
    game_of_app = {str(p["appid"]): p["game"] for p in patches}

    # 11 类主题的中文名（取自官方 topic_distribution.csv）
    dist_rows = read_csv(nlp_dir / "topic_distribution.csv")
    aspect_name: dict[str, str] = {}
    for r in dist_rows:
        aspect_name[r["aspect"]] = r["name_zh"]
    aspects = [{"id": a, "name_zh": n} for a, n in aspect_name.items()]
    aspects.sort(key=lambda x: x["id"])

    sources = {}

    # ---- 主题维度：语种 × 版本 × 阶段 × 主题 × 情感 ----
    # byPatch[patch][lang][phase][aspect] = {pos, neg, neu}
    by_patch: dict[str, dict[str, dict[str, dict[str, dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(blank)))
    )
    # byGame[appid][lang][aspect] = {...}（全时间线，不受版本窗口限制）
    by_game: dict[str, dict[str, dict[str, dict]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(blank))
    )
    # 全局（两份语料合计前的分语种全量）
    global_dist: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(blank))

    for fname, lang in (("topic_sentiment_detail.csv.gz", "zh"), ("topic_sentiment_detail_en.csv.gz", "en")):
        rows = read_csv(nlp_dir / fname)
        sources[lang] = len(rows)
        hit_window = 0

        for r in rows:
            appid = str(r["appid"])
            aspect = r["aspect"]
            senti = r["sentiment"]
            if senti not in SENTIMENTS or aspect not in aspect_name:
                continue

            global_dist[lang][aspect][senti] += 1
            by_game[appid][lang][aspect][senti] += 1

            # 找到该评论所属的版本窗口（同游戏窗口不重叠，命中即唯一）
            date = r["created_date"]
            for p in patches_by_app.get(appid, []):
                ph = phase_of(date, p["release_date"], p["window_start"], p["window_end"])
                if ph:
                    by_patch[p["patch_id"]][lang][ph][aspect][senti] += 1
                    by_patch[p["patch_id"]][lang]["all"][aspect][senti] += 1
                    hit_window += 1
                    break

        print(f"  {lang}: {len(rows):,} 行，落在版本窗口内 {hit_window:,} 行")

    # ---- 官方改动维度：75 项，正/负/中性 + 窗口分布 ----
    item_detail = read_csv(nlp_dir / "item_sentiment_detail.csv")
    item_stats: dict[str, dict] = defaultdict(
        lambda: {"sentiment": blank(), "bucket": defaultdict(int), "total": 0}
    )
    for r in item_detail:
        st = item_stats[r["item_id"]]
        senti = r["sentiment"]
        if senti in SENTIMENTS:
            st["sentiment"][senti] += 1
            st["total"] += 1
        st["bucket"][r["window_bucket"]] += 1

    items_out = []
    for it in items_all:
        st = item_stats.get(it["item_id"])
        meta = patch_meta.get(it["patch_id"], {})
        sentiment = st["sentiment"] if st else blank()
        total = st["total"] if st else 0
        bucket = {k: st["bucket"].get(k, 0) for k in ("pre_14d", "release_day", "post_14d", "out_of_window")} if st else {
            "pre_14d": 0, "release_day": 0, "post_14d": 0, "out_of_window": 0
        }
        items_out.append(
            {
                "item_id": it["item_id"],
                "title": it["title"],
                "title_en": it.get("title_en"),
                "patch_id": it["patch_id"],
                "appid": str(it["appid"]),
                "game": meta.get("game") or game_of_app.get(str(it["appid"]), ""),
                "patch_name": meta.get("patch_name", ""),
                "patch_title_zh": meta.get("title_zh") or meta.get("patch_name", ""),
                "release_date": meta.get("release_date", ""),
                "aspect": it.get("section", ""),
                "sentiment": sentiment,
                "total": total,
                "bucket": bucket,
            }
        )
    items_out.sort(key=lambda x: (-x["total"], x["item_id"]))
    hit_items = sum(1 for x in items_out if x["total"] > 0)
    print(f"  官方改动：{len(items_out)} 项，其中有评论命中 {hit_items} 项")

    # ---- 落盘 ----
    topics_payload = {
        "generated_at": "2026-11-27",
        "aspects": aspects,
        "sentiments": list(SENTIMENTS),
        "phases": list(PHASES),
        "sources": sources,
        "global": global_dist,
        "by_game": by_game,
        "by_patch": by_patch,
        "patch_meta": {
            p["patch_id"]: {
                "appid": str(p["appid"]),
                "game": p["game"],
                "patch_name": p["patch_name"],
                "title_zh": p.get("title_zh"),
                "release_date": p["release_date"],
                "window_start": p["window_start"],
                "window_end": p["window_end"],
            }
            for p in patches
        },
    }
    path = out / "topics.js"
    with path.open("w", encoding="utf-8") as fh:
        fh.write("window.SRW_NLP_TOPICS = ")
        json.dump(topics_payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    items_payload = {
        "generated_at": "2026-11-27",
        "total_items": len(items_out),
        "hit_items": hit_items,
        "note": "条目情感统计以中文评论为主（1,585 / 1,593），英文仅 8 条，故不提供语种细分",
        "items": items_out,
    }
    path2 = out / "items.js"
    with path2.open("w", encoding="utf-8") as fh:
        fh.write("window.SRW_NLP_ITEMS = ")
        json.dump(items_payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    size = (path.stat().st_size + path2.stat().st_size) / 1024
    print(f"\n完成：{out}  合计 {size:.0f} KB")


if __name__ == "__main__":
    main()
