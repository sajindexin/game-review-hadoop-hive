from __future__ import annotations

import gzip
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .models import (
    CrawlError,
    Page,
    Query,
    canonical_json,
    digest,
    item_id,
    matches,
    utc_now,
)


class Store:
    """SQLite checkpoints plus atomically replaced, immutable JSONL batch files.

    The CLI holds a directory-wide writer lock. A batch filename is derived from
    the committed sequence, so retrying after a file/DB crash replaces the same
    uncommitted file rather than appending duplicate lines.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        state_dir = self.root / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(state_dir / "crawler.db", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, source TEXT NOT NULL, appid INTEGER NOT NULL,
                config TEXT NOT NULL, checkpoint TEXT NOT NULL, status TEXT NOT NULL,
                pages INTEGER NOT NULL DEFAULT 0, scanned INTEGER NOT NULL DEFAULT 0,
                matched INTEGER NOT NULL DEFAULT 0, written INTEGER NOT NULL DEFAULT 0,
                batches INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS task_items (
                task_id TEXT NOT NULL REFERENCES tasks(task_id), item_id TEXT NOT NULL,
                PRIMARY KEY (task_id, item_id)
            );
            CREATE TABLE IF NOT EXISTS task_pages (
                task_id TEXT NOT NULL REFERENCES tasks(task_id), position_hash TEXT NOT NULL,
                PRIMARY KEY (task_id, position_hash)
            );
            CREATE TABLE IF NOT EXISTS latest_items (
                source TEXT NOT NULL, appid INTEGER NOT NULL, item_id TEXT NOT NULL,
                content_hash TEXT NOT NULL, last_seen_at TEXT NOT NULL, file_path TEXT NOT NULL,
                PRIMARY KEY (source, appid, item_id)
            );
            PRAGMA user_version=1;
        """)

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *args: Any) -> None:
        self.db.close()

    def create_task(self, query: Query, *, compressed: bool = False) -> str:
        task_id = f"{query.source}-{query.appid}-{uuid.uuid4().hex[:12]}"
        now = utc_now()
        config = {"query": query.as_dict(), "gzip": compressed}
        checkpoint = {"position": query.initial_position(), "pending": None}
        with self.db:
            self.db.execute(
                "INSERT INTO tasks (task_id, source, appid, config, checkpoint, status, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'paused', ?, ?)",
                (
                    task_id,
                    query.source,
                    query.appid,
                    canonical_json(config),
                    canonical_json(checkpoint),
                    now,
                    now,
                ),
            )
        return task_id

    def task(self, task_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise CrawlError(f"未找到任务 {task_id}；请检查 --output 是否与原任务一致")
        task = dict(row)
        task["config"] = json.loads(task["config"])
        task["checkpoint"] = json.loads(task["checkpoint"])
        return task

    def list_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT task_id FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self.task(row[0]) for row in rows]

    def set_status(self, task_id: str, status: str, error: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE tasks SET status=?, error=?, updated_at=? WHERE task_id=?",
                (status, error, utc_now(), task_id),
            )

    def cache_page(self, task_id: str, page: Page) -> None:
        task = self.task(task_id)
        checkpoint = task["checkpoint"]
        if checkpoint["pending"] is not None:
            raise CrawlError("当前页尚未消费，不能覆盖断点")
        checkpoint["pending"] = {
            "items": page.items,
            "offset": 0,
            "next_position": page.next_position,
            "exhausted": page.exhausted,
            "fetched_at": page.fetched_at,
        }
        try:
            with self.db:
                self.db.execute(
                    "INSERT INTO task_pages VALUES (?, ?)",
                    (task_id, digest(checkpoint["position"])),
                )
                self.db.execute(
                    "UPDATE tasks SET checkpoint=?, pages=pages+1, updated_at=? WHERE task_id=?",
                    (canonical_json(checkpoint), utc_now(), task_id),
                )
        except sqlite3.IntegrityError as exc:
            raise CrawlError("分页位置出现循环，已停止；请新建时间范围更小的任务") from exc

    def consume_page(self, task_id: str, limit: int | None) -> dict[str, Any]:
        task = self.task(task_id)
        query = Query.from_dict(task["config"]["query"])
        checkpoint = task["checkpoint"]
        pending = checkpoint["pending"]
        if pending is None:
            raise CrawlError("没有可以消费的缓存页")
        scanned = matched = 0
        records: list[dict[str, Any]] = []
        latest_updates: list[tuple[str, str, bool]] = []
        fetched_at = pending["fetched_at"]
        with self.db:
            while pending["offset"] < len(pending["items"]):
                if limit is not None and matched >= limit:
                    break
                item = pending["items"][pending["offset"]]
                pending["offset"] += 1
                scanned += 1
                identity = item_id(query, item)
                if not matches(query, item):
                    continue
                inserted = self.db.execute(
                    "INSERT OR IGNORE INTO task_items VALUES (?, ?)",
                    (task_id, identity),
                ).rowcount
                if not inserted:
                    continue
                matched += 1
                content_hash = digest(item)
                latest = self.db.execute(
                    "SELECT * FROM latest_items WHERE source=? AND appid=? AND item_id=?",
                    (query.source, query.appid, identity),
                ).fetchone()
                changed = latest is None or latest["content_hash"] != content_hash
                if changed:
                    records.append(self._envelope(query, item, task_id, fetched_at, content_hash))
                latest_updates.append((identity, content_hash, changed))

            file_path = ""
            batches = task["batches"]
            if records:
                batches += 1
                file_path = self._write_batch(
                    query.source,
                    task_id,
                    batches,
                    fetched_at,
                    records,
                    task["config"]["gzip"],
                )
            for identity, content_hash, changed in latest_updates:
                self.db.execute(
                    "INSERT INTO latest_items VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(source, appid, item_id) DO UPDATE SET "
                    "content_hash=excluded.content_hash, last_seen_at=excluded.last_seen_at, "
                    "file_path=CASE WHEN ? THEN excluded.file_path ELSE latest_items.file_path END "
                    "WHERE excluded.last_seen_at >= latest_items.last_seen_at",
                    (
                        query.source,
                        query.appid,
                        identity,
                        content_hash,
                        fetched_at,
                        file_path,
                        changed,
                    ),
                )
            status = "running"
            if pending["offset"] == len(pending["items"]):
                checkpoint = {"position": pending["next_position"], "pending": None}
                if pending["exhausted"]:
                    status = "completed"
            self.db.execute(
                "UPDATE tasks SET checkpoint=?, status=?, scanned=scanned+?, matched=matched+?, "
                "written=written+?, batches=?, updated_at=? WHERE task_id=?",
                (
                    canonical_json(checkpoint),
                    status,
                    scanned,
                    matched,
                    len(records),
                    batches,
                    utc_now(),
                    task_id,
                ),
            )
        return self.task(task_id)

    @staticmethod
    def _envelope(
        query: Query,
        item: dict[str, Any],
        task_id: str,
        fetched_at: str,
        content_hash: str,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": 1,
            "appid": query.appid,
            "source": f"steam_{query.source}",
            "item_id": item_id(query, item),
            "task_id": task_id,
            "fetched_at": fetched_at,
            "content_hash": content_hash,
            "query": query.as_dict(),
            "payload": item,
        }
        if query.source == "announcements":
            record["annotations"] = {
                "announced_at": item["date"],
                "effective_at": None,
                "time_basis": "announcement_published",
            }
        return record

    def _write_batch(
        self,
        source: str,
        task_id: str,
        sequence: int,
        fetched_at: str,
        records: list[dict[str, Any]],
        compressed: bool,
    ) -> str:
        partition = self.root / "raw" / source / f"ingest_date={fetched_at[:10]}"
        partition.mkdir(parents=True, exist_ok=True)
        suffix = ".jsonl.gz" if compressed else ".jsonl"
        destination = partition / f"{task_id}-batch-{sequence:06d}{suffix}"
        temporary = destination.with_name(destination.name + ".tmp")
        # fsync the underlying file after closing gzip so its footer is durable as well.
        with temporary.open("wb") as raw:
            encoded = "".join(canonical_json(record) + "\n" for record in records).encode("utf-8")
            if compressed:
                with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                    stream.write(encoded)
            else:
                raw.write(encoded)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary, destination)
        return destination.relative_to(self.root).as_posix()
