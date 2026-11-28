"""版本目录、更新统计和复核引文的 Hive 查询。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
import re
from urllib.parse import parse_qs

from .common import ApiError


ROUTE = re.compile(
    r"/api/versions(?:/([A-Za-z0-9][A-Za-z0-9_-]{0,95})(?:/(changes|stats|evidence))?)?/?"
)
ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}")
PHASES = ("before", "release", "after")
BASE_FILTER = "appid = %s AND patch_id = %s"
DATABASE = "hadoop_delivery"
CACHE_TTL = 60
QUERY_KEYS = {
    "catalog": {"appid"},
    "changes": {"appid", "item_id"},
    "stats": {"appid", "language_group"},
    "evidence": {"appid", "item_id", "language_group", "phase", "limit", "offset"},
}
VERSION_COLUMNS = (
    "appid, patch_id, game, patch_name, title_zh, platform, release_date, "
    "date_precision, window_start, window_end, source_url, changelog_url, "
    "steam_news_gid, steam_announcement_url, steam_announcement_utc, selection_note"
)
COMPARISON_COLUMNS = (
    "appid, patch_id, language_group, before_reviews, before_recommended, before_rate, "
    "release_reviews, release_recommended, release_rate, after_reviews, after_recommended, "
    "after_rate, change_percentage_points"
)
ITEM_COLUMNS = (
    "appid, patch_id, item_id, title, title_en, section, description, "
    "source_url, aliases_zh, aliases_en, annotation_note"
)
EVIDENCE_COLUMNS = (
    "appid, patch_id, item_id, item_title, review_id, language_std, phase, "
    "created_ts, updated_ts, voted_up, day_offset, edited_after_window, "
    "before_review_edited_after_release, evidence, verification, source_url, steam_review_url"
)


def _query_parameters(query: str, endpoint: str) -> dict[str, str]:
    try:
        parsed = parse_qs(query, keep_blank_values=True, max_num_fields=10)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", "请求参数不支持、重复或缺值") from exc
    for name, values in parsed.items():
        if name not in QUERY_KEYS[endpoint] or len(values) != 1 or not values[0]:
            raise ApiError(400, "invalid_request", "请求参数不支持、重复或缺值")
    return {name: values[0] for name, values in parsed.items()}


def _number(params: dict[str, str], name: str, minimum: int, maximum: int, default=None):
    if name not in params:
        return default
    value = params[name]
    if not re.fullmatch(r"[0-9]{1,10}", value) or not minimum <= int(value) <= maximum:
        raise ApiError(400, "invalid_request", f"{name} 必须是 {minimum} 到 {maximum} 之间的整数")
    return int(value)


def _json_value(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _string_array(value) -> list[str]:
    if value is None:
        return []
    # HiveServer2 serializes ARRAY columns as JSON-encoded strings over Thrift.
    return json.loads(value) if isinstance(value, str) else value


def _valid_at_phase_end(row: dict, phase: str) -> bool:
    if phase == "before":
        return not row["before_review_edited_after_release"]
    if phase == "after":
        return not row["edited_after_window"]
    if phase == "release" and row["updated_ts"]:
        return row["updated_ts"] // 86400 <= row["created_ts"] // 86400
    return True


class VersionAPI:
    """通过项目公共客户端读取已验收的版本分析表。"""

    def __init__(self, get_client):
        self.get_client = get_client

    def get(self, path: str, query: str) -> dict:
        route = ROUTE.fullmatch(path)
        if route is None:
            raise ApiError(404, "not_found", "版本接口不存在")
        patch_id, action = route.groups()
        params = _query_parameters(query, action or "catalog")
        appid = _number(params, "appid", 1, 2147483647)
        item_id = params.get("item_id")
        language = params.get("language_group", "all")
        phase = params.get("phase", "all")
        limit = _number(params, "limit", 1, 500, 100)
        offset = _number(params, "offset", 0, 2147483647, 0)
        if item_id is not None and not ITEM_ID.fullmatch(item_id):
            raise ApiError(400, "invalid_request", "item_id 格式不正确")
        if not LANGUAGE.fullmatch(language):
            raise ApiError(400, "invalid_request", "language_group 格式不正确")
        if phase not in ("all", *PHASES):
            raise ApiError(400, "invalid_request", "phase 应为 all、before、release 或 after")

        versions = self._versions(appid, patch_id)
        if patch_id is None:
            return {"source": "hive", "versions": versions}
        if not versions:
            raise ApiError(404, "version_not_found", "版本不存在")
        version = versions[0]
        key = (version["appid"], patch_id)
        if action is None:
            return {"source": "hive", "version": version}
        if action == "changes" or item_id is not None:
            changes = self._changes(key, item_id)
            if item_id is not None and not changes:
                raise ApiError(404, "item_not_found", "改动条目不属于所选版本")
            if action == "changes":
                return {"source": "hive", "version": version, "changes": changes}
        if action == "stats":
            return self._stats(version, key, language)
        return self._evidence(version, key, item_id, phase, language, limit, offset)

    def _rows(self, table: str, columns: str, where: str = "", params=()) -> list[dict]:
        fields = [name.strip() for name in columns.split(",")]
        projection = ", ".join(f"{name} AS {name}" for name in fields)
        sql = f"SELECT {projection} FROM {DATABASE}.{table}"
        if where:
            sql += " WHERE " + where
        rows = self.get_client().query(sql, params or None, cache_ttl=CACHE_TTL)
        return [{name: _json_value(value) for name, value in row.items()} for row in rows]

    def _versions(self, appid: int | None, patch_id: str | None) -> list[dict]:
        filters = []
        params = []
        if appid is not None:
            filters.append("appid = %s")
            params.append(appid)
        if patch_id is not None:
            filters.append("patch_id = %s")
            params.append(patch_id)
        versions = self._rows("patches", VERSION_COLUMNS, " AND ".join(filters), tuple(params))
        if not versions:
            return []
        rows = self._rows(
            "patch_comparison", COMPARISON_COLUMNS,
            " AND ".join([*filters, "language_group = %s"]), (*params, "all"),
        )
        summaries = {(row["appid"], row["patch_id"]): row for row in rows}
        for version in versions:
            version["summary"] = summaries.get((version["appid"], version["patch_id"]))
        return sorted(versions, key=lambda row: (row["release_date"] or "", row["patch_id"]), reverse=True)

    def _changes(self, key: tuple, item_id: str | None) -> list[dict]:
        where, params = (BASE_FILTER, key) if item_id is None else (
            BASE_FILTER + " AND item_id = %s", (*key, item_id),
        )
        changes = self._rows("patch_items", ITEM_COLUMNS, where, params)
        for item in changes:
            item["aliases_zh"] = _string_array(item["aliases_zh"])
            item["aliases_en"] = _string_array(item["aliases_en"])
        return sorted(changes, key=lambda item: item["item_id"])

    def _stats(self, version: dict, key: tuple, language: str) -> dict:
        comparisons = self._rows("patch_comparison", COMPARISON_COLUMNS, BASE_FILTER, key)
        by_language = {row["language_group"]: row for row in comparisons}
        if language not in by_language:
            raise ApiError(404, "language_group_not_found", "该版本没有此语种组的统计")
        where = BASE_FILTER + " AND language_group = %s"
        params = (*key, language)
        daily = self._rows(
            "patch_daily",
            "utc_date, day_offset, reviews, recommended, recommendation_rate, edited_after_window",
            where, params,
        )
        phases = self._rows(
            "patch_phase",
            "phase, window_days, covered_days, reviews, recommended, recommendation_rate, edited_after_window",
            where, params,
        )
        return {
            "source": "hive",
            "version": version,
            "language_group": language,
            "available_language_groups": sorted(by_language),
            "daily": sorted(daily, key=lambda row: row["day_offset"]),
            "phases": sorted(phases, key=lambda row: PHASES.index(row["phase"])),
            "comparison": by_language[language],
        }

    def _evidence(self, version, key, item_id, phase, language, limit, offset) -> dict:
        filters = [BASE_FILTER]
        params = list(key)
        if item_id is not None:
            filters.append("item_id = %s")
            params.append(item_id)
        if phase != "all":
            filters.append("phase = %s")
            params.append("release_day" if phase == "release" else phase)
        if language == "chinese":
            filters.append("language_std IN (%s, %s)")
            params.extend(("zh-Hans", "zh-Hant"))
        elif language != "all":
            filters.append("language_std = %s")
            params.append(language)

        rows = self._rows("verified_quotes", EVIDENCE_COLUMNS, " AND ".join(filters), tuple(params))
        quotes = [row for row in rows if _valid_at_phase_end(row, phase)]
        for row in quotes:
            if row["phase"] == "release_day":
                row["phase"] = "release"
        quotes.sort(key=lambda row: (row["day_offset"], row["review_id"], row["item_id"]))
        return {
            "source": "hive", "version": version, "phase": phase, "language_group": language,
            "available": len(quotes), "offset": offset, "limit": limit, "quotes": quotes[offset:offset + limit],
        }
