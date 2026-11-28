"""版本与统计接口，由黄静雯接入业务查询。"""

from .common import pending


def get_index() -> dict:
    """返回与 data/index.js 中 SRW_INDEX 相同结构的 JSON 对象。"""
    pending("版本总览")


def get_patch(patch_id: str) -> dict:
    """返回与 data/patches/<patch_id>.js 中 SRW_PATCH 相同结构的 JSON 对象。"""
    pending("版本详情")


def get_verified_quotes() -> list[dict]:
    """返回与 data/verified.js 中复核引文数组同结构的 JSON。"""
    pending("复核引文")
