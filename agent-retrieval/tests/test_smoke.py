"""冒烟测试：目录、指标、拒答、证据链接、HTTP 接口。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.retrieval import KnowledgeBase  # noqa: E402


class KnowledgeBaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kb = KnowledgeBase(ROOT / "data")
        cls.agent = Agent(cls.kb)

    def test_catalog_shape(self) -> None:
        self.assertEqual(len(self.kb.catalog.patches), 26)
        self.assertEqual(len(self.kb.catalog.items), 75)
        self.assertEqual(len(self.kb.catalog.games), 12)
        self.assertGreater(len(self.kb.catalog.metrics), 70)

    def test_metric_numbers(self) -> None:
        res = self.agent.ask("艾尔登法环 1.12 更新后推荐率是多少？", appid="1245620", patch_id="steam_1245620_20240620")
        self.assertEqual(res["status"], "answered")
        after = next(n for n in res["numbers"] if n["label"] == "更新后推荐率")
        self.assertAlmostEqual(after["value"], 0.825365, places=5)
        self.assertIn("82.5%", res["answer"])
        self.assertEqual(res["citations"][0]["type"], "metric")

    def test_ranking(self) -> None:
        res = self.agent.ask("哪个版本更新后推荐率下降最多？")
        self.assertIn("ELDEN RING", res["answer"])
        self.assertIn("13.5", res["answer"])

    def test_outside_catalog(self) -> None:
        res = self.agent.ask("明天要带伞吗？")
        self.assertEqual(res["status"], "outside_catalog")

    def test_no_fabricated_evidence(self) -> None:
        res = self.agent.ask("玩家怎么评价海浪效果？", appid="252490", patch_id="rust_20230706_deep_sea")
        self.assertEqual(res["status"], "answered")
        self.assertIn("暂无已复核引文", res["answer"])
        self.assertEqual(res["evidence_status"], "none")

    def test_evidence_has_steam_url(self) -> None:
        res = self.agent.ask("玩家怎么评价姜岛探索？", appid="413150", patch_id="sdv_20201221_15")
        self.assertEqual(res["evidence_status"], "verified_quotes")
        reviews = [c for c in res["citations"] if c["type"] == "review"]
        self.assertTrue(reviews)
        for c in reviews:
            self.assertTrue(c["verified"])
            self.assertTrue(c["url"].startswith("https://steamcommunity.com/"))
            self.assertIn("已复核", c["detail"])

    def test_retrieval_status(self) -> None:
        out = self.kb.search_reviews("姜岛", "sdv_20201221_15", item_id="S15_01", top_k=3)
        self.assertEqual(out["status"], "quotes")
        self.assertGreaterEqual(len(out["quotes"]), 1)
        bad = self.kb.search_reviews("姜岛", "sdv_20201221_15", item_id="S99_99")
        self.assertEqual(bad["status"], "unknown_item")

    def test_candidates(self) -> None:
        out = self.kb.item_candidates("姜岛真好玩，火山地牢有点绕", "413150", "sdv_20201221_15", top_k=3)
        ids = [c["item_id"] for c in out["candidates"]]
        self.assertIn("S15_01", ids)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from src.api import create_app

        cls.client = TestClient(create_app(ROOT / "data"))

    def test_health(self) -> None:
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_ask(self) -> None:
        r = self.client.post("/api/agent/ask", json={
            "question": "1.5 更新后推荐率是多少？", "appid": "413150", "patch_id": "sdv_20201221_15"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "answered")

    def test_patch(self) -> None:
        r = self.client.get("/api/patches/sdv_20201221_15")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["items"]), 5)

    def test_unknown_patch(self) -> None:
        self.assertEqual(self.client.get("/api/patches/nope").status_code, 404)

    def test_verified_endpoint(self) -> None:
        r = self.client.post("/api/retrieve/verified", json={
            "query": "姜岛", "patch_id": "sdv_20201221_15", "item_id": "S15_01", "top_k": 3})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "verified_quotes")
        self.assertTrue(body["quotes"])
        self.assertTrue(all(q["verified"] for q in body["quotes"]))


if __name__ == "__main__":
    unittest.main()
