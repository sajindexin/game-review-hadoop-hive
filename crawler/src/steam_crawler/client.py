from __future__ import annotations

import json
import logging
import os
import random
import socket
import ssl
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import truststore

from .models import CrawlError, Page, Query, item_id, item_timestamp

LOGGER = logging.getLogger(__name__)
API_BASE = "https://api.steampowered.com"


class SteamClient:
    def __init__(
        self,
        *,
        interval: float = 2,
        jitter: float = 0.5,
        timeout: float = 30,
        retries: int = 4,
        proxy: str | None = None,
        api_key: str | None = None,
        user_agent: str = "steam-feedback-crawler/0.1 (+academic data collection)",
        ca_bundle: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.interval = interval
        self.jitter = jitter
        self.retries = retries
        self.api_key = api_key
        self._last_request: float | None = None
        ca_file = ca_bundle or os.environ.get("SSL_CERT_FILE")
        ca_dir = os.environ.get("SSL_CERT_DIR")
        if ca_file or ca_dir:
            context = ssl.create_default_context(cafile=ca_file, capath=ca_dir)
        else:
            context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:
            self.http = httpx.Client(
                timeout=timeout,
                proxy=proxy,
                transport=transport,
                headers={"User-Agent": user_agent},
                follow_redirects=False,
                verify=context,
            )
        except (ValueError, httpx.InvalidURL) as exc:
            raise CrawlError("网络客户端配置无效；请检查代理地址、环境变量和 User-Agent") from exc

    def __enter__(self) -> SteamClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.http.close()

    def _pace(self) -> None:
        if self._last_request is not None:
            delay = self.interval + random.uniform(0, self.jitter)
            remaining = delay - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request = time.monotonic()

    @staticmethod
    def _network_reason(exc: httpx.TransportError) -> str:
        cause: BaseException | None = exc
        visited: set[int] = set()
        while cause is not None and id(cause) not in visited:
            visited.add(id(cause))
            if isinstance(cause, ssl.SSLCertVerificationError):
                return "TLS 证书校验失败；请检查系统证书或 SSL_CERT_FILE 配置"
            if isinstance(cause, socket.gaierror):
                return "DNS 解析失败；请检查网络或配置 --proxy"
            if isinstance(cause, OSError) and getattr(cause, "winerror", None) == 10013:
                return "连接被系统拒绝（WinError 10013）；请检查网络权限或防火墙"
            if isinstance(cause, ConnectionRefusedError):
                return "连接被拒绝；请检查网络和代理地址"
            cause = cause.__cause__ or cause.__context__
        if isinstance(exc, httpx.TimeoutException):
            return f"网络请求超时（{type(exc).__name__}）；可调整 --timeout 或配置 --proxy"
        if isinstance(exc, httpx.ProxyError):
            return "代理连接失败；请检查 --proxy 或代理环境变量"
        return f"网络请求失败（{type(exc).__name__}）；请检查网络或配置 --proxy"

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if not value:
            return 0
        try:
            return max(0, float(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                return 0

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            self._pace()
            delay = 0.0
            try:
                response = self.http.get(API_BASE + path, params=params)
            except httpx.TransportError as exc:
                reason = self._network_reason(exc)
            else:
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise CrawlError("Steam 返回了非 JSON 内容，任务进度已保留") from exc
                    if not isinstance(data, dict):
                        raise CrawlError("Steam 响应格式异常：预期 JSON 对象")
                    return data
                reason = f"Steam 返回 HTTP {response.status_code}"
                if response.status_code not in (408, 429) and response.status_code < 500:
                    raise CrawlError(reason)
                delay = self._retry_after(response.headers.get("Retry-After"))
            if attempt == self.retries:
                raise CrawlError(f"{reason}，已达到重试上限")
            delay = max(delay, min(120, 2 ** (attempt + 1)))
            LOGGER.warning("%s；%.1f 秒后重试（%d/%d）", reason, delay, attempt + 1, self.retries)
            time.sleep(delay)
        raise AssertionError("unreachable")

    def fetch(self, query: Query, position: dict[str, Any]) -> Page:
        if query.source == "reviews":
            return self._reviews(query, position)
        return self._announcements(query, position)

    def _reviews(self, query: Query, position: dict[str, Any]) -> Page:
        body: dict[str, Any] = {
            "appid": query.appid,
            "filter": 1 if query.time_field == "created" else 2,
            "languages": query.languages,
            "cursor": position["cursor"],
            "num_per_page": query.page_size,
            "review_type": {"all": 0, "positive": 1, "negative": 2}[query.review_type],
            "purchase_type": {"steam": 0, "all": 1, "non-steam": 2}[query.purchase_type],
            "filter_offtopic_activity": not query.include_offtopic,
        }
        if query.time_field == "created":
            body.update(date_range_start=query.since, date_range_end=query.until - 1)
        for field in ("min", "max"):
            value = getattr(query, f"{field}_playtime_hours")
            if value is not None:
                body[f"playtime_{field}_hours"] = value
        params = {"input_json": json.dumps(body, separators=(",", ":"))}
        if self.api_key:
            params["key"] = self.api_key
        data = self._get("/IUserReviewsService/GetAppReviews/v1/", params)
        data = data.get("response", data)
        if not isinstance(data, dict):
            raise CrawlError("评价接口响应缺少 reviews 数组，未将异常响应视为采集完成")
        items = data.get("reviews")
        cursor = data.get("cursor")
        summary = data.get("query_summary")
        if "reviews" not in data and isinstance(summary, dict):
            page_count = summary.get("num_reviews")
            matching = data.get("total_matching")
            if type(page_count) is int and page_count == 0 and type(matching) is int:
                total_reviews = summary.get("total_reviews")
                # Empty queries report zero totals. On the final empty page of a
                # nonempty query, Steam keeps the input cursor and omits total_reviews.
                no_matches = matching == 0 and type(total_reviews) is int and total_reviews == 0
                end_of_results = (
                    matching > 0
                    and "total_reviews" not in summary
                    and isinstance(cursor, str)
                    and bool(cursor)
                    and cursor == position["cursor"]
                    and position != query.initial_position()
                )
                if no_matches or end_of_results:
                    items = []
        if not isinstance(items, list):
            raise CrawlError("评价接口响应缺少 reviews 数组，未将异常响应视为采集完成")
        self._validate_items(query, items)
        exhausted = not items or min(item_timestamp(query, item) for item in items) < query.since
        if not exhausted and (not isinstance(cursor, str) or not cursor):
            raise CrawlError("评价接口缺少下一页 cursor，无法安全续采")
        if not exhausted and cursor == position["cursor"]:
            raise CrawlError("评价接口游标未推进，已停止以避免无限循环")
        return Page(items, {"cursor": cursor or position["cursor"]}, exhausted)

    def _announcements(self, query: Query, position: dict[str, Any]) -> Page:
        params: dict[str, Any] = {
            "appid": query.appid,
            "maxlength": 0,
            "count": position["count"],
            "enddate": position["enddate"],
        }
        if query.feeds:
            params["feeds"] = ",".join(query.feeds)
        data = self._get("/ISteamNews/GetNewsForApp/v2/", params).get("appnews")
        if not isinstance(data, dict) or not isinstance(data.get("newsitems"), list):
            raise CrawlError("公告接口响应缺少 appnews.newsitems 数组")
        if data.get("appid") != query.appid:
            raise CrawlError("公告接口返回的 appid 与请求不一致")
        items = data["newsitems"]
        self._validate_items(query, items)
        if not items:
            return Page([], position, True)
        oldest = min(item_timestamp(query, item) for item in items)
        if oldest < query.since:
            return Page(items, position, True)
        # Re-read the boundary second so equal-timestamp entries are not silently skipped.
        enddate = min(position["enddate"], oldest + 1)
        count = query.page_size
        if enddate == position["enddate"]:
            if len(items) >= position["count"]:
                if position["count"] >= 1000:
                    raise CrawlError("公告同秒记录超出可安全分页范围，无法保证完整性；请缩小范围")
                count = min(1000, position["count"] * 2)
            else:
                # Handle both strict and inclusive enddate implementations. Only
                # subtract a second after seeing a record exactly at the bound.
                enddate = oldest - 1 if oldest == position["enddate"] else oldest
                if enddate < 0:
                    return Page(items, position, True)
        return Page(items, {"enddate": enddate, "count": count}, False)

    @staticmethod
    def _validate_items(query: Query, items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                raise CrawlError("Steam 返回的记录不是 JSON 对象")
            item_id(query, item)
            item_timestamp(query, item)
