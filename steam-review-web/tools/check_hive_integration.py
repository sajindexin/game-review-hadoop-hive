"""只读联调检查：将 Hive 的代表性结果与现有 Web 数据包比对。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from hive_service import get_hive_client

DATA = Path(__file__).resolve().parents[1] / "data"
PATCH_ID = "sdv_20201221_15"


def bundle(path):
    raw = (DATA / path).read_text(encoding="utf-8")
    return json.loads(raw.split("=", 1)[1].strip().rstrip(";"))


def check(label, actual, expected):
    if actual != expected:
        raise AssertionError(f"{label}: actual={actual!r}, expected={expected!r}")
    print(f"PASS {label}", flush=True)


def percent(value):
    return round(value * 100, 2) if value is not None else None


def check_patch(hive):
    expected = bundle(f"patches/{PATCH_ID}.js")
    where = "WHERE patch_id=%(patch)s AND language_group=%(lang)s"
    params = {"patch": PATCH_ID, "lang": "all"}
    compare = hive.query(
        """SELECT before_reviews AS before_reviews, release_reviews AS release_reviews,
           after_reviews AS after_reviews, before_rate AS before_rate,
           release_rate AS release_rate, after_rate AS after_rate,
           change_percentage_points AS delta_pp
           FROM steam_dw.ads_patch_compare """ + where,
        params,
    )
    check("patch comparison row", len(compare), 1)
    metric = compare[0]
    stages = hive.query(
        """SELECT phase AS phase, reviews AS reviews, recommended AS recommended,
           recommendation_rate AS rate FROM steam_dw.ads_patch_stage """ + where,
        params,
    )
    check("patch stage row count", len(stages), len(expected["stats"]["all"]))
    actual = {row["phase"]: {"reviews": row["reviews"], "recommended": row["recommended"],
                             "rate": percent(row["rate"])} for row in stages}
    check("patch stage matches Web", actual, expected["stats"]["all"])
    for phase in ("before", "release", "after"):
        check(f"{phase} comparison count", metric[f"{phase}_reviews"], actual[phase]["reviews"])
        check(f"{phase} comparison rate", percent(metric[f"{phase}_rate"]), actual[phase]["rate"])
    check("patch window reviews", sum(row["reviews"] for row in actual.values()), expected["window_reviews_total"])
    check("Web display delta", round(actual["after"]["rate"] - actual["before"]["rate"], 2), expected["delta_pp"])
    print(f"INFO Hive raw delta={metric['delta_pp']:.8f} pp, Web display={expected['delta_pp']:.2f} pp", flush=True)

    trend = hive.query(
        """SELECT utc_date AS `date`, day_offset AS `offset`, reviews AS reviews,
           recommended AS recommended, recommendation_rate AS rate FROM steam_dw.ads_patch_daily """
        + where + " ORDER BY day_offset",
        params,
    )
    for row in trend:
        row["rate"] = percent(row["rate"])
    check("daily trend matches Web", trend, expected["trend"])
    check("daily and stage review totals", sum(row["reviews"] for row in trend), expected["window_reviews_total"])


def check_topics(hive):
    topics = bundle("nlp/topics.js")
    for lang, table in (("zh", "nlp_topic.topic_distribution"), ("en", "nlp_topic.topic_distribution_en")):
        rows = hive.query(f"SELECT aspect AS aspect, sentiment AS sentiment, `count` AS n FROM {table}")
        actual = {(row["aspect"], row["sentiment"]): row["n"] for row in rows}
        check(f"{lang} unique topic/sentiment rows", len(actual), len(rows))
        expected = {(aspect, sentiment): count for aspect, totals in topics["global"][lang].items()
                    for sentiment, count in totals.items() if count}
        check(f"{lang} topic distribution matches Web", {key: n for key, n in actual.items() if n}, expected)
        print(f"INFO {lang} topic hits={sum(actual.values()):,}", flush=True)


def check_quotes(hive):
    expected = bundle("verified.js")
    rows = hive.query("SELECT review_id AS review_id, patch_id AS patch_id, item_id AS item_id "
                      "FROM hadoop_delivery.verified_quotes")

    def keys(records):
        return Counter((row["review_id"], row["patch_id"], row["item_id"]) for row in records)

    check("verified quote count", len(rows), len(expected))
    check("verified quote review/patch/item identities", keys(rows) == keys(expected), True)


def main():
    hive = get_hive_client()
    check("connection", hive.query("SELECT 1 AS ok"), [{"ok": 1}])
    sample = "Stardew Valley's review"
    check("bound text", hive.query("SELECT %s AS bound_text", (sample,)), [{"bound_text": sample}])
    check_patch(hive)
    check_topics(hive)
    check_quotes(hive)
    for table, expected in (("game_review_upload.gr_agent_corpus_62007", 62007),
                            ("game_review_upload.gr_manifest", 23)):
        result = hive.query(f"SELECT COUNT(*) AS n FROM {table}")
        check(f"{table} row count", result, [{"n": expected}])
    counts = hive.query("SELECT COUNT(*) AS n, COUNT(DISTINCT appid) AS games FROM game_reviews.reviews_v1")
    check("cleaned review count and game coverage", counts, [{"n": 4884580, "games": 12}])


if __name__ == "__main__":
    main()
