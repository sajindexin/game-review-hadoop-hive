from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CrawlError(Exception):
    """An actionable collection or storage error."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_boundary(value: str | None, timezone: str, *, end: bool = False) -> int | None:
    """Dates include the entire end day; datetime end bounds are exclusive."""
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"未知时区：{timezone}") from exc
    if value is None:
        return None
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            if end:
                day += timedelta(days=1)
            parsed = datetime.combine(day, datetime.min.time(), tzinfo=zone)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zone)
        if parsed.microsecond:
            raise ValueError("时间精度最多到秒")
        result = int(parsed.timestamp())
        if not 0 <= result <= 4_294_967_295:
            raise ValueError("时间超出 Steam uint32 时间戳范围")
        return result
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"无效时间 {value!r}：{exc}") from exc


LANGUAGE_ALIASES = {
    "zh": "schinese",
    "zh-cn": "schinese",
    "zh-hans": "schinese",
    "中文": "schinese",
    "zh-tw": "tchinese",
    "zh-hant": "tchinese",
    "en": "english",
    "ja": "japanese",
    "ko": "koreana",
    "ru": "russian",
    "de": "german",
    "fr": "french",
}


def split_values(values: list[str] | None) -> list[str]:
    return list(
        dict.fromkeys(v.strip() for part in values or [] for v in part.split(",") if v.strip())
    )


def normalize_languages(values: list[str] | None) -> list[str]:
    languages = sorted({LANGUAGE_ALIASES.get(v.lower(), v.lower()) for v in split_values(values)})
    languages = languages or ["all"]
    if "all" in languages and len(languages) > 1:
        raise ValueError("语言 all 不能与其他语言同时使用")
    if any(not lang.isascii() or not lang.isalpha() for lang in languages):
        raise ValueError("语言请使用 Steam API 语言代码，例如 schinese、english、all")
    return languages


@dataclass
class Query:
    source: str
    appid: int
    since: int = 0
    until: int = 0
    page_size: int = 100
    languages: list[str] = field(default_factory=lambda: ["all"])
    time_field: str = "created"
    review_type: str = "all"
    purchase_type: str = "all"
    include_offtopic: bool = True
    min_playtime_hours: int | None = None
    max_playtime_hours: int | None = None
    feeds: list[str] = field(default_factory=lambda: ["steam_community_announcements"])
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Query:
        fields = data.copy()
        if fields.pop("updates_only", False):
            raise CrawlError(
                "该任务启用了已移除的更新筛选；请使用 news 新建公告采集任务，覆盖此前跳过的公告"
            )
        return cls(**fields)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def initial_position(self) -> dict[str, Any]:
        if self.source == "reviews":
            return {"cursor": "*"}
        return {"enddate": self.until, "count": self.page_size}


@dataclass
class Page:
    items: list[dict[str, Any]]
    next_position: dict[str, Any]
    exhausted: bool
    fetched_at: str = field(default_factory=utc_now)


def item_timestamp(query: Query, item: dict[str, Any]) -> int:
    key = "date" if query.source == "announcements" else f"timestamp_{query.time_field}"
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CrawlError(f"Steam 返回的记录缺少有效时间字段 {key}")
    return value


def item_id(query: Query, item: dict[str, Any]) -> str:
    key = "recommendationid" if query.source == "reviews" else "gid"
    value = item.get(key)
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value):
        raise CrawlError(f"Steam 返回的记录缺少有效 ID 字段 {key}")
    return str(value)


def matches(query: Query, item: dict[str, Any]) -> bool:
    stamp = item_timestamp(query, item)
    if not query.since <= stamp < query.until:
        return False
    if query.source == "reviews":
        text = str(item.get("review", "")).casefold()
        if "all" not in query.languages and item.get("language") not in query.languages:
            return False
    else:
        text = f"{item.get('title', '')}\n{item.get('contents', '')}".casefold()
        if query.feeds and item.get("feedname") not in query.feeds:
            return False
    if query.keywords and not any(word.casefold() in text for word in query.keywords):
        return False
    return not any(word.casefold() in text for word in query.exclude_keywords)
