from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from steam_crawler.models import Query


@pytest.fixture
def query() -> Query:
    return Query(
        source="reviews", appid=570, since=100, until=1000, page_size=3, languages=["schinese"]
    )


def review(identity: int, *, created: int = 500, updated: int | None = None, **fields: Any):
    return {
        "recommendationid": str(identity),
        "review": '更新后卡顿\n希望修复"闪退"',
        "language": "schinese",
        "timestamp_created": created,
        "timestamp_updated": updated if updated is not None else created,
        "voted_up": False,
        **fields,
    }


def announcement(identity: int, *, published: int = 500, **fields: Any):
    return {
        "gid": str(identity),
        "date": published,
        "title": "Patch notes",
        "contents": "Fixed a crash.",
        "feedname": "steam_community_announcements",
        **fields,
    }


def read_records(root: Path) -> list[dict[str, Any]]:
    import gzip

    records = []
    for path in sorted((root / "raw").rglob("*.jsonl*")):
        if path.name.endswith(".tmp"):
            continue
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            records.extend(json.loads(line) for line in stream)
    return records
