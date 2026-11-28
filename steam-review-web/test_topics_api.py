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
        hive.query.return_value = [
            {"created_date": "2020-12-20", "sentiment": "positive", "cnt": 2},
            {"created_date": "2020-12-20", "sentiment": "negative", "cnt": 1},
            {"created_date": "2020-12-21", "sentiment": "positive", "cnt": 5},
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/topics/trend?appid=413150&aspect=content")
        self.assertEqual(status, 200)
        self.assertEqual([d["date"] for d in payload["trend"]], ["2020-12-20", "2020-12-21"])
        self.assertEqual(payload["trend"][0]["negative"], 1)

    def test_feedback_detail_and_list(self):
        hive = Mock()
        hive.query.side_effect = [
            [{"patch_id": "sdv_20201221_15", "item_id": "S15_01", "item_title": "姜岛探索",
              "pre_14d": 1, "release_day": 0, "post_14d": 2, "out_of_window": 631}],
            [{"sentiment": "positive", "cnt": 2}, {"sentiment": "neutral", "cnt": 1}],
            [{"window_bucket": "out_of_window", "sentiment": "neutral", "cnt": 1}],
        ]
        with patch.object(routes, "get_hive", return_value=hive):
            status, payload = self.request("/api/nlp/items/feedback?appid=413150&item_id=S15_01")
        self.assertEqual(status, 200)
        self.assertEqual(payload["windows"][0]["patch_id"], "sdv_20201221_15")
        self.assertEqual(payload["by_sentiment"]["positive"], 2)

        hive2 = Mock()
        hive2.query.return_value = [
            {"item_id": "S15_01", "item_title": "姜岛探索", "aspect": "content",
             "sentiment": "positive", "cnt": 7},
        ]
        with patch.object(routes, "get_hive", return_value=hive2):
            status, payload = self.request("/api/nlp/items/feedback?appid=413150")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["by_sentiment"]["positive"], 7)

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
        self.assertIn("topic_sentiment_detail", sql)

    def test_reviews_english_table_selected_by_lang(self):
        hive = Mock()
        hive.query.return_value = []
        with patch.object(routes, "get_hive", return_value=hive):
            status, _ = self.request("/api/nlp/reviews?appid=413150&lang=en")
        self.assertEqual(status, 200)
        self.assertIn("topic_sentiment_detail_en", hive.query.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
