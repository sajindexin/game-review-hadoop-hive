from __future__ import annotations

import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from src.router import Router
from tools.topic_stats import FILES, build_topic_stats


class TopicAggregationTest(unittest.TestCase):
    def test_windows_language_and_overlapping_versions(self):
        patches = [
            {"patch_id": "a", "appid": 730, "release_date": "2026-01-15",
             "window_start": "2026-01-01", "window_end": "2026-01-29"},
            {"patch_id": "b", "appid": 730, "release_date": "2026-01-29",
             "window_start": "2026-01-15", "window_end": "2026-02-12"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for lang, days in {
                "chinese": ["2025-12-31", "2026-01-01", "2026-01-15", "2026-01-29", "2026-01-30"],
                "en": ["2026-01-15"],
            }.items():
                with gzip.open(root / FILES[lang], "wt", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["appid", "created_date", "aspect", "name_zh", "sentiment"])
                    writer.writeheader()
                    for day in days:
                        writer.writerow({"appid": "730", "created_date": day, "aspect": "performance",
                                         "name_zh": "性能", "sentiment": "negative"})
                    writer.writerow({"appid": "440", "created_date": "2026-01-15",
                                     "aspect": "performance", "name_zh": "性能", "sentiment": "negative"})
            stats = build_topic_stats(patches, root)
        counts = {(r["patch_id"], r["language"], r["phase"]): r["count"] for r in stats["rows"]}
        self.assertEqual(counts, {
            ("a", "chinese", "before"): 1, ("a", "chinese", "release"): 1,
            ("a", "chinese", "after"): 1, ("a", "en", "release"): 1,
            ("b", "chinese", "before"): 1, ("b", "chinese", "release"): 1,
            ("b", "chinese", "after"): 1, ("b", "en", "before"): 1,
        })


class TopicRoutingTest(unittest.TestCase):
    def test_topic_intents(self):
        router = Router()
        for question, intent, phases, lang in [
            ("玩家主要在讨论什么？", "topic", [], None),
            ("英文玩家更新后主要讨论什么？", "topic", ["after"], "en"),
            ("中英文玩家主要讨论什么主题？", "topic", [], "all"),
            ("更新后哪些方面的负面反馈增加了？", "topic_change", ["before", "after"], None),
        ]:
            with self.subTest(question=question):
                route = router.route(question, None)
                self.assertEqual((route.intent, route.phases, route.language), (intent, phases, lang))
