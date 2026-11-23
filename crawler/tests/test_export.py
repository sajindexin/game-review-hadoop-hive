from __future__ import annotations

import gzip
import json
from dataclasses import replace

from conftest import announcement, review

from steam_crawler.cli import main
from steam_crawler.models import Page
from steam_crawler.sampling import RESULT_FILE
from steam_crawler.storage import Store


def save_batch(store, query, records, *, compressed=False):
    task = store.create_task(query, compressed=compressed)
    store.cache_page(task, Page(records, query.initial_position(), True))
    store.consume_page(task, None)


def test_export_common_directory_keeps_latest_versions_and_filters_app(tmp_path, query, capsys):
    root = tmp_path / "data"
    output = tmp_path / "jsonl files"
    update = review(1, review="最新正文\n保留换行")
    news = announcement(3, title="更新公告")
    with Store(root) as store:
        save_batch(store, query, [review(1), review(2)])
        save_batch(store, query, [update], compressed=True)
        save_batch(store, replace(query, appid=730), [review(2)])
        save_batch(store, replace(query, source="announcements"), [news], compressed=True)

    assert (
        main(["export", "--input", str(root), "--output", str(output), "--app-id", "570", "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    for kind, expected in [("reviews", [update, review(2)]), ("announcements", [news])]:
        path = output / f"{kind}.jsonl"
        assert result["files"][kind] == {"path": str(path), "written": len(expected)}
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert sorted((r["payload"] for r in records), key=str) == sorted(expected, key=str)
        assert all(record["appid"] == 570 for record in records)


def test_export_separate_sources_preserves_sample_and_checks_overwrite(tmp_path, query, capsys):
    review_root = tmp_path / "samples"
    news_root = tmp_path / "news"
    output = tmp_path / "out"
    sample = review_root / RESULT_FILE
    sample.parent.mkdir(parents=True)
    sample_data = "".join(
        json.dumps({"appid": 570, "payload": review(identity)}, ensure_ascii=False) + "\n"
        for identity in (2, 1)
    ).encode("utf-8")
    with gzip.open(sample, "wb") as f:
        f.write(sample_data)
    with Store(news_root) as store:
        save_batch(store, replace(query, source="announcements"), [announcement(5)])

    output.mkdir()
    news_file = output / "announcements.jsonl"
    news_file.write_bytes(b"existing file")
    args = [
        "export",
        "--reviews-from",
        str(review_root),
        "--announcements-from",
        str(news_root),
        "--output",
        str(output),
    ]
    assert main(args) == 1
    assert "--overwrite" in capsys.readouterr().err
    assert list(output.iterdir()) == [news_file]
    assert news_file.read_bytes() == b"existing file"

    assert main([*args, "--overwrite"]) == 0
    assert (output / "reviews.jsonl").read_bytes() == sample_data
    assert json.loads(news_file.read_text(encoding="utf-8"))["payload"] == announcement(5)
