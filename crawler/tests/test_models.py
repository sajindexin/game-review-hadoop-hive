from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import announcement, review

from steam_crawler.cli import build_parser, main, make_queries
from steam_crawler.models import Query, matches, normalize_languages, parse_boundary


def test_date_end_includes_whole_day_and_shanghai_timezone():
    start = parse_boundary("2026-11-19", "Asia/Shanghai")
    end = parse_boundary("2026-11-19", "Asia/Shanghai", end=True)
    assert datetime.fromtimestamp(start, UTC).isoformat() == "2026-11-18T16:00:00+00:00"
    assert end - start == 86400


def test_datetime_end_is_exclusive_and_explicit_offset_wins():
    explicit = parse_boundary("2026-11-19T12:00:00+08:00", "UTC", end=True)
    assert explicit == parse_boundary("2026-11-19T04:00:00Z", "Asia/Shanghai")


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "2026-11-19T00:00:00.1Z"])
def test_invalid_dates_are_rejected(value):
    with pytest.raises(ValueError):
        parse_boundary(value, "UTC")


def test_invalid_timezone_is_rejected_even_without_date():
    with pytest.raises(ValueError, match="未知时区"):
        parse_boundary(None, "Does/NotExist")


def test_language_aliases_and_deduplication():
    assert normalize_languages(["zh-CN,en", "schinese"]) == ["english", "schinese"]
    with pytest.raises(ValueError):
        normalize_languages(["all,english"])


def test_exact_time_bounds_and_language_filter(query):
    assert matches(query, review(1, created=100))
    assert not matches(query, review(1, created=1000))
    assert not matches(query, review(1, created=99))
    assert not matches(query, review(1, language="english"))


def test_updated_range_keeps_old_created_review(query):
    query.time_field = "updated"
    assert matches(query, review(1, created=1, updated=500))


def test_news_keeps_all_announcement_types_and_respects_explicit_filters():
    query = Query(source="announcements", appid=570, until=1000)
    assert matches(query, announcement(1))
    assert matches(query, announcement(2, title="直播", contents="促销信息"))
    assert not matches(query, announcement(3, feedname="external_media"))
    query.keywords = ["CRASH"]
    assert matches(query, announcement(1))
    query.exclude_keywords = ["fixed"]
    assert not matches(query, announcement(1))


def test_cli_multiple_apps_languages_and_day_end():
    args = build_parser().parse_args(
        [
            "reviews",
            "--app-id",
            "570",
            "--app-id",
            "730",
            "--app-id",
            "570",
            "--language",
            "schinese,english",
            "--since",
            "2026-01-01",
            "--until",
            "2026-01-31",
        ]
    )
    queries = make_queries(args)
    assert [query.appid for query in queries] == [570, 730]
    assert queries[0].languages == ["english", "schinese"]
    assert queries[0].until - queries[0].since == 31 * 86400


@pytest.mark.parametrize(
    "arguments",
    [
        ["--since", "2026-12-01", "--until", "2026-01-01"],
        ["--page-size", "101"],
        ["--language", "all,english"],
        ["--min-playtime-hours", "10", "--max-playtime-hours", "2"],
    ],
)
def test_invalid_query_fails_before_creating_files(arguments, tmp_path):
    root = tmp_path / "not-created"
    with pytest.raises(SystemExit) as exc:
        main(["reviews", "--app-id", "570", "--output", str(root), *arguments])
    assert exc.value.code == 2
    assert not root.exists()


@pytest.mark.parametrize(
    "option,value",
    [
        ("--max-items", "-1"),
        ("--interval", "nan"),
        ("--timeout", "0"),
        ("--jitter", "inf"),
        ("--app-id", "0"),
    ],
)
def test_invalid_numeric_arguments(option, value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["reviews", "--app-id", "570", option, value])
