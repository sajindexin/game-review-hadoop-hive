from __future__ import annotations

import json

import httpx
import pytest
from conftest import announcement, review

from steam_crawler.client import SteamClient
from steam_crawler.models import CrawlError, Query


def test_new_review_api_enums_filters_and_response_wrapper(query):
    def handle(request):
        assert request.url.path == "/IUserReviewsService/GetAppReviews/v1/"
        body = json.loads(request.url.params["input_json"])
        assert body["filter"] == 1
        assert body["languages"] == ["schinese"]
        assert body["date_range_start"] == 100
        assert body["date_range_end"] == 999
        assert body["purchase_type"] == 1
        assert body["num_per_page"] == 3
        assert body["filter_offtopic_activity"] is False
        return httpx.Response(200, json={"response": {"reviews": [review(1)], "cursor": "next/+="}})

    with SteamClient(transport=httpx.MockTransport(handle), interval=0, jitter=0) as client:
        page = client.fetch(query, query.initial_position())
    assert page.next_position == {"cursor": "next/+="}
    assert not page.exhausted


def test_updated_sort_never_sends_created_date_filters(query):
    query.time_field = "updated"

    def handle(request):
        body = json.loads(request.url.params["input_json"])
        assert body["filter"] == 2
        assert "date_range_start" not in body
        assert "date_range_end" not in body
        return httpx.Response(
            200, json={"reviews": [review(1, created=1, updated=500)], "cursor": "n"}
        )

    with SteamClient(transport=httpx.MockTransport(handle)) as client:
        assert len(client.fetch(query, query.initial_position()).items) == 1


def test_rate_limit_retry_after_and_credentials_not_logged(monkeypatch, caplog, query):
    sleeps = []
    monkeypatch.setattr("steam_crawler.client.time.sleep", sleeps.append)
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.params["key"] == "secret-test-key"
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"reviews": []})

    with SteamClient(
        transport=httpx.MockTransport(handle),
        interval=0,
        jitter=0,
        api_key="secret-test-key",
    ) as client:
        assert client.fetch(query, query.initial_position()).exhausted
    assert sleeps == [7]
    assert "secret-test-key" not in caplog.text


def test_nonretryable_http_error(query):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(403)

    with SteamClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(CrawlError, match="HTTP 403"):
            client.fetch(query, query.initial_position())
    assert len(requests) == 1


@pytest.mark.parametrize(
    "response",
    [
        {"error": "denied"},
        {"reviews": "invalid"},
        {"response": {}},
        {
            "response": {
                "total_matching": 6,
                "query_summary": {"num_reviews": 0, "total_reviews": 6},
            }
        },
        {"reviews": [{"recommendationid": "1"}], "cursor": "n"},
    ],
)
def test_malformed_review_response_is_not_successful_empty_page(query, response):
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(CrawlError):
            client.fetch(query, query.initial_position())


def test_review_summary_with_zero_matches_can_omit_reviews(query):
    response = {
        "response": {
            "cursor": "*",
            "total_matching": 0,
            "query_summary": {"num_reviews": 0, "total_reviews": 0},
        }
    }
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        page = client.fetch(query, query.initial_position())
    assert page.items == [] and page.exhausted


@pytest.mark.parametrize(
    ("input_cursor", "output_cursor", "exhausted"),
    [("last", "last", True), ("last", "next", False), ("*", "*", False)],
)
def test_review_final_empty_page_can_omit_reviews(query, input_cursor, output_cursor, exhausted):
    response = {
        "response": {
            "cursor": output_cursor,
            "total_matching": 270,
            "query_summary": {"num_reviews": 0},
        }
    }
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        if exhausted:
            page = client.fetch(query, {"cursor": input_cursor})
            assert page.items == [] and page.exhausted
        else:
            with pytest.raises(CrawlError, match="缺少 reviews 数组"):
                client.fetch(query, {"cursor": input_cursor})


def test_unchanged_review_cursor_is_rejected(query):
    response = {"reviews": [review(1)], "cursor": "*"}
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(CrawlError, match="游标未推进"):
            client.fetch(query, query.initial_position())


def test_news_full_contents_and_boundary_overlap():
    query = Query(source="announcements", appid=570, since=100, until=1000, page_size=2)

    def handle(request):
        assert request.url.params["maxlength"] == "0"
        assert request.url.params["feeds"] == "steam_community_announcements"
        return httpx.Response(
            200,
            json={
                "appnews": {
                    "appid": 570,
                    "newsitems": [announcement(1, published=600), announcement(2, published=500)],
                }
            },
        )

    with SteamClient(transport=httpx.MockTransport(handle)) as client:
        page = client.fetch(query, query.initial_position())
    assert page.next_position == {"enddate": 501, "count": 2}


def test_news_same_second_full_page_expands_instead_of_skipping():
    query = Query(source="announcements", appid=570, until=1000, page_size=2)
    data = {"appnews": {"appid": 570, "newsitems": [announcement(1), announcement(2)]}}
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        page = client.fetch(query, {"enddate": 501, "count": 2})
    assert page.next_position == {"enddate": 501, "count": 4}


def test_news_same_second_short_page_can_advance():
    query = Query(source="announcements", appid=570, until=1000, page_size=2)
    data = {"appnews": {"appid": 570, "newsitems": [announcement(1), announcement(2)]}}
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        page = client.fetch(query, {"enddate": 501, "count": 4})
    assert page.next_position == {"enddate": 500, "count": 2}


def test_news_tie_limit_fails_explicitly():
    query = Query(source="announcements", appid=570, until=1000, page_size=1000)
    data = {"appnews": {"appid": 570, "newsitems": [announcement(i) for i in range(1000)]}}
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        with pytest.raises(CrawlError, match="完整性"):
            client.fetch(query, {"enddate": 501, "count": 1000})


def test_news_inclusive_enddate_can_advance_without_looping():
    query = Query(source="announcements", appid=570, until=1000, page_size=4)
    data = {"appnews": {"appid": 570, "newsitems": [announcement(1), announcement(2)]}}
    with SteamClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))
    ) as client:
        page = client.fetch(query, {"enddate": 500, "count": 4})
    assert page.next_position == {"enddate": 499, "count": 4}


def test_invalid_proxy_does_not_expose_credentials():
    with pytest.raises(CrawlError) as exc:
        SteamClient(proxy="unknown://username:secret-test-password@localhost:9999")
    assert "secret-test-password" not in str(exc.value)
