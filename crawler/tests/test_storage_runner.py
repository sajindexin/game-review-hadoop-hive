from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import announcement, read_records, review

from steam_crawler.models import CrawlError, Page, canonical_json
from steam_crawler.runner import run_task
from steam_crawler.storage import Store


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.positions = []

    def fetch(self, query, position):
        self.positions.append(position)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def test_exact_limit_and_resume_consumes_cached_page_without_missing_items(tmp_path, query):
    client = FakeClient(
        [
            Page([review(1), review(2), review(3)], {"cursor": "next"}, False),
            Page([], {"cursor": "next"}, True),
        ]
    )
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        first = run_task(store, client, task_id, max_items=2)
        assert first["status"] == "paused"
        assert first["written"] == 2
        assert first["checkpoint"]["pending"]["offset"] == 2
    # A new process/connection can recover the cached third item without refetching '*'.
    with Store(tmp_path) as store:
        second = run_task(store, client, task_id, max_items=0)
        assert second["status"] == "completed"
        assert second["written"] == 3
        assert second["this_run"]["matched"] == 1
    assert client.positions == [{"cursor": "*"}, {"cursor": "next"}]
    assert [record["item_id"] for record in read_records(tmp_path)] == ["1", "2", "3"]


@pytest.mark.parametrize(
    "source,legacy_updates_only",
    [("reviews", False), ("announcements", False), ("announcements", True)],
)
def test_resume_legacy_tasks_after_removing_update_filter(
    tmp_path, query, source, legacy_updates_only
):
    query = replace(query, source=source)
    items = (
        [review(1), review(2)]
        if source == "reviews"
        else [announcement(1), announcement(2, title="直播", contents="促销信息")]
    )
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        first = run_task(store, FakeClient([Page(items, {}, True)]), task_id, max_items=1)
        assert first["status"] == "paused"
        config = first["config"]
        config["query"]["updates_only"] = legacy_updates_only
        with store.db:
            store.db.execute(
                "UPDATE tasks SET config=? WHERE task_id=?", (canonical_json(config), task_id)
            )
        client = FakeClient([])
        if legacy_updates_only:
            with pytest.raises(CrawlError, match="news 新建公告采集任务"):
                run_task(store, client, task_id, max_items=0)
            result = store.task(task_id)
            assert result["status"] == "failed"
            assert result["written"] == 1
            assert result["checkpoint"] == first["checkpoint"]
            assert "已移除的更新筛选" in result["error"]
        else:
            result = run_task(store, client, task_id, max_items=0)
            assert result["status"] == "completed"
            assert result["written"] == 2
        assert not client.positions
    assert len(read_records(tmp_path)) == (1 if legacy_updates_only else 2)


def test_unchanged_records_are_not_rewritten_but_a_b_a_changes_are_kept(tmp_path, query):
    with Store(tmp_path) as store:
        states = [review(1), review(1), review(1, voted_up=True), review(1)]
        counts = []
        for item in states:
            task_id = store.create_task(query)
            client = FakeClient([Page([item], {"cursor": "done"}, True)])
            counts.append(run_task(store, client, task_id, max_items=0)["written"])
        assert counts == [1, 0, 1, 1]
    assert len(read_records(tmp_path)) == 3


def test_jsonl_gzip_preserves_unicode_newlines_and_big_ids(tmp_path, query):
    with Store(tmp_path) as store:
        task_id = store.create_task(query, compressed=True)
        value = review(12345678901234567890)
        run_task(store, FakeClient([Page([value], {}, True)]), task_id, max_items=0)
    [record] = read_records(tmp_path)
    assert record["payload"] == value
    assert record["item_id"] == "12345678901234567890"
    assert list((tmp_path / "raw").rglob("*.jsonl.gz"))


def test_write_failure_does_not_commit_checkpoint_or_dedup_state(tmp_path, query, monkeypatch):
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        original_write = store._write_batch

        def crash_after_rename(*args, **kwargs):
            original_write(*args, **kwargs)
            raise OSError("simulated failure before DB commit")

        monkeypatch.setattr(store, "_write_batch", crash_after_rename)
        client = FakeClient([Page([review(1), review(2)], {}, True)])
        with pytest.raises(OSError):
            run_task(store, client, task_id, max_items=0)
        task = store.task(task_id)
        assert task["status"] == "failed"
        assert task["written"] == 0
        assert task["checkpoint"]["pending"]["offset"] == 0
        assert store.db.execute("SELECT COUNT(*) FROM latest_items").fetchone()[0] == 0
        monkeypatch.setattr(store, "_write_batch", original_write)
        result = run_task(store, FakeClient([]), task_id, max_items=0)
        assert result["written"] == 2
    assert len(list((tmp_path / "raw").rglob("*.jsonl"))) == 1
    assert len(read_records(tmp_path)) == 2


def test_page_limit_and_filtered_items_do_not_consume_match_budget(tmp_path, query):
    query.keywords = ["crash"]
    client = FakeClient(
        [
            Page([review(1, review="unrelated")], {"cursor": "n"}, False),
            Page([review(2, review="crash")], {}, True),
        ]
    )
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        result = run_task(store, client, task_id, max_items=1, max_pages=1)
        assert result["status"] == "paused" and result["matched"] == 0
        result = run_task(store, client, task_id, max_items=1)
        assert result["status"] == "completed" and result["matched"] == 1


def test_pagination_overlap_deduplicates_within_task(tmp_path, query):
    client = FakeClient(
        [
            Page([review(1), review(2)], {"cursor": "n"}, False),
            Page([review(2), review(3)], {}, True),
        ]
    )
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        result = run_task(store, client, task_id, max_items=0)
        assert result["scanned"] == 4
        assert result["matched"] == result["written"] == 3


def test_network_error_keeps_position_and_can_resume(tmp_path, query):
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        with pytest.raises(CrawlError):
            run_task(store, FakeClient([CrawlError("HTTP 429")]), task_id)
        assert store.task(task_id)["checkpoint"]["position"] == {"cursor": "*"}
        result = run_task(store, FakeClient([Page([review(1)], {}, True)]), task_id)
        assert result["status"] == "completed"


def test_query_is_frozen_and_missing_task_is_actionable(tmp_path, query):
    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        assert store.task(task_id)["config"]["query"] == query.as_dict()
        with pytest.raises(CrawlError, match="--output"):
            store.task("not-found")


def test_older_pending_observation_does_not_overwrite_latest_state(tmp_path, query):
    with Store(tmp_path) as store:
        old = store.create_task(query)
        store.cache_page(old, Page([review(1)], {}, True, fetched_at="2026-01-01T00:00:00Z"))
        new = store.create_task(replace(query))
        store.cache_page(
            new, Page([review(1, voted_up=True)], {}, True, fetched_at="2026-01-02T00:00:00Z")
        )
        store.consume_page(new, None)
        newest_hash = store.db.execute("SELECT content_hash FROM latest_items").fetchone()[0]
        store.consume_page(old, None)
        assert (
            store.db.execute("SELECT content_hash FROM latest_items").fetchone()[0] == newest_hash
        )


def test_keyboard_interrupt_is_resumable(tmp_path, query):
    class InterruptedClient:
        def fetch(self, query, position):
            raise KeyboardInterrupt

    with Store(tmp_path) as store:
        task_id = store.create_task(query)
        with pytest.raises(KeyboardInterrupt):
            run_task(store, InterruptedClient(), task_id)
        assert store.task(task_id)["status"] == "paused"
