from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def payload(self) -> dict:
        return {"error": {"code": self.code, "message": self.message}}


def get_hive():
    """在 Hive 路由实际被调用时加载公共客户端，兼容已有离线启动方式。"""
    try:
        from hive_service import get_hive_client
    except ImportError as exc:
        raise ApiError(503, "hive_unavailable", "请先安装公共 Hive 客户端") from exc
    return get_hive_client()
