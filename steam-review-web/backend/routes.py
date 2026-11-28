from __future__ import annotations

import logging

from .common import ApiError, get_hive
from .versions import VersionAPI

logger = logging.getLogger(__name__)


def hive_health() -> dict:
    if get_hive().query("SELECT 1 AS ok", max_rows=1) != [{"ok": 1}]:
        raise ApiError(503, "hive_unavailable", "Hive 健康检查返回异常")
    return {"status": "ok", "source": "hive"}


def dispatch_get(path: str, query: str = "") -> dict:
    """分发 Hive 健康检查和版本分析接口。"""
    if path == "/api/health/hive":
        if query:
            raise ApiError(400, "invalid_request", "此接口不接受查询参数")
        return hive_health()
    if path == "/api/versions" or path.startswith("/api/versions/"):
        return VersionAPI(get_hive).get(path, query)
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
