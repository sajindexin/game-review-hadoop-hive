from __future__ import annotations

import gzip
import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .client import SteamClient
from .models import CrawlError, Query, utc_now
from .runner import run_task
from .storage import Store

LOGGER = logging.getLogger(__name__)
PLAN_FILE = "sampling-plan.json"
PROGRESS_FILE = "sampling-progress.json"
RESULT_FILE = "sample/reviews.jsonl.gz"


@dataclass(frozen=True)
class Day:
    date: str
    since: int
    until: int
    quota: int


@dataclass(frozen=True)
class SamplePlan:
    query: Query
    timezone: str
    total: int
    quota: str
    fill: str
    gzip_batches: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "query": self.query.as_dict(),
            "timezone": self.timezone,
            "total": self.total,
            "quota": self.quota,
            "fill": self.fill,
            "gzip_batches": self.gzip_batches,
        }

    def days(self) -> list[Day]:
        zone = ZoneInfo(self.timezone)
        current = datetime.fromtimestamp(self.query.since, zone).date()
        last = datetime.fromtimestamp(self.query.until - 1, zone).date()
        windows = []
        while current <= last:
            next_day = current + timedelta(days=1)
            start = int(datetime.combine(current, time.min, zone).timestamp())
            end = int(datetime.combine(next_day, time.min, zone).timestamp())
            since, until = max(start, self.query.since), min(end, self.query.until)
            if since < until:
                windows.append((current.isoformat(), since, until))
            current = next_day
        weights = [end - start if self.quota == "duration" else 1 for _, start, end in windows]
        return [
            Day(label, start, end, quota)
            for (label, start, end), quota in reversed(
                list(zip(windows, allocate(self.total, weights), strict=True))
            )
        ]


