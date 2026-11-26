"""冒烟测试：目录、指标、拒答、证据链接、HTTP 接口。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.retrieval import KnowledgeBase  # noqa: E402
from src.router import Router  # noqa: E402


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


class _FakeClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error

    def post(self, *args, **kwargs):
        if self.error:
            raise self.error
        return _FakeResponse(self.content or "{}")


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

    def test_ranking_ignores_page_patch(self) -> None:
        res = self.agent.ask("哪个版本推荐率下降最多？", appid="413150", patch_id="sdv_20201221_15")
        self.assertEqual(res["status"], "answered")
        self.assertIn("ELDEN RING", res["answer"])

    def test_ranking_extreme_wording(self) -> None:
        res = self.agent.ask("推荐率最高的版本是哪个？")
        self.assertEqual(res["status"], "answered")
        self.assertIn("最高", res["answer"])

    def test_multi_item_question_falls_back_to_available_item(self) -> None:
        res = self.agent.ask("玩家怎么评价姜岛和火山地牢？", appid="413150", patch_id="sdv_20201221_15")
        self.assertEqual(res["evidence_status"], "verified_quotes")
        items = {c.get("item_id") for c in res["citations"] if c["type"] == "review"}
        self.assertEqual(items, {"S15_01"})

    def test_release_day_alias(self) -> None:
        res = self.kb.search_verified("", "steam_2358720_20241214", phase="release_day", top_k=5)
        self.assertEqual(res["status"], "verified_quotes")
        self.assertTrue(all(q["phase"] == "release" for q in res["quotes"]))

    def test_games_have_review_counts(self) -> None:
        counts = [g["review_count"] for g in self.kb.catalog.games]
        self.assertTrue(all(c > 0 for c in counts), counts)
        self.assertEqual(sum(counts), 4884580)

    def test_phase_edit_rules_kept(self) -> None:
        # 原 find_evidence 的阶段规则：469 条里 55 条会被排除
        def excluded(v: dict) -> bool:
            if v["phase"] == "before" and v.get("before_review_edited_after_release"):
                return True
            if v["phase"] == "after" and v.get("edited_after_window"):
                return True
            if v["phase"] == "release" and v.get("updated_ts") and (
                    int(v["updated_ts"]) // 86400 > int(v["created_ts"]) // 86400):
                return True
            return False
        affected = [v for v in self.kb.verified if excluded(v)]
        self.assertEqual(len(affected), 55)
        patch = "steam_2358720_20241214"
        must_exclude = {v["review_id"] for v in affected if v["patch_id"] == patch and v["phase"] == "before"}
        self.assertTrue(must_exclude)
        res = self.kb.search_verified("", patch, phase="before", top_k=50)
        got = {q["review_id"] for q in res["quotes"]}
        self.assertFalse(got & must_exclude, "阶段过滤没有排除窗口后编辑的评测")

    def test_language_filter_is_hard(self) -> None:
        res = self.agent.ask("玩家怎么评价姜岛探索？", appid="413150",
                             patch_id="sdv_20201221_15", language="en")
        reviews = [c for c in res["citations"] if c["type"] == "review"]
        self.assertTrue(reviews)
        self.assertTrue(all(c["language_std"] == "en" for c in reviews))

    def test_item_relevance_gate(self) -> None:
        res = self.kb.search_verified("海浪", "rust_20230706_deep_sea", top_k=3)
        self.assertEqual(res["status"], "no_verified_quotes")

    def test_announcement_lists_all_items(self) -> None:
        res = self.agent.ask("Rust Jungle Update 改了什么？", appid="252490", patch_id="rust_20250501_jungle")
        anns = [c for c in res["citations"] if c["type"] == "announcement"]
        self.assertEqual(len(anns), 10)
        self.assertIn("服务器与传送带性能", res["answer"])

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


class RouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kb = KnowledgeBase(ROOT / "data")
        cls.patch = cls.kb.catalog.patch("sdv_20201221_15")
        cls.items = cls.kb.catalog.items_of("sdv_20201221_15")

    def test_llm_route_parsed(self) -> None:
        payload = json.dumps({
            "intent": "review", "phases": ["before", "after"], "item_ids": ["S15_01"],
            "language": "chinese", "ranking": None, "confidence": 0.92,
        }, ensure_ascii=False)
        router = Router(client=_FakeClient(payload))
        route = router.route("更新前后玩家怎么评价姜岛？", self.patch, self.items)
        self.assertEqual(route.source, "llm")
        self.assertEqual(route.intent, "review")
        self.assertEqual(route.phases, ["before", "after"])
        self.assertEqual(route.item_ids, ["S15_01"])
        self.assertEqual(route.language, "chinese")

    def test_llm_failure_falls_back(self) -> None:
        router = Router(client=_FakeClient(error=RuntimeError("boom")))
        route = router.route("更新前后玩家怎么评价姜岛？", self.patch, self.items)
        self.assertEqual(route.source, "rules_fallback")
        self.assertEqual(route.phases, ["before", "after"])

    def test_agent_uses_compare_route(self) -> None:
        payload = json.dumps({
            "intent": "review", "phases": ["before", "after"],
            "item_ids": ["2358720_20241214_01"], "language": None, "ranking": None,
        }, ensure_ascii=False)
        agent = Agent(self.kb, router=Router(client=_FakeClient(payload)))
        res = agent.ask("更新前后玩家怎么评价土地庙挑战？", appid="2358720",
                        patch_id="steam_2358720_20241214")
        phases = {c.get("phase") for c in res["citations"] if c["type"] == "review"}
        self.assertTrue({"before", "after"} <= phases)


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

    def test_invalid_phase_rejected(self) -> None:
        r = self.client.post("/api/agent/ask", json={
            "question": "x", "patch_id": "sdv_20201221_15", "phase": "bad"})
        self.assertEqual(r.status_code, 422)

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
