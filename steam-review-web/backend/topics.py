"""主题分析、情感统计和改动反馈的 Hive 查询（第四部分）。

数据源：nlp-topic-sentiment 模块产出的结果表（nlp_topic 库，
Parquet 入库脚本见 nlp-topic-sentiment/data/result/IMPORT_GUIDE.md）。

端点一览（均 GET，返回 (payload, cacheable) 中的 payload）：
  /api/nlp/stats/distribution?lang=zh|en              11 类主题 × 情感 计数
  /api/nlp/stats/summary?appid=&lang=zh|en            评论级情感汇总（命中率、正负面）
  /api/nlp/topics/trend?appid=&aspect=&lang=&days=    主题情感按日趋势
  /api/nlp/items/feedback?appid=&item_id=             改动反馈：条目情感 + 版本窗口计数
  /api/nlp/reviews?appid=&aspect=&sentiment=&lang=&limit=&offset=   引文明细（带 evidence）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from urllib.parse import parse_qs

from .common import ApiError


ROUTE = re.compile(
    r"/api/nlp/(?:stats/(distribution|summary)"
    r"|topics/trend"
    r"|items/feedback"
    r"|reviews)/?"
)
ASPECTS = (
    "content", "level_design", "multiplayer", "graphics", "performance",
    "story", "controls", "monetization", "cheating", "audio", "community",
)
SENTIMENTS = ("positive", "negative", "neutral")
ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
DATABASE = "nlp_topic"
CACHE_TTL = 60
QUERY_KEYS = {
    "distribution": {"lang"},
    "summary": {"appid", "lang"},
    "trend": {"appid", "aspect", "lang", "days"},
    "feedback": {"appid", "item_id"},
    "reviews": {"appid", "aspect", "sentiment", "lang", "limit", "offset"},
}
# 中文主表 / 英文 _en 表；表名由 lang 参数选择，不接受自由拼接
_DETAIL_TABLES = {"zh": "topic_sentiment_detail", "en": "topic_sentiment_detail_en"}
_SUMMARY_TABLES = {"zh": "topic_sentiment_review_summary", "en": "topic_sentiment_review_summary_en"}
_DISTRIBUTION_TABLES = {"zh": "topic_distribution", "en": "topic_distribution_en"}


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


def _enum(params: dict[str, str], name: str, allowed, default=None):
    value = params.get(name, default)
    if value not in allowed:
        raise ApiError(400, "invalid_request", f"{name} 应为 {' / '.join(allowed)} 之一")
    return value


def _json_value(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class TopicAPI:
    """通过项目公共客户端读取 NLP 结果表（第四部分）。"""

    def __init__(self, get_client):
        self.get_client = get_client

    def get(self, path: str, query: str) -> dict:
        route = ROUTE.fullmatch(path)
        if route is None:
            raise ApiError(404, "not_found", "主题情感接口不存在")
        endpoint = route.group(1) or ("trend" if path.startswith("/api/nlp/topics/") else
                                      "feedback" if path.startswith("/api/nlp/items/") else "reviews")
        params = _query_parameters(query, endpoint)
        handler = {
            "distribution": self._distribution,
            "summary": self._summary,
            "trend": self._trend,
            "feedback": self._feedback,
            "reviews": self._reviews,
        }[endpoint]
        return handler(params)

    # ---------------------------------------------------------------- helpers

    def _rows(self, sql: str, params=(), ttl: int = CACHE_TTL) -> list[dict]:
        rows = self.get_client().query(sql, params or None, max_rows=10000, cache_ttl=ttl)
        return [{name: _json_value(value) for name, value in row.items()} for row in rows]

    def _detail_table(self, lang: str) -> str:
        return f"{DATABASE}.{_DETAIL_TABLES[lang]}"

    def _summary_table(self, lang: str) -> str:
        return f"{DATABASE}.{_SUMMARY_TABLES[lang]}"

    @staticmethod
    def _group(rows: list[dict], key_fields: tuple[str, ...], value_name: str = "cnt") -> list[dict]:
        """按 key_fields 聚合成 {**keys, value}，保持稳定排序。"""
        merged: dict[tuple, dict] = {}
        for row in rows:
            key = tuple(row[name] for name in key_fields)
            entry = merged.setdefault(key, {**{name: row[name] for name in key_fields}, value_name: 0})
            entry[value_name] += row.get(value_name, 0)
        return sorted(merged.values(), key=lambda item: tuple(item[name] for name in key_fields))

    # ---------------------------------------------------------------- 端点

    def _distribution(self, params: dict[str, str]) -> dict:
        lang = _enum(params, "lang", ("zh", "en"), "zh")
        table = f"{DATABASE}.{_DISTRIBUTION_TABLES[lang]}"
        rows = self._rows(f"SELECT aspect, name_zh, sentiment, count FROM {table}")
        return {
            "source": "hive", "lang": lang,
            "distribution": self._group(rows, ("aspect", "name_zh", "sentiment"), "count"),
        }

    def _summary(self, params: dict[str, str]) -> dict:
        lang = _enum(params, "lang", ("zh", "en"), "zh")
        appid = _number(params, "appid", 1, 2147483647)
        table = self._summary_table(lang)
        where, sql_params = ("WHERE appid = %s", [appid]) if appid is not None else ("", [])
        totals = self._rows(
            f"SELECT count(*) AS reviews, "
            f"sum(CASE WHEN n_aspects > 0 THEN 1 ELSE 0 END) AS reviews_with_aspects, "
            f"sum(n_aspects) AS aspect_mentions, sum(pos_aspects) AS pos_aspects, "
            f"sum(neg_aspects) AS neg_aspects FROM {table} {where}", tuple(sql_params),
        )
        # count(*) 恒返回一行；空结果（异常数据/空表）时归零而不是报错
        totals = totals[0] if totals else {"reviews": 0, "reviews_with_aspects": 0,
                                           "aspect_mentions": 0, "pos_aspects": 0, "neg_aspects": 0}
        by_sentiment = self._rows(
            f"SELECT primary_sentiment, count(*) AS cnt FROM {table} {where} "
            f"GROUP BY primary_sentiment", tuple(sql_params),
        )
        return {
            "source": "hive", "lang": lang, "appid": appid,
            "totals": {k: (int(v) if v is not None else 0) for k, v in totals.items()},
            "by_primary_sentiment": {row["primary_sentiment"]: row["cnt"] for row in by_sentiment},
        }

    def _trend(self, params: dict[str, str]) -> dict:
        lang = _enum(params, "lang", ("zh", "en"), "zh")
        appid = _number(params, "appid", 1, 2147483647)
        aspect = params.get("aspect")
        days = _number(params, "days", 7, 3650, 365)
        if aspect is not None and aspect not in ASPECTS:
            raise ApiError(400, "invalid_request", f"aspect 应为 {' / '.join(ASPECTS)} 之一")
        filters = ["appid = %s"]
        sql_params: list = [appid]
        if aspect is not None:
            filters.append("aspect = %s")
            sql_params.append(aspect)
        rows = self._rows(
            f"SELECT created_date, sentiment, count(*) AS cnt FROM {self._detail_table(lang)} "
            f"WHERE {' AND '.join(filters)} GROUP BY created_date, sentiment "
            f"ORDER BY created_date LIMIT {days}", tuple(sql_params),
        )
        by_day: dict[str, dict] = {}
        for row in rows:
            entry = by_day.setdefault(row["created_date"], {"date": row["created_date"], "positive": 0, "negative": 0, "neutral": 0})
            entry[row["sentiment"]] = row["cnt"]
        return {
            "source": "hive", "lang": lang, "appid": appid, "aspect": aspect,
            "days": days, "trend": sorted(by_day.values(), key=lambda item: item["date"]),
        }

    def _feedback(self, params: dict[str, str]) -> dict:
        """改动反馈：官方更新条目的评论情感 + 版本窗口分布。

        item_id 缺省时返回该游戏全部条目的情感汇总列表；
        给定 item_id 时返回单条目详情（含与第三部分 patch_id 对齐的窗口计数）。
        """
        appid = _number(params, "appid", 1, 2147483647)
        item_id = params.get("item_id")
        if item_id is not None and not ITEM_ID.fullmatch(item_id):
            raise ApiError(400, "invalid_request", "item_id 格式不正确")

        detail = f"{DATABASE}.item_sentiment_detail"
        if item_id is None:
            rows = self._rows(
                f"SELECT item_id, item_title, aspect, sentiment, count(*) AS cnt FROM {detail} "
                f"WHERE appid = %s GROUP BY item_id, item_title, aspect, sentiment", (appid,),
            )
            items = self._group(rows, ("item_id", "item_title", "aspect", "sentiment"), "cnt")
            grouped: dict[str, dict] = {}
            for item in items:
                entry = grouped.setdefault(
                    item["item_id"],
                    {"item_id": item["item_id"], "item_title": item["item_title"],
                     "aspect": item["aspect"], "by_sentiment": {}},
                )
                entry["by_sentiment"][item["sentiment"]] = item["cnt"]
            return {
                "source": "hive", "appid": appid,
                "items": sorted(grouped.values(), key=lambda item: (-sum(item["by_sentiment"].values()), item["item_id"])),
            }

        windows = self._rows(
            f"SELECT patch_id, item_id, item_title, pre_14d, release_day, post_14d, out_of_window "
            f"FROM {DATABASE}.item_patch_summary WHERE item_id = %s", (item_id,),
        )
        by_sentiment = self._rows(
            f"SELECT sentiment, count(*) AS cnt FROM {detail} "
            f"WHERE appid = %s AND item_id = %s GROUP BY sentiment", (appid, item_id),
        )
        by_window = self._rows(
            f"SELECT window_bucket, sentiment, count(*) AS cnt FROM {detail} "
            f"WHERE appid = %s AND item_id = %s GROUP BY window_bucket, sentiment", (appid, item_id),
        )
        if not by_sentiment and not windows:
            raise ApiError(404, "item_not_found", "该游戏没有命中此条目的评论")
        return {
            "source": "hive", "appid": appid, "item_id": item_id,
            "windows": windows,  # patch_id 与第三部分 /api/versions 的版本目录对齐
            "by_sentiment": {row["sentiment"]: row["cnt"] for row in by_sentiment},
            "by_window_sentiment": self._group(by_window, ("window_bucket", "sentiment"), "cnt"),
        }

    def _reviews(self, params: dict[str, str]) -> dict:
        lang = _enum(params, "lang", ("zh", "en"), "zh")
        appid = _number(params, "appid", 1, 2147483647)
        aspect = params.get("aspect")
        sentiment = params.get("sentiment")
        limit = _number(params, "limit", 1, 500, 50)
        offset = _number(params, "offset", 0, 2147483647, 0)
        if aspect is not None and aspect not in ASPECTS:
            raise ApiError(400, "invalid_request", f"aspect 应为 {' / '.join(ASPECTS)} 之一")
        if sentiment is not None and sentiment not in SENTIMENTS:
            raise ApiError(400, "invalid_request", f"sentiment 应为 {' / '.join(SENTIMENTS)} 之一")

        filters = ["appid = %s"]
        sql_params: list = [appid]
        if aspect is not None:
            filters.append("aspect = %s")
            sql_params.append(aspect)
        if sentiment is not None:
            filters.append("sentiment = %s")
            sql_params.append(sentiment)
        sql_params.extend((limit, offset))
        rows = self._rows(
            f"SELECT review_id, game_name, created_date, aspect, name_zh, sentiment, "
            f"confidence, sentiment_word, evidence FROM {self._detail_table(lang)} "
            f"WHERE {' AND '.join(filters)} "
            f"ORDER BY confidence DESC, review_id LIMIT %s OFFSET %s", tuple(sql_params),
        )
        return {
            "source": "hive", "lang": lang, "appid": appid,
            "filters": {"aspect": aspect, "sentiment": sentiment},
            "offset": offset, "limit": limit, "reviews": rows,
        }
