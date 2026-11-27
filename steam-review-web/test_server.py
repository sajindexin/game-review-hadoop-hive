import unittest

from server import adapt_agent_response, answer, local_statistics_intent, verified_item_quotes


class LocalStatisticsTests(unittest.TestCase):
    def test_existing_page_questions(self):
        cases = [
            ("中文玩家和英文玩家的评价差多少？", "language", "5.32 pp"),
            ("这次更新里哪一项改动被提到最多？", "items", "本地分屏合作（Multiplayer changes）· 提及 17 次"),
            ("评论量峰值出现在哪一天？", "trend", "2020-12-22</b>（551 条"),
        ]
        for question, expected_intent, expected_text in cases:
            with self.subTest(question=question):
                intent = local_statistics_intent(question)
                self.assertEqual(intent, expected_intent)
                result = answer("sdv_20201221_15", question, intent)
                self.assertEqual(result["intent"], expected_intent)
                self.assertIn(expected_text, result["answer"])

    def test_agent_questions_stay_with_agent(self):
        for question in (
            "星露谷 1.5 改了什么？",
            "中文玩家怎么评价姜岛探索？",
            "哪个版本发布日推荐率最高？",
            "哪个版本评论量最多？",
            "为什么玩家吐槽旅行图？",
        ):
            with self.subTest(question=question):
                self.assertIsNone(local_statistics_intent(question))


class AgentAdapterTests(unittest.TestCase):
    def test_topic_response_keeps_nlp_source(self):
        response = adapt_agent_response({
            "route": {"intent": "topic_change", "language": "all"},
            "patch": {"patch_id": "sdv_20201221_15"},
            "topic_stats": {"method": "rule", "rows": []},
            "citations": [{"id": 1, "type": "topic", "title": "NLP", "source": "detail.csv.gz"}],
        }, "更新后哪些方面的负面反馈增加了？")
        self.assertEqual(response["intent"], "topic_change")
        self.assertEqual(response["stats"], {})
        self.assertEqual(response["topic_stats"]["method"], "rule")
        self.assertEqual(response["topic_citations"][0]["source"], "detail.csv.gz")

    def test_topic_question_fallback_is_explicit(self):
        for question in ("玩家主要在讨论什么？", "更新后哪些方面的负面反馈增加了？"):
            self.assertIsNone(local_statistics_intent(question))
            result = answer("sdv_20201221_15", question)
            self.assertEqual(result["raw_status"], "unsupported")
            self.assertEqual(result["citations"], [])

    def test_reviewed_item_quotes(self):
        rows = verified_item_quotes("sdv_20201221_15", "S15_01")
        self.assertTrue(rows)
        self.assertTrue(all(r["verified"] for r in rows))
        self.assertTrue(all(r["text"] == r["evidence"] and "/profiles/" in r["url"] for r in rows))

    def test_agent_metrics_keep_language_group(self):
        response = adapt_agent_response({
            "numbers": [
                {"label": "更新前推荐率", "value": 0.8011, "unit": "%", "language_group": "en"},
                {"label": "更新后推荐率", "value": 0.7874, "unit": "%", "language_group": "en"},
            ],
        }, "英文推荐率是多少？")
        self.assertEqual(response["stats"]["language_group"], "en")
        self.assertEqual(response["stats"]["before_rate"], 80.11)
        self.assertEqual(response["stats"]["after_rate"], 78.74)

    def test_scoped_review_uses_matching_local_metrics(self):
        response = adapt_agent_response({
            "patch": {"patch_id": "sdv_20201221_15"},
            "route": {"intent": "review", "language": "chinese"},
        }, "中文玩家怎么评价姜岛探索？")
        self.assertEqual(response["stats"], {
            "before_rate": 93.51,
            "release_rate": 100.0,
            "after_rate": 93.9,
            "delta_pp": 0.39,
            "language_group": "chinese",
        })
        self.assertEqual(answer("sdv_20201221_15", "评论量峰值出现在哪一天？")["stats"]["language_group"], "all")

    def test_announcement_sources_keep_the_original_citation_numbers(self):
        url = "https://www.stardewvalley.net/stardew-valley-1-5-update-full-changelog/"
        response = adapt_agent_response({
            "citations": [
                {"id": 1, "type": "metric"},
                {"id": 2, "type": "announcement", "title": "姜岛探索", "url": url},
                {"id": 3, "type": "review", "review_id": "83814498", "snippet": "姜岛"},
                {"id": 4, "type": "announcement", "title": "海滩农场"},
            ],
            "links": {"announcement": url},
        }, "星露谷改了什么？")
        self.assertEqual(response["announcement_citations"], [
            {"id": 2, "title": "姜岛探索", "url": url},
            {"id": 4, "title": "海滩农场", "url": url},
        ])
        self.assertEqual([c["review_id"] for c in response["citations"]], ["83814498"])


if __name__ == "__main__":
    unittest.main()
