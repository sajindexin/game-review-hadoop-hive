from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .client import SteamClient
from .models import CrawlError, Query
from .storage import Store

LOGGER = logging.getLogger(__name__)


def run_task(
    store: Store,
    client: SteamClient,
    task_id: str,
    *,
    max_items: int = 1000,
    max_pages: int = 0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    task = store.task(task_id)
    if task["status"] == "completed":
        raise CrawlError("该任务已完成；如需刷新数据，请重新运行 reviews 或 news 创建任务")
    initial_matched, initial_written, initial_pages = (
        task["matched"],
        task["written"],
        task["pages"],
    )
    try:
        query = Query.from_dict(task["config"]["query"])
        LOGGER.info("任务 %s；来源=%s，appid=%s", task_id, query.source, query.appid)
        store.set_status(task_id, "running")
        while True:
            task = store.task(task_id)
            if task["status"] == "completed":
                break
            remaining = max_items - (task["matched"] - initial_matched) if max_items else None
            if remaining is not None and remaining <= 0:
                store.set_status(task_id, "paused")
                break
            if task["checkpoint"]["pending"] is None:
                if max_pages and task["pages"] - initial_pages >= max_pages:
                    store.set_status(task_id, "paused")
                    break
                page = client.fetch(query, task["checkpoint"]["position"])
                store.cache_page(task_id, page)
            task = store.consume_page(task_id, remaining)
            if on_progress is not None:
                on_progress(task)
            else:
                count = task["matched"] - initial_matched
                progress = (
                    f"{count:,}/{max_items:,} ({count / max_items:.2%})"
                    if max_items
                    else f"{count:,}"
                )
                LOGGER.info(
                    "任务 %s：本次匹配 %s，累计写入 %s，已获取 %d 页",
                    task_id,
                    progress,
                    f"{task['written']:,}",
                    task["pages"],
                )
    except KeyboardInterrupt:
        store.set_status(task_id, "paused", "用户中断，可通过 resume 继续")
        raise
    except Exception as exc:
        # Never persist HTTP exception strings: URLs may contain API credentials.
        error = str(exc) if isinstance(exc, CrawlError) else type(exc).__name__
        store.set_status(task_id, "failed", error)
        raise
    task = store.task(task_id)
    task["this_run"] = {
        "matched": task["matched"] - initial_matched,
        "written": task["written"] - initial_written,
        "pages": task["pages"] - initial_pages,
    }
    return task
