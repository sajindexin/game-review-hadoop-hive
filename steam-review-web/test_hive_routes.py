import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import server
from backend import routes
from backend.common import ApiError


class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def request(self, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def assert_json(self, path, expected_status, payload=None):
        status, headers, body = self.request(path, payload)
        self.assertEqual(status, expected_status)
        self.assertEqual(headers.get_content_type(), "application/json")
        return json.loads(body)

    def test_pending_modules_have_explicit_errors(self):
        with patch.object(routes.version_api, "get_index", side_effect=ApiError(501, "not_implemented", "待接入")):
            self.assertEqual(self.assert_json("/api/index", 501)["error"]["code"], "not_implemented")

    def test_unknown_api_and_unexpected_query(self):
        self.assertEqual(self.assert_json("/api/unknown", 404)["error"]["code"], "not_found")
        self.assertEqual(self.assert_json("/api/nlp/items?lang=zh", 400)["error"]["code"], "invalid_request")

    def test_module_results_are_returned_without_envelope(self):
        for module, function, path in ((routes.version_api, "get_index", "/api/index"),
                                       (routes.nlp_api, "get_topics", "/api/nlp/topics"),
                                       (routes.nlp_api, "get_items", "/api/nlp/items")):
            with self.subTest(path=path), patch.object(module, function, return_value={"data": function}) as handler:
                self.assertEqual(self.assert_json(path, 200), {"data": function})
                handler.assert_called_once_with()
        with patch.object(routes.version_api, "get_patch", return_value={"patch": {"patch_id": "p1"}}) as handler:
            self.assertEqual(self.assert_json("/api/patches/p1", 200), {"patch": {"patch_id": "p1"}})
            handler.assert_called_once_with("p1")
        with patch.object(routes.version_api, "get_verified_quotes", return_value=[{"item_id": "i1"}]):
            self.assertEqual(self.assert_json("/api/verified-quotes", 200), [{"item_id": "i1"}])
        with patch.object(routes.version_api, "get_patch", side_effect=ApiError(404, "not_found", "版本不存在")):
            self.assertEqual(self.assert_json("/api/patches/missing", 404)["error"]["code"], "not_found")

    def test_health_uses_hive_without_cache(self):
        hive = Mock()
        hive.query.return_value = [{"ok": 1}]
        with patch.object(routes, "get_hive", return_value=hive):
            self.assertEqual(self.assert_json("/api/health/hive", 200), {"status": "ok", "source": "hive"})
        hive.query.assert_called_once_with("SELECT 1 AS ok", max_rows=1)

    def test_hive_errors_mapped_to_http(self):
        try:
            from hive_service import HiveConfigurationError, HiveQueryError, HiveTimeout, HiveUnavailable
        except ImportError:
            self.skipTest("install the shared Hive package to check Hive exception mapping")
        for error, status, code in ((HiveConfigurationError, 503, "hive_unavailable"),
                                    (HiveUnavailable, 503, "hive_unavailable"),
                                    (HiveTimeout, 504, "hive_timeout"),
                                    (HiveQueryError, 500, "hive_query_failed")):
            with self.subTest(error=error), patch.object(routes, "get_hive", side_effect=error):
                self.assertEqual(self.assert_json("/api/health/hive", status)["error"]["code"], code)

    def test_existing_static_pages_and_local_ask(self):
        for path in ("/index.html", "/patch.html", "/nlp.html", "/ask.html", "/data/index.js"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        payload = {"patch_id": "sdv_20201221_15", "question": "评论量峰值出现在哪一天？"}
        with patch.object(server, "AGENT_BASE", None):
            result = self.assert_json("/api/ask", 200, payload)
        self.assertEqual(result["intent"], "trend")
        self.assertEqual(result["source"], "local-rules")
        self.assertIn("2020-12-22", result["answer"])

    def test_agent_proxy_preserves_fallback(self):
        payload = {"patch_id": "sdv_20201221_15", "question": "中文玩家怎么评价姜岛探索？"}
        with (
            patch.object(server, "AGENT_BASE", "http://localhost:8000"),
            patch.object(server, "call_agent", side_effect=urllib.error.URLError("offline")),
        ):
            result = self.assert_json("/api/ask", 200, payload)
        self.assertEqual(result["source"], "local-rules")
        self.assertTrue(result["fallback"])

    def test_agent_proxy_forwards_and_adapts_response(self):
        calls = []

        class AgentHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                calls.append((self.path, json.loads(body)))
                result = {"status": "answered", "answer": "更新后推荐率为 98.42%",
                          "numbers": [{"label": "更新后推荐率", "value": 0.9842, "unit": "%"}]}
                response = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), AgentHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        payload = {"patch_id": "sdv_20201221_15", "question": "这次更新后推荐率是多少？"}
        try:
            with patch.object(server, "AGENT_BASE", f"http://127.0.0.1:{upstream.server_port}"):
                result = self.assert_json("/api/ask", 200, payload)
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join()
        self.assertEqual(calls, [("/api/agent/ask", {**payload, "appid": "413150"})])
        self.assertEqual(result["source"], "agent-retrieval")
        self.assertFalse(result["fallback"])
        self.assertEqual(result["stats"]["after_rate"], 98.42)


if __name__ == "__main__":
    unittest.main()
