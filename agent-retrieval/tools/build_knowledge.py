"""构建知识库：版本目录 + 指标表 + 窗口评测语料 + BM25/向量索引。

用法（在 game-review-agent 目录下）：
    python -m tools.build_knowledge --parquet "D:\\...\\cleaned_reviews.parquet" ^
        --branch "..\\feat\\game-update-feedback-analysis" --out data

可选：--quick 只扫前 6 个 row group，用于快速冒烟。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

import numpy as np
import pyarrow.dataset as ds
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.bm25 import BM25Index  # noqa: E402
from src.dense import load_encoder, save_vectors  # noqa: E402
from src.tokenize import normalize_for_match, tokenize  # noqa: E402

CJK = re.compile(r"[\u4e00-\u9fff]")
MAX_TEXT = 1000
DOCS_PER_PHASE = 900          # before / release / after 各保留的普通样本
ALIAS_DOCS_PER_PATCH = 1200    # 命中官方条目别名的评测优先保留


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


GENERIC_TERMS = {
    "更新", "新增", "玩家", "游戏", "支持", "加入", "调整", "内容", "系统", "模式", "功能",
    "版本", "修复", "优化", "增加", "可以", "使用", "以及", "现在", "已经", "通过", "进行",
    "问题", "之后", "之前", "同时", "部分", "包括", "相关", "官方", "本次", "地图", "武器",
    "任务", "装备", "角色", "事件", "建筑", "物品", "界面", "显示", "时间", "数据", "主题",
}


def compile_aliases(items: list[dict]) -> re.Pattern | None:
    aliases: list[str] = []
    for it in items:
        aliases.extend(it.get("aliases_zh") or [])
        aliases.extend(it.get("aliases_en") or [])
        aliases.extend(it.get("synonyms") or [])
        # 标题里的双字词也作为召回词，过滤掉“地图/武器”这类过于宽泛的词
        for run in re.findall(r"[\u4e00-\u9fff]+", it.get("title", "")):
            for i in range(len(run) - 1):
                bg = run[i : i + 2]
                if bg not in GENERIC_TERMS:
                    aliases.append(bg)
        aliases.extend(w for w in re.findall(r"[A-Za-z]{4,}", it.get("title_en", "")))
    aliases = [a.strip() for a in aliases if a and len(a.strip()) >= 2]
    if not aliases:
        return None
    parts = []
    for a in sorted(set(aliases), key=len, reverse=True):
        esc = re.escape(a)
        if CJK.search(a):
            parts.append(esc)
        else:
            parts.append(rf"\b{esc}\b")
    return re.compile("|".join(parts), re.IGNORECASE)


def _opt_float(value) -> float | None:
    try:
        if value in (None, "", "nan", "NaN"):
            return None
        v = float(value)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def build_catalog(patches: list[dict], items: list[dict], metrics_rows: list[dict],
                  manifest: dict, generated_at: str, source: dict) -> dict:
    by_patch = defaultdict(list)
    for it in items:
        by_patch[it["patch_id"]].append(it)
    patch_rows = []
    for p in patches:
        d = dict(p)
        d["items"] = sorted(by_patch.get(p["patch_id"], []), key=lambda x: x["item_id"])
        d["item_count"] = len(d["items"])
        patch_rows.append(d)
    patch_rows.sort(key=lambda p: (p["release_date"], p["patch_id"]))

    counts = {g["appid"]: g["count"] for g in manifest.get("games", [])}
    games = []
    for appid, name in sorted({(str(p["appid"]), p["game"]) for p in patch_rows}):
        games.append({
            "appid": appid,
            "game_name": name,
            "review_count": counts.get(appid, 0),
            "patch_count": sum(1 for p in patch_rows if str(p["appid"]) == appid),
        })

    keep_groups = {"all", "chinese", "en"}
    metrics = []
    for r in metrics_rows:
        if r["language_group"] not in keep_groups:
            continue
        metrics.append({
            "appid": str(r["appid"]),
            "patch_id": r["patch_id"],
            "language_group": r["language_group"],
            "before_reviews": int(float(r["before_reviews"] or 0)),
            "before_recommended": int(float(r["before_recommended"] or 0)),
            "before_rate": _opt_float(r["before_rate"]),
            "release_reviews": int(float(r["release_reviews"] or 0)),
            "release_recommended": int(float(r["release_recommended"] or 0)),
            "release_rate": _opt_float(r["release_rate"]),
            "after_reviews": int(float(r["after_reviews"] or 0)),
            "after_recommended": int(float(r["after_recommended"] or 0)),
            "after_rate": _opt_float(r["after_rate"]),
            "change_percentage_points": _opt_float(r["change_percentage_points"]),
        })

    return {
        "data_version": manifest.get("data_version", "v1"),
        "generated_at": generated_at,
        "games": games,
        "patches": patch_rows,
        "items": sorted(items, key=lambda x: (x["patch_id"], x["item_id"])),
        "metrics": metrics,
        "source": source,
    }


def scan_corpus(parquet: Path, patches: list[dict], items: list[dict],
                max_batches: int | None = None) -> tuple[list[dict], dict]:
    by_patch_items = defaultdict(list)
    for it in items:
        by_patch_items[it["patch_id"]].append(it)
    alias_re = {p["patch_id"]: compile_aliases(by_patch_items[p["patch_id"]]) for p in patches}

    patch_by_key: dict[tuple[str, int], dict] = {}
    lo = hi = None
    for p in patches:
        start = date.fromisoformat(p["window_start"])
        end = date.fromisoformat(p["window_end"])
        ts0 = int(datetime.combine(start, dtime.min, tzinfo=timezone.utc).timestamp())
        ts1 = int(datetime.combine(end, dtime.max, tzinfo=timezone.utc).timestamp())
        lo = ts0 if lo is None else min(lo, ts0)
        hi = ts1 if hi is None else max(hi, ts1)
        for day in range(p["release_day"] - 14, p["release_day"] + 15):
            patch_by_key[(str(p["appid"]), day)] = p

    cols = ["appid", "review_id", "steamid", "language_std", "voted_up", "created_ts",
            "created_at", "votes_up", "playtime_hours", "review_text"]
    dataset = ds.dataset(str(parquet), format="parquet")
    flt = (ds.field("created_ts") >= lo) & (ds.field("created_ts") <= hi)
    scanner = dataset.scanner(columns=cols, filter=flt, batch_size=32768,
                              fragment_readahead=2, batch_readahead=2)

    rng = random.Random(20261124)
    buckets: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    seen: set[str] = set()
    rows_scanned = 0
    hit_rows = 0
    batch_no = 0
    t0 = time.perf_counter()

    for batch in scanner.to_batches():
        batch_no += 1
        if max_batches and batch_no > max_batches:
            break
        df = batch.to_pandas()
        rows_scanned += len(df)
        if df.empty:
            continue
        df["day"] = (df["created_ts"] // 86400).astype("int64")
        keys = list(zip(df["appid"].astype(str), df["day"].astype(int)))
        patch_ids = [patch_by_key[k]["patch_id"] if k in patch_by_key else None for k in keys]
        df["patch_id"] = patch_ids
        df = df[df["patch_id"].notna()]
        if df.empty:
            continue
        df["text"] = df["review_text"].fillna("").astype(str)
        for patch_id, g in df.groupby("patch_id", sort=False):
            p = next(x for x in patches if x["patch_id"] == patch_id)
            rel_day = p["release_day"]
            days = g["day"].to_numpy()
            phase = np.where(days < rel_day, "before", np.where(days == rel_day, "release", "after"))
            g = g.assign(phase=phase)
            rx = alias_re[patch_id]
            hit = g["text"].str.contains(rx, regex=True) if rx else None
            if hit is not None:
                g = g.assign(alias_hit=hit.fillna(False).to_numpy())
            else:
                g = g.assign(alias_hit=False)
            for (phase_name, is_hit), gg in g.groupby(["phase", "alias_hit"], sort=False):
                key = ("alias" if is_hit else "sample", patch_id, phase_name)
                cap = ALIAS_DOCS_PER_PATCH if is_hit else DOCS_PER_PHASE
                room = cap * 2 - len(buckets[key])
                if room <= 0:
                    continue
                take = gg if len(gg) <= room else gg.sample(n=room, random_state=rng.randrange(1 << 30))
                for row in take.itertuples(index=False):
                    if row.review_id in seen:
                        continue
                    seen.add(row.review_id)
                    text = row.text[:MAX_TEXT]
                    steamid = (row.steamid or "").strip()
                    url = (f"https://steamcommunity.com/profiles/{steamid}/recommended/{row.appid}/"
                           if steamid else f"https://store.steampowered.com/app/{row.appid}/")
                    matched = []
                    for it in by_patch_items[patch_id]:
                        terms = (it.get("aliases_zh") or []) + (it.get("aliases_en") or []) + (it.get("synonyms") or [])
                        for a in terms:
                            if a and a.lower() in text.lower():
                                matched.append(it["item_id"])
                                break
                    buckets[key].append({
                        "appid": str(row.appid), "patch_id": patch_id, "phase": phase_name,
                        "review_id": str(row.review_id), "language_std": str(row.language_std or ""),
                        "voted_up": bool(row.voted_up), "created_ts": int(row.created_ts),
                        "created_at": str(row.created_at or ""), "votes_up": int(row.votes_up or 0),
                        "playtime_hours": float(row.playtime_hours or 0.0),
                        "text": text, "url": url,
                        "matched_items": ",".join(sorted(set(matched))),
                        "alias_hit": bool(is_hit),
                    })
        if batch_no % 10 == 0:
            print(f"  scanned {rows_scanned:,} rows / {batch_no} batches, kept {len(seen):,}"
                  f" ({time.perf_counter() - t0:.0f}s)", flush=True)

    docs: list[dict] = []
    for (kind, patch_id, phase), rows in buckets.items():
        cap = ALIAS_DOCS_PER_PATCH if kind == "alias" else DOCS_PER_PHASE
        if len(rows) > cap:
            rows = rng.sample(rows, cap)
        docs.extend(rows)
    docs.sort(key=lambda d: (d["patch_id"], d["created_ts"], d["review_id"]))
    for i, d in enumerate(docs):
        d["doc_id"] = i
    stats = {
        "rows_scanned": rows_scanned,
        "batches": batch_no,
        "docs": len(docs),
        "by_phase": dict(Counter(d["phase"] for d in docs)),
        "by_language": dict(Counter(d["language_std"] for d in docs).most_common(8)),
        "seconds": round(time.perf_counter() - t0, 1),
    }
    return docs, stats


PHASE_MAP = {"before": "before", "release_day": "release", "after": "after"}


def load_verified(path: Path, parquet: Path) -> list[dict]:
    """上游版本分析模块的已复核引文：补上 Steam 永久链接，统一阶段命名。"""
    rows = load_jsonl(path)
    ids = [str(r["review_id"]) for r in rows]
    dataset = ds.dataset(str(parquet), format="parquet")
    table = dataset.to_table(columns=["review_id", "steamid"],
                             filter=ds.field("review_id").isin(ids))
    steam = {str(r["review_id"]): (r["steamid"] or "") for r in table.to_pylist()}
    out = []
    for r in rows:
        rid = str(r["review_id"])
        appid = str(r["appid"])
        sid = str(steam.get(rid, "") or "")
        url = (f"https://steamcommunity.com/profiles/{sid}/recommended/{appid}/"
               if sid else f"https://store.steampowered.com/app/{appid}/")
        ts = int(r.get("created_ts") or 0)
        out.append({
            "review_id": rid, "appid": appid, "patch_id": r["patch_id"], "item_id": r["item_id"],
            "item_title": r.get("item_title", ""),
            "phase": PHASE_MAP.get(str(r.get("phase")), str(r.get("phase"))),
            "language_std": r.get("language_std", ""), "voted_up": bool(r.get("voted_up")),
            "created_ts": ts, "updated_ts": (int(r["updated_ts"]) if r.get("updated_ts") else None),
            "day_offset": r.get("day_offset"),
            "edited_after_window": bool(r.get("edited_after_window")),
            "before_review_edited_after_release": bool(r.get("before_review_edited_after_release")),
            "created_at": (datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ts else ""),
            "evidence": r.get("evidence", ""), "span_start": r.get("span_start"), "span_end": r.get("span_end"),
            "verification": r.get("verification", ""), "score": r.get("score"),
            "source_url": r.get("source_url", ""), "url": url, "matched_items": [r["item_id"]],
        })
    out.sort(key=lambda x: (x["patch_id"], x["item_id"], x["created_ts"]))
    return out


def discover_upstream(branch: Path, rel: str) -> Path:
    """上游分支的目录名可能变；优先精确路径，否则在 branch 下一层自动找。"""
    direct = branch / rel
    if direct.exists():
        return direct
    hits = sorted(p for p in branch.glob(f"*/{rel}") if p.exists())
    return hits[0] if hits else direct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--branch", help="上游版本分析模块分支目录 game-update-feedback-analysis（可选，给了就优先用它）")
    ap.add_argument("--items", help="items.jsonl 路径（可选）")
    ap.add_argument("--patches", help="patches.jsonl 路径（可选）")
    ap.add_argument("--metrics", help="更新前后对比.csv 路径（可选）")
    ap.add_argument("--verified", help="窗口内已复核可引用关联.jsonl（可选）")
    ap.add_argument("--lexicon", help="条目同义词表 items_extra.jsonl（可选）")
    ap.add_argument("--out", default="data")
    ap.add_argument("--quick", action="store_true", help="只扫前 8 个 batch，冒烟用")
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    if args.branch:
        branch = Path(args.branch)
        default_items = discover_upstream(branch, "items/items.jsonl")
        default_patches = discover_upstream(branch, "patches/patches.jsonl")
        default_metrics = discover_upstream(branch, "更新前后对比.csv")
    else:
        default_items = Path("data_sources/catalog/items.jsonl")
        default_patches = Path("data_sources/catalog/patches.jsonl")
        default_metrics = Path("data_sources/catalog/更新前后对比.csv")
    items_path = Path(args.items) if args.items else default_items
    patches_path = Path(args.patches) if args.patches else default_patches
    metrics_path = Path(args.metrics) if args.metrics else default_metrics
    for p in (items_path, patches_path, metrics_path):
        if not p.exists():
            raise SystemExit(f"缺少输入文件：{p}（可用 --branch 指向上游版本分析模块分支目录）")

    patches = load_jsonl(patches_path)
    items = load_jsonl(items_path)
    lexicon_path = Path(args.lexicon) if args.lexicon else Path("data_sources/lexicon/items_extra.jsonl")
    if lexicon_path.exists():
        extra = {r["item_id"]: r for r in load_jsonl(lexicon_path)}
        for it in items:
            e = extra.get(it["item_id"], {})
            it["synonyms"] = e.get("synonyms", [])
            it["negative"] = e.get("negative", [])
        print(f"lexicon: {sum(len(it.get('synonyms') or []) for it in items)} synonyms "
              f"for {len(extra)} items")
    else:
        for it in items:
            it["synonyms"] = []
            it["negative"] = []
    with metrics_path.open(encoding="utf-8-sig", newline="") as f:
        metrics_rows = list(csv.DictReader(f))

    parquet = Path(args.parquet)
    # manifest.json 在 v1 根目录（parquet 在 v1/data/ 下），不是 releases/ 下
    manifest_path = parquet.parents[1] / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text("utf-8"))
    else:
        print(f"WARN manifest 不存在：{manifest_path}，改用 parquet 现场统计游戏条数", flush=True)
        import pyarrow.compute as pc
        appids = ds.dataset(str(parquet), format="parquet").to_table(columns=["appid"]).column("appid")
        counts = pc.value_counts(appids).to_pylist()
        manifest = {"games": [{"appid": str(x["values"]), "count": int(x["counts"])} for x in counts]}

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = {
        "parquet": str(parquet), "parquet_sha256": sha256_of(parquet) if not args.quick else "quick-mode",
        "patches": str(patches_path),
        "items": str(items_path),
        "metrics": str(metrics_path),
    }
    catalog = build_catalog(patches, items, metrics_rows, manifest, generated_at, source)
    (root / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"catalog: {len(catalog['patches'])} patches, {len(catalog['items'])} items, "
          f"{len(catalog['metrics'])} metric rows")

    print("scanning reviews ...", flush=True)
    docs, stats = scan_corpus(parquet, patches, items, max_batches=8 if args.quick else None)
    print("corpus:", json.dumps(stats, ensure_ascii=False))

    import pandas as pd
    corpus_path = root / "corpus.parquet"
    pd.DataFrame(docs).to_parquet(corpus_path, index=False)

    print("building BM25 ...", flush=True)
    tokens = [tokenize(d["text"][:800]) for d in docs]
    bm25 = BM25Index().build(tokens)
    bm25.save(root / "bm25.pkl")
    print(f"  terms={len(bm25.postings):,} avgdl={bm25.avgdl:.1f}")

    print("building vectors ...", flush=True)
    enc = load_encoder()
    vectors = enc.encode([d["text"][:800] for d in docs], batch_size=128)
    save_vectors(root / "dense.npz", vectors, enc.meta())
    print(f"  encoder={enc.meta()['name']} dim={vectors.shape[1]}")

    verified_meta = {"count": 0}
    verified_path = Path(args.verified) if args.verified else Path("data_sources/verified_quotes.jsonl")
    if verified_path.exists():
        verified = load_verified(verified_path, parquet)
        (root / "verified.json").write_text(json.dumps(verified, ensure_ascii=False), encoding="utf-8")
        vtokens = [tokenize(v["evidence"] + " " + v["item_title"]) for v in verified]
        vbm = BM25Index().build(vtokens)
        vbm.save(root / "verified_bm25.pkl")
        verified_meta = {
            "count": len(verified),
            "patches": len({v["patch_id"] for v in verified}),
            "items": len({(v["patch_id"], v["item_id"]) for v in verified}),
            "with_steam_url": sum(1 for v in verified if "/profiles/" in v["url"]),
            "source": str(verified_path),
        }
        print(f"verified quotes: {len(verified)} records, {verified_meta['patches']} patches, "
              f"{verified_meta['items']} items, {verified_meta['with_steam_url']} with steam url")

    meta = {
        "data_version": catalog["data_version"],
        "built_at": generated_at,
        "docs": len(docs),
        "corpus": stats,
        "encoder": enc.meta(),
        "bm25": {"k1": bm25.k1, "b": bm25.b, "terms": len(bm25.postings), "avgdl": round(bm25.avgdl, 2)},
        "patches": len(catalog["patches"]),
        "items": len(catalog["items"]),
        "metrics_rows": len(catalog["metrics"]),
        "verified": verified_meta,
    }
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print("done:", json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()

