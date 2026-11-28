"""数据接口与静态数据的一致性测试。

这层测试保护两件事：

1. **接口输出等于源文件内容**。前端不知道数据从哪来，只认 JSON 结构；
   如果接口少字段或改了类型，页面会静默渲染成空白，比报错更难查。
2. **路由的边界行为**。路径穿越、非法 patch_id、未知路径都必须给出明确状态码，
   而不是抛出 500 或者返回半个页面。
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from backend import dataset
from backend.common import ApiError
from backend.routes import dispatch_get

DATA = Path(__file__).resolve().parent / "data"


def source_payload(rel: str) -> dict:
    """从 data/*.js 里取出 JSON 对象，作为对照基准。"""
    text = (DATA / rel).read_text(encoding="utf-8")
    body = re.sub(r"^\s*window\.SRW_[A-Z_]+\s*=\s*", "", text).strip()
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)


class DatasetApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset.configure(DATA)

    def _payload(self, url: str):
        """url 可带查询串，如 /api/verified?patch_id=xxx。"""
        path, _, query = url.partition("?")
        payload, cacheable = dispatch_get(path, query)
        self.assertTrue(cacheable, f"{url} 应可缓存")
        return payload

    def test_index_matches_source(self):
        self.assertEqual(
            json.dumps(self._payload("/api/index"), sort_keys=True, ensure_ascii=False),
            json.dumps(source_payload("index.js"), sort_keys=True, ensure_ascii=False),
        )

    def test_nlp_topics_matches_source(self):
        self.assertEqual(
            json.dumps(self._payload("/api/nlp/topics"), sort_keys=True, ensure_ascii=False),
            json.dumps(source_payload("nlp/topics.js"), sort_keys=True, ensure_ascii=False),
        )

    def test_nlp_items_matches_source(self):
        self.assertEqual(
            json.dumps(self._payload("/api/nlp/items"), sort_keys=True, ensure_ascii=False),
            json.dumps(source_payload("nlp/items.js"), sort_keys=True, ensure_ascii=False),
        )

    def test_verified_matches_source(self):
        self.assertEqual(
            json.dumps(self._payload("/api/verified"), sort_keys=True, ensure_ascii=False),
            json.dumps(source_payload("verified.js"), sort_keys=True, ensure_ascii=False),
        )

    def test_verified_filtered_by_patch(self):
        """带 patch_id 时只返回该版本的引文，且必须是全量的子集。"""
        full = self._payload("/api/verified")
        part = self._payload("/api/verified?patch_id=sdv_20201221_15")

        self.assertLess(len(part), len(full))
        self.assertTrue(all(r["patch_id"] == "sdv_20201221_15" for r in part))

        # 逐条核对确实都在全量里
        full_ids = {r["review_id"] for r in full}
        for r in part:
            self.assertIn(r["review_id"], full_ids)

        # 结果应与全量里筛出来的一致
        expected = [r for r in full if r["patch_id"] == "sdv_20201221_15"]
        self.assertEqual(part, expected)

    def test_verified_unknown_patch_returns_empty(self):
        self.assertEqual(self._payload("/api/verified?patch_id=no_such"), [])

    def test_every_patch_reachable(self):
        """26 个版本包都要能取到，且内容与源文件一致。"""
        patches = sorted(p.stem for p in (DATA / "patches").glob("*.js"))
        self.assertEqual(len(patches), 26)
        for pid in patches:
            with self.subTest(patch_id=pid):
                got = self._payload(f"/api/patch/{pid}")
                want = source_payload(f"patches/{pid}.js")
                self.assertEqual(got, want)

    def test_meta_is_lightweight(self):
        meta = self._payload("/api/meta")
        self.assertEqual(len(meta["games"]), 12)
        self.assertEqual(meta["source"]["patches"], 26)
        # 只给 patch_id 清单，不带整包数据
        first = meta["games"][0]
        self.assertIn("patches", first)
        self.assertIsInstance(first["patches"][0], str)
        self.assertLess(len(json.dumps(meta, ensure_ascii=False)), 20000)


class RouteErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dataset.configure(DATA)

    def test_unknown_route_404(self):
        with self.assertRaises(ApiError) as ctx:
            dispatch_get("/api/whatever")
        self.assertEqual(ctx.exception.status, 404)

    def test_missing_patch_404(self):
        with self.assertRaises(ApiError) as ctx:
            dispatch_get("/api/patch/no_such_patch")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "patch_not_found")

    def test_path_traversal_rejected(self):
        """路径穿越不能读到 data 以外的文件。"""
        for evil in ("/api/patch/..%2F..%2Fetc", "/api/patch/../server", "/api/patch/a/b"):
            with self.subTest(path=evil):
                with self.assertRaises(ApiError) as ctx:
                    dispatch_get(evil)
                self.assertIn(ctx.exception.status, (400, 404))

    def test_query_rejected(self):
        with self.assertRaises(ApiError) as ctx:
            dispatch_get("/api/index", "a=1")
        self.assertEqual(ctx.exception.status, 400)

    def test_health_hive_not_cacheable(self):
        """健康检查是探活，不该被标成可缓存。"""
        try:
            _, cacheable = dispatch_get("/api/health/hive")
        except ApiError:
            # 没有 Hive 客户端时抛 503，同样不需要断言缓存标记
            return
        self.assertFalse(cacheable)


if __name__ == "__main__":
    unittest.main()
