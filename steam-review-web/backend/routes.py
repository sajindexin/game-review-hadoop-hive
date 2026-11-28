from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs

from . import dataset
from .common import ApiError, get_hive
from .topics import TopicAPI
from .versions import VersionAPI

logger = logging.getLogger(__name__)

# /api/patch/<patch_id>
_PATCH_ROUTE = re.compile(r"^/api/patch/([A-Za-z0-9_]+)$")

# 这些接口返回构建产物，内容随部署变化，用 ETag 协商缓存
_STATIC_DATA_ROUTES = {
    "/api/meta": dataset.meta,
    "/api/index": dataset.index,
    "/api/nlp/topics": dataset.nlp_topics,
    "/api/nlp/items": dataset.nlp_items,
}

# 允许的查询参数：空 = 不接受任何参数
_ALLOWED_QUERY = {
    "/api/verified": {"patch_id"},
}


def hive_health() -> dict:
    if get_hive().query("SELECT 1 AS ok", max_rows=1) != [{"ok": 1}]:
        raise ApiError(503, "hive_unavailable", "Hive 健康检查返回异常")
    return {"status": "ok", "source": "hive"}


def _check_query(path: str, query: str) -> dict:
    """校验查询参数，返回解析后的字典。"""
    allowed = _ALLOWED_QUERY.get(path, set())
    if not query:
        return {}
    params = parse_qs(query, keep_blank_values=True)
    extra = set(params) - allowed
    if extra:
        raise ApiError(400, "invalid_request", f"不支持的查询参数：{', '.join(sorted(extra))}")
    return {k: v[0] for k, v in params.items()}


def dispatch_get(path: str, query: str = ""):
    """GET 路由分发。

    返回 (payload, cacheable) 二元组：cacheable 为 True 时允许前端做协商缓存。
    - 静态数据集（构建产物）：可缓存
    - Hive 健康检查、版本分析接口：实时查库，不缓存
    """
    if path == "/api/health/hive":
        if query:
            raise ApiError(400, "invalid_request", "此接口不接受查询参数")
        return hive_health(), False

    if path == "/api/versions" or path.startswith("/api/versions/"):
        return VersionAPI(get_hive).get(path, query), False

    # 第四部分：主题分析 / 情感统计 / 改动反馈（Hive 实时查询，不协商缓存）。
    # 注意顺序：/api/nlp/topics、/api/nlp/items 两个静态端点必须先走 _STATIC_DATA_ROUTES，
    # 这里只接 stats/、topics/trend、items/feedback、reviews 四组动态前缀。
    if (path.startswith("/api/nlp/stats/")
            or path == "/api/nlp/topics/trend" or path.startswith("/api/nlp/topics/trend/")
            or path.startswith("/api/nlp/items/feedback")
            or path == "/api/nlp/reviews" or path.startswith("/api/nlp/reviews/")):
        return TopicAPI(get_hive).get(path, query), False

    handler = _STATIC_DATA_ROUTES.get(path)
    if handler is not None:
        _check_query(path, query)
        return handler(), True

    if path == "/api/verified":
        params = _check_query(path, query)
        return dataset.verified(params.get("patch_id")), True

    m = _PATCH_ROUTE.match(path)
    if m:
        if query:
            raise ApiError(400, "invalid_request", "此接口不接受查询参数")
        return dataset.patch(m.group(1)), True

    raise ApiError(404, "not_found", "接口不存在")


def api_error(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    try:
        from hive_service import HiveConfigurationError, HiveQueryError, HiveTimeout, HiveUnavailable
    except ImportError:
        pass
    else:
        if isinstance(exc, (HiveConfigurationError, HiveUnavailable)):
            logger.warning("Hive unavailable: %s", type(exc).__name__)
            return ApiError(503, "hive_unavailable", "Hive 服务不可用或未配置")
        if isinstance(exc, HiveTimeout):
            return ApiError(504, "hive_timeout", "Hive 查询超时")
        if isinstance(exc, HiveQueryError):
            logger.warning("Hive query failed: %s", type(exc).__name__)
            return ApiError(500, "hive_query_failed", "Hive 查询失败")
    logger.error("API request failed", exc_info=exc)
    return ApiError(500, "internal_error", "服务内部错误")
