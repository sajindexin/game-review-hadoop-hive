# -*- coding: utf-8 -*-
"""
把离线数据（4.88M 条清洗评论 parquet + 组内 Hive 统计 CSV + 版本/改动条目 jsonl）
导出成前端可直接 <script> 加载的数据文件。

输出：
  data/index.js              游戏 / 版本清单
  data/patches/<patch_id>.js 单版本详情（趋势、阶段统计、改动条目 + 引文、评论样本）

用法（仓库根目录）：
  python steam-review-web/tools/export_web_data.py \
      --parquet team_package_v1/游戏评论交付/releases/v1/data/cleaned_reviews.parquet \
      --analysis repo/game-update-feedback-analysis \
      --out steam-review-web/data
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import defaultdict
from pathlib import Path

random.seed(20261124)


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def clip(text: str, limit: int = 680) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def is_art(text: str) -> bool:
    """过滤 Braille / 制表符拼的 ASCII 艺术刷屏评论，这类内容对演示没有信息量。"""
    if not text:
        return True
    art = sum(1 for ch in text if 0x2800 <= ord(ch) <= 0x28FF or 0x2500 <= ord(ch) <= 0x257F)
    letters = sum(1 for ch in text if ch.isalnum())
    return art / max(1, len(text)) > 0.02 or letters < 8


def build_alias_index(items: list[dict]) -> list[tuple[str, list[str], re.Pattern]]:
    """(item_id, 关键词列表, 正则) —— 用于给评论打「命中改动」标签。"""
    out = []
    for it in items:
        words = [w for w in ([it["title"]] + it.get("aliases_zh", []) + it.get("aliases_en", [])) if w]
        if not words:
            continue
        pat = re.compile("|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)), re.I)
        out.append((it["item_id"], words, pat))
    return out


def sql_like_clause(words: list[str]) -> str:
    conds = []
    for w in words:
        safe = w.lower().replace("'", "''")
        conds.append(f"lower(review_text) LIKE '%{safe}%'")
    return "(" + " OR ".join(conds) + ")"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--analysis", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    parquet = Path(args.parquet).resolve().as_posix()
    analysis = Path(args.analysis)
    out = Path(args.out)
    (out / "patches").mkdir(parents=True, exist_ok=True)

    patches = load_jsonl(analysis / "黄静雯_全组版本与改动" / "patches" / "patches.jsonl")
    items = load_jsonl(analysis / "黄静雯_全组版本与改动" / "items" / "items.jsonl")
    phase_rows = load_csv(analysis / "黄静雯_全组Hive统计/分阶段统计.csv")
    daily_rows = load_csv(analysis / "黄静雯_全组Hive统计/逐日统计.csv")
    counts_rows = load_csv(Path("team_package_v1/游戏评论交付/releases/v1/reports/game_counts.csv"))

    total_by_app = {str(r["appid"]): int(r["count"]) for r in counts_rows}

    items_by_patch: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        items_by_patch[it["patch_id"]].append(it)

    def rate_of(row: dict) -> float | None:
        raw = (row.get("recommendation_rate") or "").strip()
        return round(float(raw) * 100, 2) if raw else None

    phase_by_patch: dict[tuple[str, str, str], dict] = {}
    for r in phase_rows:
        phase_by_patch[(r["patch_id"], r["language_group"], r["phase"])] = r

    daily_by_patch: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in daily_rows:
        daily_by_patch[(r["patch_id"], r["language_group"])].append(
            {
                "date": r["utc_date"],
                "offset": int(r["day_offset"]),
                "reviews": int(r["reviews"]),
                "recommended": int(r["recommended"]),
                "rate": rate_of(r),
            }
        )
    for v in daily_by_patch.values():
        v.sort(key=lambda x: x["offset"])

    import duckdb  # 延迟导入，只在真正跑导出时需要

    con = duckdb.connect()
    con.execute("PRAGMA threads=4")

    games: dict[str, dict] = {}

    for idx, p in enumerate(patches):
        pid = p["patch_id"]
        appid = str(p["appid"])
        patch_items = items_by_patch.get(pid, [])
        alias_index = build_alias_index(patch_items)

        stats = {}
        for lang in ("all", "chinese", "en"):
            entry = {}
            for phase in ("before", "release", "after"):
                row = phase_by_patch.get((pid, lang, phase))
                if not row:
                    continue
                entry[phase] = {
                    "reviews": int(row["reviews"]),
                    "recommended": int(row["recommended"]),
                    "rate": rate_of(row),
                }
            stats[lang] = entry

        all_stats = stats.get("all", {})
        delta = None
        if "before" in all_stats and "after" in all_stats:
            delta = round(all_stats["after"]["rate"] - all_stats["before"]["rate"], 2)

        # ---- 每个版本只扫一次 parquet，落成临时表，后续查询都在内存里跑 ----
        con.execute("DROP TABLE IF EXISTS win")
        con.execute(
            f"""
            CREATE TEMP TABLE win AS
            SELECT review_id, steamid, language, language_std, voted_up, created_at, votes_up,
                   votes_funny, playtime_hours, review_text
            FROM read_parquet('{parquet}')
            WHERE appid = '{appid}'
              AND created_date BETWEEN '{p['window_start']}' AND '{p['window_end']}'
              AND review_len BETWEEN 40 AND 1400
            """
        )

        # ---- 评论样本：高赞 200 + 随机 700 ----
        rows = con.execute(
            """
            WITH base AS (SELECT * FROM win),
                 top AS (SELECT * FROM base ORDER BY votes_up DESC NULLS LAST LIMIT 200),
                 rnd AS (SELECT * FROM base ORDER BY hash(review_id) LIMIT 700)
            SELECT * FROM top UNION ALL SELECT * FROM rnd
            """
        ).fetchall()
        cols = [d[0] for d in con.description]
        seen: set[str] = set()
        candidates: list[dict] = []
        for r in rows:
            rec = dict(zip(cols, r))
            if rec["review_id"] in seen:
                continue
            seen.add(rec["review_id"])
            candidates.append(rec)

        clean = [c for c in candidates if not is_art(c["review_text"])]

        for c in clean:
            text = c["review_text"] or ""
            c["_hits"] = [iid for iid, _words, pat in alias_index if pat.search(text)]
            c["_phase"] = (
                "before" if c["created_at"][:10] < p["release_date"]
                else "release" if c["created_at"][:10] == p["release_date"]
                else "after"
            )

        def pick(group: list[dict], n: int) -> list[dict]:
            matched = [c for c in group if c["_hits"]]
            rest = [c for c in group if not c["_hits"]]
            random.shuffle(matched)
            random.shuffle(rest)
            matched.sort(key=lambda c: -(c["votes_up"] or 0))
            return (matched[:n] + rest)[:n] if matched else rest[:n]

        chosen_ids: set[str] = set()
        comments: list[dict] = []

        def add(c: dict) -> None:
            if c["review_id"] in chosen_ids:
                return
            chosen_ids.add(c["review_id"])
            comments.append(
                {
                    "review_id": c["review_id"],
                    "steamid": c["steamid"] or "",
                    "phase": c["_phase"],
                    "language": c["language_std"] or c["language"] or "",
                    "voted_up": bool(c["voted_up"]),
                    "created_at": c["created_at"],
                    "votes_up": int(c["votes_up"] or 0),
                    "votes_funny": int(c["votes_funny"] or 0),
                    "playtime_hours": round(float(c["playtime_hours"] or 0), 1),
                    "text": clip(c["review_text"]),
                    "items": c["_hits"],
                }
            )

        # 先保证中文样本（面向中文读者，且中文口径往往和总体差异最大）
        zh_group = [c for c in clean if (c["language_std"] or "") in ("zh-Hans", "zh-Hant")]
        for c in pick([c for c in zh_group if c["voted_up"]], 4) + pick(
            [c for c in zh_group if not c["voted_up"]], 3
        ):
            add(c)

        quota = {"before": 9, "release": 6, "after": 15}
        for phase, n in quota.items():
            group = [c for c in clean if c["_phase"] == phase and c["review_id"] not in chosen_ids]
            have = sum(1 for c in comments if c["phase"] == phase)
            need = max(0, n - have)
            if not need:
                continue
            for c in pick([c for c in group if c["voted_up"]], max(1, need * 2 // 3)) + pick(
                [c for c in group if not c["voted_up"]], max(1, need // 3)
            ):
                add(c)
        comments.sort(key=lambda c: (c["phase"] != "release", -c["votes_up"], c["created_at"]))

        # ---- 改动条目：窗口内提及数 + 引文证据 ----
        items_out = []
        for it in patch_items:
            _iid, words, _pat = next(
                (x for x in alias_index if x[0] == it["item_id"]), (it["item_id"], [it["title"]], None)
            )
            where = sql_like_clause(words)
            mentions = con.execute(f"SELECT count(*) FROM win WHERE {where}").fetchone()[0]
            ev_rows = con.execute(
                f"""
                SELECT review_id, steamid, language_std, language, voted_up, created_at,
                       votes_up, playtime_hours, review_text
                FROM win WHERE {where}
                ORDER BY votes_up DESC NULLS LAST
                LIMIT 200
                """
            ).fetchall()

            # 引文排序：关键词命中次数优先，其次点赞，避免高赞长评挤掉针对性引文
            scored = []
            lowered = [w.lower() for w in words]
            for r in ev_rows:
                text = r[8] or ""
                if is_art(text):
                    continue
                low = text.lower()
                occ = sum(low.count(w) for w in lowered)
                scored.append((occ, int(r[6] or 0), r))
            scored.sort(key=lambda x: (-x[0], -x[1]))

            evidence = [
                {
                    "review_id": r[0],
                    "steamid": r[1] or "",
                    "language": r[2] or r[3] or "",
                    "voted_up": bool(r[4]),
                    "created_at": r[5],
                    "votes_up": int(r[6] or 0),
                    "playtime_hours": round(float(r[7] or 0), 1),
                    "hits": occ,
                    "text": clip(r[8], 300),
                }
                for occ, _v, r in scored[:4]
            ]
            items_out.append(
                {
                    "item_id": it["item_id"],
                    "title": it["title"],
                    "title_en": it.get("title_en"),
                    "description": it.get("description"),
                    "section": it.get("section"),
                    "aliases": (it.get("aliases_zh", []) + it.get("aliases_en", []))[:6],
                    "mentions": int(mentions),
                    "evidence": evidence,
                }
            )
        items_out.sort(key=lambda x: (-x["mentions"], x["item_id"]))

        window_total = con.execute("SELECT count(*) FROM win").fetchone()[0]

        lang_cmp = []
        for lang in ("all", "chinese", "en", "ru", "pt-BR", "ja", "ko", "de", "es", "fr"):
            s = stats.get(lang)
            if not s or "before" not in s or "after" not in s:
                continue
            lang_cmp.append(
                {
                    "lang": lang,
                    "before_rate": s["before"]["rate"],
                    "after_rate": s["after"]["rate"],
                    "before_reviews": s["before"]["reviews"],
                    "after_reviews": s["after"]["reviews"],
                    "delta": round(s["after"]["rate"] - s["before"]["rate"], 2),
                }
            )

        payload = {
            "patch": {
                "patch_id": pid,
                "appid": appid,
                "game": p["game"],
                "patch_name": p["patch_name"],
                "title_zh": p.get("title_zh"),
                "release_date": p["release_date"],
                "window_start": p["window_start"],
                "window_end": p["window_end"],
                "platform": p.get("platform"),
                "source_url": p.get("source_url"),
                "changelog_url": p.get("changelog_url"),
                "selection_note": p.get("selection_note"),
                "items": items_out,
            },
            "stats": stats,
            "delta_pp": delta,
            "trend": daily_by_patch.get((pid, "all"), []),
            "trend_lang": {
                "chinese": daily_by_patch.get((pid, "chinese"), []),
                "en": daily_by_patch.get((pid, "en"), []),
            },
            "language_compare": lang_cmp,
            "comments": comments,
            # Hive 口径：三阶段有效评论合计；pool_reviews：其中正文长度 40–1400 的可分析样本
            "window_reviews_total": sum(v["reviews"] for v in all_stats.values() if v),
            "pool_reviews": int(window_total),
        }

        with (out / "patches" / f"{pid}.js").open("w", encoding="utf-8") as fh:
            fh.write("window.SRW_PATCH = ")
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write(";\n")

        g = games.setdefault(
            appid,
            {
                "appid": appid,
                "name": p["game"],
                "reviews_total": total_by_app.get(appid, 0),
                "patches": [],
            },
        )
        g["patches"].append(
            {
                "patch_id": pid,
                "patch_name": p["patch_name"],
                "title_zh": p.get("title_zh"),
                "release_date": p["release_date"],
                "before_rate": all_stats.get("before", {}).get("rate"),
                "after_rate": all_stats.get("after", {}).get("rate"),
                "delta_pp": delta,
                "window_reviews": sum(v["reviews"] for v in all_stats.values() if v),
                "item_count": len(items_out),
                "top_item": items_out[0]["title"] if items_out else None,
            }
        )
        print(f"[{idx + 1}/{len(patches)}] {pid} 样本 {len(comments)} / 窗口 {window_total} / 条目 {len(items_out)}")

    for g in games.values():
        g["patches"].sort(key=lambda x: x["release_date"])
        deltas = [x["delta_pp"] for x in g["patches"] if x["delta_pp"] is not None]
        g["avg_delta"] = round(sum(deltas) / len(deltas), 2) if deltas else None
        g["latest"] = g["patches"][-1]["patch_id"] if g["patches"] else None
        g["first"] = g["patches"][0]["patch_id"] if g["patches"] else None

    index = {
        "generated_at": "2026-11-24",
        "source": {"patches": len(patches), "games": len(games)},
        "games": sorted(games.values(), key=lambda x: -x["reviews_total"]),
    }
    with (out / "index.js").open("w", encoding="utf-8") as fh:
        fh.write("window.SRW_INDEX = ")
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    size = sum(f.stat().st_size for f in out.rglob("*.js"))
    print(f"\n完成：{len(games)} 款游戏 / {len(patches)} 个版本 / 合计 {size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
