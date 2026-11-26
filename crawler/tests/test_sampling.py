from __future__ import annotations

import gzip
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import review

from steam_crawler import cli
from steam_crawler.client import SteamClient
from steam_crawler.models import Query, parse_boundary
from steam_crawler.sampling import SamplePlan


def test_local_day_length_and_equal_quota():
    zone = "America/New_York"
    query = Query(
        "reviews",
        730,
        since=parse_boundary("2025-03-08T12:00:00-05:00", zone),
        until=parse_boundary("2025-03-10T00:00:00-04:00", zone),
    )
    duration = SamplePlan(query, zone, 35, "duration", "all", True).days()
    assert [(day.date, day.quota) for day in duration] == [("2025-03-09", 23), ("2025-03-08", 12)]
    assert duration[0].since == duration[1].until
    assert duration[0].until - duration[0].since == 23 * 3600
    equal = SamplePlan(query, zone, 35, "equal", "all", True).days()
    assert [day.quota for day in equal] == [17, 18]


@pytest.mark.parametrize(
    "fill,compressed,counts",
    [
        ("none", False, [4, 4, 1]),
        ("month", True, [4, 6, 1]),
        ("all", True, [5, 6, 1]),
    ],
)
def test_sample_preview_resume_and_refill(tmp_path, monkeypatch, capsys, fill, compressed, counts):
    capacity = {"2026-02-01": 9, "2026-01-31": 6, "2026-01-30": 1}
    zone = ZoneInfo("Asia/Shanghai")
    requests = []

    def handle(request):
        body = json.loads(request.url.params["input_json"])
        assert body["appid"] == 730 and body["filter"] == 1
        assert body["languages"] == ["english", "schinese", "tchinese"]
        assert body["filter_offtopic_activity"] is True
        start, end = body["date_range_start"], body["date_range_end"]
        date = datetime.fromtimestamp(start, zone).date().isoformat()
        assert end + 1 - start == 86400
        requests.append((date, body["cursor"]))
        if body["cursor"] == "*":
            items = [
                review(end - i, created=end - i, language="english" if i % 2 else "schinese")
                for i in range(capacity[date])
            ]
            # An out-of-range entry and the duplicate exercise local filtering and deduplication.
            if date == "2026-02-01":
                items[:0] = [review(end + 1, created=end + 1), items[0]]
        else:
            items = []
        return httpx.Response(200, json={"response": {"reviews": items, "cursor": "next"}})

    monkeypatch.setattr(
        cli,
        "SteamClient",
        lambda **kwargs: SteamClient(**kwargs, transport=httpx.MockTransport(handle)),
    )
    opts = ["--output", str(tmp_path), "--json"]
    start_args = [
        "sample-reviews",
        "--app-id",
        "730",
        "--since",
        "2026-01-30",
        "--until",
        "2026-02-01",
        "--total",
        "12",
        "--language",
        "zh-CN,zh-TW,en",
        "--fill",
        fill,
        "--exclude-offtopic",
    ]
    if compressed:
        start_args.append("--gzip")
    assert cli.main([*start_args, "--plan-only", *opts]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "planned"
    assert not requests
    plan = json.loads((tmp_path / "sampling-plan.json").read_text(encoding="utf-8"))
    assert [day["quota"] for day in plan["days"]] == [4, 4, 4]
    local = ["--interval", "0", "--jitter", "0", *opts]
    assert cli.main(["resume-sample", "--max-pages", "1", *local]) == 0
    partial = json.loads(capsys.readouterr().out)
    assert (partial["status"], partial["written"], partial["pages_this_run"]) == ("paused", 4, 1)
    assert requests == [("2026-02-01", "*")]
    assert cli.main(["status", "--sample", *opts]) == 0
    assert json.loads(capsys.readouterr().out) == partial

    total = sum(counts)
    assert cli.main(["resume-sample", *local]) == (0 if total == 12 else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["written"] == total and result["missing"] == 12 - total
    assert result["status"] == ("completed" if total == 12 else "shortfall")
    assert requests.count(("2026-02-01", "*")) == 1
    with gzip.open(tmp_path / result["result_file"], "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    stamps = [row["payload"]["timestamp_created"] for row in rows]
    assert len(set(stamps)) == len(stamps) == total
    assert stamps == sorted(stamps, reverse=True)
    summary = json.loads((tmp_path / "sample/summary.json").read_text(encoding="utf-8"))
    assert [day["written"] for day in summary["days"]] == counts
    assert sum(summary["language_counts"].values()) == total


def test_page_limit_pauses_even_inside_only_day(tmp_path, monkeypatch, capsys):
    def handle(request):
        body = json.loads(request.url.params["input_json"])
        end = body["date_range_end"]
        first = body["cursor"] == "*"
        item = review(1 if first else 2, created=end if first else end - 1)
        return httpx.Response(
            200, json={"response": {"reviews": [item], "cursor": "second" if first else "third"}}
        )

    monkeypatch.setattr(
        cli,
        "SteamClient",
        lambda **kwargs: SteamClient(**kwargs, transport=httpx.MockTransport(handle)),
    )
    opts = ["--output", str(tmp_path), "--json", "--interval", "0", "--max-pages", "1"]
    assert (
        cli.main(
            [
                "sample-reviews",
                "--app-id",
                "730",
                "--since",
                "2026-01-30",
                "--until",
                "2026-01-30",
                "--total",
                "2",
                "--fill",
                "none",
                *opts,
            ]
        )
        == 0
    )
    first = json.loads(capsys.readouterr().out)
    assert (first["status"], first["written"]) == ("paused", 1)
    assert cli.main(["resume-sample", *opts]) == 0
    final = json.loads(capsys.readouterr().out)
    assert (final["status"], final["written"]) == ("completed", 2)
