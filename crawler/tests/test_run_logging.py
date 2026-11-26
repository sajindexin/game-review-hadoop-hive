from __future__ import annotations

import json

import httpx
import pytest
from conftest import review

from steam_crawler import cli
from steam_crawler.client import SteamClient


@pytest.mark.parametrize(
    "terminal_options",
    [
        pytest.param([], id="stdout"),
        pytest.param(["--json"], id="json"),
        pytest.param(["--quiet"], id="quiet"),
    ],
)
def test_collection_logs_paths_and_sampling_progress(
    tmp_path, monkeypatch, capsys, terminal_options
):
    def handle(request):
        body = json.loads(request.url.params["input_json"])
        end = body["date_range_end"]
        return httpx.Response(
            200,
            json={"response": {"reviews": [review(1, created=end)], "cursor": "next"}},
        )

    monkeypatch.setattr(
        cli,
        "SteamClient",
        lambda **kwargs: SteamClient(**kwargs, transport=httpx.MockTransport(handle)),
    )
    root = tmp_path / "含空格 output"
    opts = ["--output", str(root), "--interval", "0", "--jitter", "0", *terminal_options]
    args = [
        "sample-reviews",
        "--app-id",
        "730",
        "--since",
        "2026-11-19",
        "--until",
        "2026-11-19",
        "--total",
        "1",
        *opts,
    ]
    assert cli.main(args) == 0
    terminal = capsys.readouterr()
    log_path = root / "collection.log"
    log = log_path.read_text(encoding="utf-8")
    assert f"输出目录：{root}" in log and f"日志文件：{log_path}" in log
    assert "采样总进度 1/1 (100.00%)；2026-11-19 当日 1/1 条" in log
    if "--json" in terminal_options:
        assert terminal.err == log
        result = json.loads(terminal.out)
        assert result["output_dir"] == str(root) and result["log_file"] == str(log_path)
    elif "--quiet" in terminal_options:
        assert terminal.err == ""
        assert "采样 [completed]" in terminal.out and "采样总进度" not in terminal.out
    else:
        assert terminal.err == ""
        assert terminal.out.startswith(log)
