from __future__ import annotations

import json

import httpx
import pytest
from conftest import announcement, read_records, review
from filelock import FileLock

from steam_crawler import cli
from steam_crawler.client import SteamClient


def install_client(monkeypatch, handler):
    monkeypatch.setattr(
        cli,
        "SteamClient",
        lambda **kwargs: SteamClient(**kwargs, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "offtopic_options,filter_offtopic",
    [([], False), (["--exclude-offtopic"], True)],
)
def test_cli_collect_resume_and_status_json(
    tmp_path, monkeypatch, capsys, offtopic_options, filter_offtopic
):
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.url.params["input_json"])
        assert body["languages"] == ["all"]
        assert body["filter_offtopic_activity"] is filter_offtopic
        items = (
            [review(1), review(2, language="english"), review(3, language="russian")]
            if body["cursor"] == "*"
            else []
        )
        return httpx.Response(200, json={"response": {"reviews": items, "cursor": "n"}})

    install_client(monkeypatch, handle)
    args = ["--output", str(tmp_path), "--json"]
    review_args = ["reviews", "--app-id", "570", "--max-items", "2", *offtopic_options]
    assert cli.main([*review_args, *args]) == 0
    [task] = json.loads(capsys.readouterr().out)
    assert task["status"] == "paused"
    assert cli.main(["status", "--task-id", task["task_id"], *args]) == 0
    [status] = json.loads(capsys.readouterr().out)
    assert status["pending_items"] == 1
    assert cli.main(["resume", task["task_id"], "--max-items", "0", *args]) == 0
    [resumed] = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "completed"
    assert resumed["written"] == 3
    assert len(calls) == 2
    assert {record["payload"]["language"] for record in read_records(tmp_path)} == {
        "schinese",
        "english",
        "russian",
    }


def test_cli_news_preserves_raw_and_does_not_invent_effective_time(tmp_path, monkeypatch, capsys):
    items = [
        announcement(1, tags=["patchnotes"]),
        announcement(2, title="直播", contents="促销信息"),
        announcement(3, title="Monthly Newsletter", contents="News from the team."),
    ]
    data = {"appnews": {"appid": 570, "newsitems": items}}
    install_client(monkeypatch, lambda _: httpx.Response(200, json=data))
    assert (
        cli.main(
            [
                "news",
                "--app-id",
                "570",
                "--max-items",
                "3",
                "--output",
                str(tmp_path),
                "--json",
            ]
        )
        == 0
    )
    [task] = json.loads(capsys.readouterr().out)
    assert task["written"] == 3
    records = read_records(tmp_path)
    assert [record["payload"] for record in records] == items
    for record in records:
        assert record["annotations"] == {
            "announced_at": 500,
            "effective_at": None,
            "time_basis": "announcement_published",
        }
        assert "updates_only" not in record["query"]


@pytest.mark.parametrize("options", [["--language", "schinese"], ["--updates-only"]])
def test_cli_news_rejects_unsupported_options(options):
    with pytest.raises(SystemExit) as exc:
        cli.main(["news", "--app-id", "570", *options])
    assert exc.value.code == 2


def test_cli_directory_writer_lock(tmp_path, capsys):
    with FileLock(tmp_path / ".crawler.lock"):
        assert cli.main(["reviews", "--app-id", "570", "--output", str(tmp_path)]) == 1
    assert "已有采集程序" in capsys.readouterr().err


def test_status_without_database_does_not_create_output(tmp_path, capsys):
    root = tmp_path / "not-created"
    assert cli.main(["status", "--output", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert not root.exists()


def test_cli_failure_reports_saved_task(tmp_path, monkeypatch, capsys):
    install_client(monkeypatch, lambda _: httpx.Response(403))
    assert (
        cli.main(
            [
                "reviews",
                "--app-id",
                "570",
                "--output",
                str(tmp_path),
                "--json",
            ]
        )
        == 1
    )
    [task] = json.loads(capsys.readouterr().out)
    assert task["status"] == "failed"
    assert "403" in task["error"]