def allocate(total: int, weights: list[int]) -> list[int]:
    denominator = sum(weights)
    position = assigned = 0
    shares = []
    for weight in weights:
        position += weight
        cumulative = (total * position + denominator // 2) // denominator
        shares.append(cumulative - assigned)
        assigned = cumulative
    return shares


def topups(extra: list[int], missing: int) -> list[int]:
    """Level additions relative to each day's base quota, including prior invocations."""
    low, high = min(extra), max(extra) + missing + 1
    while high - low > 1:
        mid = (low + high) // 2
        needed = sum(max(0, mid - n) for n in extra)
        if needed <= missing:
            low = mid
        else:
            high = mid
    shares = [max(0, low - n) for n in extra]
    return [
        share + rest
        for share, rest in zip(
            shares,
            allocate(missing - sum(shares), [int(n <= low) for n in extra]),
            strict=True,
        )
    ]


def save_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def create_plan(store: Store, plan: SamplePlan) -> None:
    path = store.root / PLAN_FILE
    if path.exists():
        raise CrawlError("该目录已有采样计划，请使用 resume-sample 或换一个 --output")
    if store.db.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
        raise CrawlError("采样需要独立数据目录，请为 --output 指定新目录")
    if any((store.root / "raw").rglob("*.jsonl*")):
        raise CrawlError("该目录已有原始数据，请为 --output 指定新目录")
    days = plan.days()
    save_json(
        path,
        {
            **plan.as_dict(),
            "days": [
                {"date": day.date, "since": day.since, "until": day.until, "quota": day.quota}
                for day in days
            ],
        },
    )


def read_plan(root: Path) -> SamplePlan:
    path = root / PLAN_FILE
    if not path.is_file():
        raise CrawlError("找不到采样计划，请检查 --output")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["schema_version"] != 1:
            raise ValueError("schema_version")
        plan = SamplePlan(
            Query.from_dict(data["query"]),
            data["timezone"],
            data["total"],
            data["quota"],
            data["fill"],
            data["gzip_batches"],
        )
        if plan.query.source != "reviews" or plan.query.time_field != "created":
            raise ValueError("query")
        if not 0 <= plan.query.since < plan.query.until <= 4_294_967_295:
            raise ValueError("time range")
        if not isinstance(plan.total, int) or plan.total <= 0:
            raise ValueError("total")
        if plan.quota not in {"duration", "equal"} or plan.fill not in {"none", "month", "all"}:
            raise ValueError("quota or fill")
        actual = [(day.date, day.since, day.until, day.quota) for day in plan.days()]
        saved = [(day["date"], day["since"], day["until"], day["quota"]) for day in data["days"]]
        if actual != saved:
            raise ValueError("days changed")
    except (KeyError, TypeError, ValueError) as exc:
        raise CrawlError("采样计划格式无效或配额已改变，请另选数据目录新建计划") from exc
    return plan


def read_progress(root: Path) -> dict[str, Any]:
    plan = read_plan(root)
    path = root / PROGRESS_FILE
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CrawlError("进度文件格式无效，可通过 resume-sample 从数据库恢复") from exc
    return {
        "status": "planned",
        "appid": plan.query.appid,
        "written": 0,
        "total": plan.total,
        "missing": plan.total,
        "days_with_data": 0,
        "days_total": len(plan.days()),
    }


class PageBudgetReached(Exception):
    pass


def task_progress(task: dict[str, Any]) -> dict[str, Any]:
    # Pending pages stay in SQLite rather than accumulating in memory across days.
    keys = ("task_id", "status", "written", "pages", "batches")
    return {key: task[key] for key in keys}


def log_sample_progress(*, written: int, total: int, date: str, daily: int, quota: int) -> None:
    LOGGER.info(
        "采样总进度 %s/%s (%.2f%%)；%s 当日 %s/%s 条",
        f"{written:,}",
        f"{total:,}",
        100 * written / total,
        date,
        f"{daily:,}",
        f"{quota:,}",
    )


class Sampler:
    def __init__(self, store: Store, plan: SamplePlan, max_pages: int = 0):
        self.store = store
        self.plan = plan
        self.days = plan.days()
        self.max_pages = max_pages
        self.pages = 0
        self.tasks: dict[Day, dict[str, Any]] = {}
        by_start = {day.since: day for day in self.days}
        for row in store.db.execute("SELECT task_id FROM tasks").fetchall():
            task = store.task(row[0])
            day = by_start.get(task["config"]["query"].get("since"))
            if (
                day is None
                or day in self.tasks
                or task["config"]["query"] != self.query(day).as_dict()
                or task["config"]["gzip"] != self.plan.gzip_batches
                or task["matched"] != task["written"]
            ):
                raise CrawlError("数据目录中的任务与采样计划不一致，请检查 --output")
            self.tasks[day] = task_progress(task)

    def query(self, day: Day) -> Query:
        return replace(self.plan.query, since=day.since, until=day.until)

    def count(self, day: Day) -> int:
        return self.tasks.get(day, {}).get("written", 0)

    def exhausted(self, day: Day) -> bool:
        return self.tasks.get(day, {}).get("status") == "completed"

    def describe(self, status: str, *, error: str | None = None) -> dict[str, Any]:
        zone = ZoneInfo(self.plan.timezone)
        written = sum(self.count(day) for day in self.days)
        return {
            "status": status,
            "appid": self.plan.query.appid,
            "total": self.plan.total,
            "written": written,
            "missing": self.plan.total - written,
            "pages_this_run": self.pages,
            "days_total": len(self.days),
            "days_with_data": sum(self.count(day) > 0 for day in self.days),
            "days_at_base_quota": sum(self.count(day) >= day.quota for day in self.days),
            "days_short": sum(
                self.exhausted(day) and self.count(day) < day.quota for day in self.days
            ),
            "since": datetime.fromtimestamp(self.plan.query.since, zone).isoformat(),
            "until": datetime.fromtimestamp(self.plan.query.until, zone).isoformat(),
            "updated_at": utc_now(),
            "result_file": RESULT_FILE if status in {"completed", "shortfall"} else None,
            "error": error,
        }

    def progress(self, status: str, *, error: str | None = None) -> dict[str, Any]:
        result = self.describe(status, error=error)
        save_json(self.store.root / PROGRESS_FILE, result)
        return result

    def collect(self, client: SteamClient, day: Day, amount: int) -> int:
        if amount <= 0 or self.exhausted(day):
            return 0
        pages_left = self.max_pages - self.pages if self.max_pages else 0
        if self.max_pages and pages_left <= 0:
            raise PageBudgetReached
        if day not in self.tasks:
            task_id = self.store.create_task(self.query(day), compressed=self.plan.gzip_batches)
            self.tasks[day] = task_progress(self.store.task(task_id))
        current = self.tasks[day]
        task_id, before, pages = current["task_id"], current["written"], current["pages"]
        other_days_written = sum(task["written"] for task in self.tasks.values()) - before

        def report(task: dict[str, Any]) -> None:
            log_sample_progress(
                written=other_days_written + task["written"],
                total=self.plan.total,
                date=day.date,
                daily=task["written"],
                quota=before + amount,
            )

        LOGGER.info("采样日期=%s，本次配额=%d", day.date, amount)
        try:
            task = run_task(
                self.store,
                client,
                task_id,
                max_items=amount,
                max_pages=pages_left,
                on_progress=report,
            )
        finally:
            self.tasks[day] = task_progress(self.store.task(task_id))
            self.pages += self.tasks[day]["pages"] - pages
        if task["matched"] != task["written"]:
            raise CrawlError(f"{day.date} 有重复记录，无法按配额统计，请检查采样目录")
        gained = task["written"] - before
        if gained < amount and not self.exhausted(day):
            raise PageBudgetReached
        self.progress("running")
        return gained

    def fill(self, client: SteamClient, days: list[Day], missing: int) -> None:
        while missing > 0:
            available = [day for day in days if not self.exhausted(day)]
            if not available:
                return
            extras = [max(0, self.count(day) - day.quota) for day in available]
            before = missing
            for day, amount in zip(available, topups(extras, missing), strict=True):
                missing -= self.collect(client, day, amount)
            if missing == before and not any(self.exhausted(day) for day in available):
                raise CrawlError("补齐配额时未取得进展")

    def run(self, client: SteamClient) -> dict[str, Any]:
        start = self.progress("running")
        LOGGER.info("采样已开始，已写入 %s/%s 条", f"{start['written']:,}", f"{self.plan.total:,}")
        try:
            months = defaultdict(list)
            for day in self.days:
                months[day.date[:7]].append(day)
                self.collect(client, day, day.quota - self.count(day))
            if self.plan.fill != "none":
                for days in months.values():
                    self.fill(client, days, sum(day.quota - self.count(day) for day in days))
            if self.plan.fill == "all":
                self.fill(client, self.days, self.plan.total - sum(map(self.count, self.days)))
            written = sum(map(self.count, self.days))
            self.export(written)
            LOGGER.info("已导出 %s 条样本：%s", f"{written:,}", self.store.root / RESULT_FILE)
            return self.progress("completed" if written == self.plan.total else "shortfall")
        except PageBudgetReached:
            return self.progress("paused")
        except KeyboardInterrupt:
            self.progress("paused")
            raise
        except Exception as exc:
            self.progress(
                "failed", error=str(exc) if isinstance(exc, CrawlError) else type(exc).__name__
            )
            raise

    def export(self, expected: int) -> None:
        final = self.store.root / RESULT_FILE
        final.parent.mkdir(exist_ok=True)
        temp = final.with_suffix(".tmp")
        batches: dict[str, list[tuple[int, Path]]] = defaultdict(list)
        for path in (self.store.root / "raw" / "reviews").rglob("*.jsonl*"):
            task_id, marker, tail = path.name.partition("-batch-")
            sequence = tail.split(".", 1)[0]
            if marker and sequence.isdecimal() and path.suffix in (".gz", ".jsonl"):
                batches[task_id].append((int(sequence), path))

        seen: set[str] = set()
        languages: Counter[str] = Counter()
        day_counts = []
        with gzip.open(temp, "wt", encoding="utf-8", newline="\n") as output:
            for day in self.days:
                task = self.tasks.get(day)
                items = []
                for sequence, path in batches.get(task["task_id"], []) if task else ():
                    if sequence > task["batches"]:
                        continue
                    opener = gzip.open if path.suffix == ".gz" else open
                    with opener(path, "rt", encoding="utf-8") as stream:
                        for line in stream:
                            record = json.loads(line)
                            item = record["payload"]
                            identity, stamp = record["item_id"], item["timestamp_created"]
                            language = item["language"]
                            allowed = self.plan.query.languages
                            if (
                                identity in seen
                                or not day.since <= stamp < day.until
                                or ("all" not in allowed and language not in allowed)
                            ):
                                raise CrawlError(f"{day.date} 的评价未通过 ID、日期或语言核验")
                            seen.add(identity)
                            languages[language] += 1
                            items.append((stamp, identity, line))
                if len(items) != self.count(day):
                    raise CrawlError(f"{day.date} 的文件与断点记录条数不同")
                for _, _, line in sorted(items, reverse=True):
                    output.write(line)
                day_counts.append({"date": day.date, "quota": day.quota, "written": len(items)})
        if len(seen) != expected:
            raise CrawlError("样本文件总条数与任务记录不一致")
        temp.replace(final)
        save_json(
            final.parent / "summary.json",
            {
                "appid": self.plan.query.appid,
                "written": len(seen),
                "language_counts": dict(languages),
                "sort": "timestamp_created_desc",
                "days": day_counts,
            },
        )
