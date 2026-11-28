"""静态数据集的 API 输出。

页面原来通过 `<script src="data/*.js">` 注入 `window.SRW_*` 全局变量，
这种方式有三个问题：

1. 首屏必须等脚本下载解析完才能渲染，26 个版本数据包更大；
2. 数据被写进全局命名空间，多个页面共用时容易互相覆盖（例如 nlp.js 复用 SRW_PATCH 的坑）；
3. 缓存无法按 URL 精细控制，也做不了条件请求。

改成 HTTP JSON 接口后，前端用 fetch 并发取数，能配 ETag 做协商缓存。

数据源仍是 `data/` 下的 JS 文件（内容是 `window.SRW_X = {...};`），
这里只负责解析取出里面的 JSON 对象，再原样以 JSON 返回，**不改变任何数据结构**，
所以前端渲染逻辑不需要跟着改。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import ApiError

# data/index.js → window.SRW_INDEX = {...};
_ASSIGN = re.compile(r"^\s*window\.(SRW_[A-Z_]+)\s*=\s*", re.S)

# 数据根目录由调用方注入，避免本模块硬编码相对路径
_DATA_ROOT: Path | None = None

# route key → 相对 data/ 的文件路径
_FILES = {
    "index": "index.js",
    "nlp_topics": "nlp/topics.js",
    "nlp_items": "nlp/items.js",
    "verified": "verified.js",
}


def configure(data_root: Path) -> None:
    """由 server.py 在启动时调用，绑定数据目录。"""
    global _DATA_ROOT
    _DATA_ROOT = Path(data_root)


def _root() -> Path:
    if _DATA_ROOT is None:
        raise ApiError(500, "not_configured", "数据目录未初始化")
    return _DATA_ROOT


def _read_js_payload(path: Path) -> dict:
    """从 `window.SRW_X = {...};` 里取出 JSON 对象。

    不用 eval：这些文件是构建产物，格式固定为单条赋值语句，
    按前缀切掉赋值部分再 json.loads 即可，避免任何代码执行风险。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ApiError(404, "not_found", f"数据文件不存在：{path.name}")

    m = _ASSIGN.match(text)
    if not m:
        raise ApiError(500, "bad_payload", f"数据文件格式异常：{path.name}")

    body = text[m.end():].strip()
    if body.endswith(";"):
        body = body[:-1]

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiError(500, "bad_payload", f"数据文件 JSON 解析失败：{path.name}（{exc.msg}）")


def _load(key: str) -> dict:
    rel = _FILES.get(key)
    if rel is None:
        raise ApiError(404, "not_found", "接口不存在")
    return _read_js_payload(_root() / rel)


# ---------------------------------------------------------------- 各接口

def meta() -> dict:
    """轻量元信息：前端可先调它拿到版本清单，再按需拉详情。"""
    idx = _load("index")
    return {
        "generated_at": idx.get("generated_at"),
        "source": idx.get("source", {}),
        "games": [
            {
                "appid": g.get("appid"),
                "name": g.get("name"),
                "reviews_total": g.get("reviews_total"),
                "patches": [p.get("patch_id") for p in g.get("patches", [])],
            }
            for g in idx.get("games", [])
        ],
    }


def index() -> dict:
    return _load("index")


def patch(patch_id: str) -> dict:
    if not patch_id or not re.fullmatch(r"[A-Za-z0-9_]+", patch_id):
        raise ApiError(400, "invalid_patch_id", "patch_id 只能包含字母、数字和下划线")

    path = _root() / "patches" / f"{patch_id}.js"
    if not path.exists():
        raise ApiError(404, "patch_not_found", f"找不到版本：{patch_id}")
    return _read_js_payload(path)


def nlp_topics() -> dict:
    return _load("nlp_topics")


def nlp_items() -> dict:
    return _load("nlp_items")


def verified(patch_id: str | None = None) -> list:
    """已复核引文。

    全量约 403 KB，但单个版本只用其中 30 KB 左右，
    所以支持按 patch_id 过滤——这正是接口相对静态脚本的价值。
    """
    rows = _load("verified")
    if not isinstance(rows, list):
        raise ApiError(500, "bad_payload", "verified 数据格式异常")
    if patch_id is None:
        return rows
    return [r for r in rows if str(r.get("patch_id")) == patch_id]
