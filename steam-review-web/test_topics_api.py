"""第四部分 TopicAPI 路由测试：Mock Hive 客户端，走真实 HTTP 分发。"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import server
from backend import dataset, routes


class TopicApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 静态端点 /api/nlp/topics|items 需要 dataset 目录；造最小数据包
        cls.tmp_data = tempfile.TemporaryDirectory()
        root = Path(cls.tmp_data.name)
        nlp_dir = root / "nlp"
        nlp_dir.mkdir(parents=True)
        (nlp_dir / "topics.js").write_text("window.SRW_NLP_TOPICS = {\"topics\": []};", encoding="utf-8")
        (nlp_dir / "items.js").write_text("window.SRW_NLP_ITEMS = {\"items\": []};", encoding="utf-8")
        dataset.configure(root)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.tmp_data.cleanup()

    def request(self, path):
        try:
            response = urllib.request.urlopen(self.base + path, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def dispatch(self, path):
        """带 Mock Hive 的路由分发（不起 HTTP 也能测分发逻辑）。"""
        hive = Mock()
        hive.query.return_value = []
        with patch.object(routes, "get_hive", return_value=hive):
            return hive, routes.dispatch_get(path, "")

    # ------------------------------------------------------------- 分发与缓存约定

    def test_dispatch_maps_prefixes_and_marks_uncacheable(self):
        for path in ("/api/nlp/stats/distribution", "/api/nlp/stats/summary",
                     "/api/nlp/topics/trend", "/api/nlp/items/feedback", "/api/nlp/reviews"):
            hive, (payload, cacheable) = self.dispatch(path)
            self.assertIs(cacheable, False, path)
            self.assertEqual(payload.get("source"), "hive", path)

    def test_static_nlp_routes_still_win(self):
        """静态 /api/nlp/topics 与 /api/nlp/items 不能被动态前缀抢走。"""
        with patch.object(routes, "get_hive", side_effect=AssertionError("静态端点不应查 Hive")):
            payload, cacheable = routes.dispatch_get("/api/nlp/topics", "")
        self.assertTrue(cacheable)
        self.assertEqual(payload, {"topics": []})
        with patch.object(routes, "get_hive", side_effect=AssertionError("静态端点不应查 Hive")):
            payload, cacheable = routes.dispatch_get("/api/nlp/items", "")
        self.assertTrue(cacheable)
        self.assertEqual(payload, {"items": []})

    def test_unknown_nlp_path_404(self):
        with self.assertRaises(Exception):
            self.dispatch("/api/nlp/unknown")

    # ------------------------------------------------------------- 参数校验

    def test_parameter_validation(self):
        hive = Mock()
        hive.query.return_value = []
        cases = (
            ("/api/nlp/stats/distribution?lang=fr", 400),
            ("/api/nlp/stats/summary?appid=0", 400),
            ("/api/nlp/topics/trend?appid=413150&aspect=bogus", 400),
            ("/api/nlp/reviews?appid=413150&sentiment=bogus", 400),
            ("/api/nlp/reviews?appid=413150&limit=99999", 400),
            ("/api/nlp/reviews?appid=413150&extra=1", 400),
            ("/api/nlp/items/feedback?appid=413150&item_id=%E5%A7%9C%E5%B2%9B", 400),
        )
        for path, status in cases:
            with self.subTest(path=path), patch.object(routes, "get_hive", return_value=hive):
                self.assertEqual(self.request(path)[0], status)

    # ------------------------------------------------------------- 各端点返回结构

    def test_distribution(self):
        hive = Mock()
        hive.query.return_value = [
            {"aspect": "content", "name_zh": "内容/玩法", "sentiment": "positive", "count": 3},
            {"aspect": "content", "name_zh": "内容/玩法", "sentiment": "negative", "count": 1},
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/stats/distribution?lang=zh")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["distribution"]), 2)
        self.assertEqual(payload["lang"], "zh")
        sql = hive.query.call_args[0][0]
        self.assertIn("nlp_topic.topic_distribution", sql)

    def test_summary(self):
        hive = Mock()
        hive.query.side_effect = [
            [{"reviews": 100, "reviews_with_aspects": 60, "aspect_mentions": 150,
              "pos_aspects": 90, "neg_aspects": 20}],
            [{"primary_sentiment": "positive", "cnt": 40},
             {"primary_sentiment": "neutral", "cnt": 50},
             {"primary_sentiment": "none", "cnt": 10}],
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/stats/summary?appid=413150")
        self.assertEqual(status, 200)
        self.assertEqual(payload["totals"]["reviews"], 100)
        self.assertEqual(payload["by_primary_sentiment"]["positive"], 40)
        sql = hive.query.call_args_list[0][0][0]
        self.assertIn("appid = %s", sql)

    def test_trend_groups_by_day(self):
        hive = Mock()
        # SQL 已聚合为每日一行（先按日聚合再 LIMIT 天数，不截断单日情感）
        hive.query.return_value = [
            {"created_date": "2020-12-21", "positive": 5, "negative": 0, "neutral": 0},
            {"created_date": "2020-12-20", "positive": 2, "negative": 1, "neutral": 3},
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/topics/trend?appid=413150&aspect=content&days=7")
        self.assertEqual(status, 200)
        self.assertEqual([d["date"] for d in payload["trend"]], ["2020-12-20", "2020-12-21"])
        self.assertEqual(payload["trend"][0], {"date": "2020-12-20", "positive": 2, "negative": 1, "neutral": 3})
        self.assertEqual(payload["covered_days"], 2)
        sql = hive.query.call_args[0][0]
        self.assertIn("GROUP BY created_date", sql)
        self.assertIn("ORDER BY created_date DESC LIMIT %s", sql)
        # LIMIT 作用在外层（每日一行）而不是分组明细上
        self.assertEqual(sql.count("GROUP BY"), 1)
        self.assertIn("appid = %s", sql)

    def test_trend_without_appid_covers_all_games(self):
        hive = Mock()
        hive.query.return_value = []
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/topics/trend?days=7")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["appid"])
        self.assertNotIn("appid = %s", hive.query.call_args[0][0])

    def test_feedback_detail_and_list(self):
        hive = Mock()
        # 详情：先查条目字典校验归属，再查窗口表 + 情感统计（同一 appid+item_id 范围）
        hive.query.side_effect = [
            [{"item_id": "S15_01", "title": "姜岛探索", "game": "Stardew Valley", "aspect": "content"}],
            [{"patch_id": "sdv_20201221_15", "item_id": "S15_01", "item_title": "姜岛探索",
              "pre_14d": 1, "release_day": 0, "post_14d": 2, "out_of_window": 631}],
            [{"sentiment": "positive", "cnt": 2}, {"sentiment": "neutral", "cnt": 1}],
            [{"window_bucket": "out_of_window", "sentiment": "neutral", "cnt": 1}],
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/items/feedback?appid=413150&item_id=S15_01")
        self.assertEqual(status, 200)
        self.assertEqual(payload["item_title"], "姜岛探索")
        self.assertEqual(payload["windows"][0]["patch_id"], "sdv_20201221_15")
        self.assertEqual(payload["by_sentiment"]["positive"], 2)
        self.assertEqual(payload["by_sentiment"]["negative"], 0)
        catalog_sql = hive.query.call_args_list[0][0][0]
        self.assertIn("nlp_topic.item_dict", catalog_sql)
        self.assertIn("item_id = %s", catalog_sql)
        self.assertIn("appid = %s", catalog_sql)

    def test_feedback_item_game_mismatch_404(self):
        """用 CS2 的 appid 查星露谷条目：字典无匹配，404 且不再查统计。"""
        hive = Mock()
        hive.query.return_value = []  # item_dict 无此 appid+item_id 组合
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/items/feedback?appid=730&item_id=S15_01")
        self.assertEqual(status, 404)
        self.assertEqual(hive.query.call_count, 1)  # 只查了字典，未查窗口/情感

    def test_feedback_list_zero_fill(self):
        """列表以条目字典为基础：零命中条目补零出现。"""
        hive = Mock()
        hive.query.side_effect = [
            [  # item_dict：两个官方条目
                {"item_id": "S15_01", "title": "姜岛探索", "game": "Stardew Valley", "aspect": "content"},
                {"item_id": "S16_02", "title": "沙漠节", "game": "Stardew Valley", "aspect": "content"},
            ],
            [  # 情感明细：只有姜岛探索被命中
                {"item_id": "S15_01", "sentiment": "positive", "cnt": 7},
                {"item_id": "S15_01", "sentiment": "neutral", "cnt": 2},
            ],
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/items/feedback?appid=413150")
        self.assertEqual(status, 200)
        self.assertEqual([item["item_id"] for item in payload["items"]], ["S15_01", "S16_02"])
        self.assertEqual(payload["items"][0]["by_sentiment"], {"positive": 7, "negative": 0, "neutral": 2})
        self.assertEqual(payload["items"][1]["item_title"], "沙漠节")
        self.assertEqual(payload["items"][1]["by_sentiment"], {"positive": 0, "negative": 0, "neutral": 0})

    def test_feedback_list_without_appid(self):
        hive = Mock()
        hive.query.side_effect = [[], []]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/items/feedback")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["appid"])
        self.assertNotIn("appid = %s", hive.query.call_args_list[0][0][0])

    def test_reviews_pagination(self):
        hive = Mock()
        hive.query.return_value = [
            {"review_id": 1, "game_name": "Stardew Valley", "created_date": "2020-12-21",
             "aspect": "content", "name_zh": "内容/玩法", "sentiment": "positive",
             "confidence": 0.8, "sentiment_word": "好玩", "evidence": "姜岛探索很好玩"},
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/reviews?appid=413150&aspect=content&limit=10&offset=20")
        self.assertEqual(status, 200)
        self.assertEqual(payload["offset"], 20)
        self.assertEqual(payload["reviews"][0]["evidence"], "姜岛探索很好玩")
        sql = hive.query.call_args[0][0]
        self.assertIn("LIMIT %s OFFSET %s", sql)
        self.assertIn("ORDER BY confidence DESC, aspect, review_id", sql)
        self.assertIn("topic_sentiment_detail", sql)
        self.assertIn("appid = %s", sql)

    def test_reviews_without_appid(self):
        hive = Mock()
        hive.query.return_value = []
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/reviews?lang=en&limit=3")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["appid"])
        sql = hive.query.call_args[0][0]
        self.assertNotIn("appid = %s", sql)
        self.assertIn("topic_sentiment_detail_en", sql)

    def test_reviews_english_table_selected_by_lang(self):
        hive = Mock()
        hive.query.return_value = []
        with patch.object(routes, "get_hive", return_value=hive):
            status, _ = self.request("/api/nlp/reviews?appid=413150&lang=en")
        self.assertEqual(status, 200)
        self.assertIn("topic_sentiment_detail_en", hive.query.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
