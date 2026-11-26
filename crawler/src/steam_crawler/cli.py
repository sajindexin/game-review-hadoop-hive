from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from filelock import FileLock, Timeout

from . import __version__
from .client import SteamClient
from .exporting import KINDS, export_data
from .models import CrawlError, Query, normalize_languages, parse_boundary, split_values
from .run_logging import LOG_FILE, collection_logging
from .runner import run_task
from .sampling import (
    PLAN_FILE,
    PROGRESS_FILE,
    RESULT_FILE,
    SamplePlan,
    Sampler,
    create_plan,
    read_plan,
    read_progress,
)
from .storage import Store

SAMPLE_COMMANDS = {"sample-reviews", "resume-sample"}
LOGGER = logging.getLogger(__name__)


def nonnegative_int(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("必须为非负整数")
    return result


def positive_int(value: str) -> int:
    result = nonnegative_int(value)
    if result == 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return result


def nonnegative_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("必须为有限的非负数字")
    return result


def positive_float(value: str) -> float:
    result = nonnegative_float(value)
    if result == 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Steam 评价与公告采集工具")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("reviews", "采集公开评价"),
        ("sample-reviews", "按天分配总配额，采集评价"),
        ("news", "采集公告与新闻（官方接口不支持语言筛选）"),
        ("resume", "续采已有任务，沿用原筛选条件"),
        ("resume-sample", "继续按天采样，沿用保存的计划"),
        ("status", "查看已保存任务"),
    ):
        sub = commands.add_parser(command, help=help_text, description=help_text)
        if command in SAMPLE_COMMANDS:
            sub.add_argument("--output", type=Path, required=True, help="采样独立数据目录")
        else:
            sub.add_argument(
                "--output", type=Path, default=Path("data"), help="数据目录，默认 ./data"
            )
        sub.add_argument(
            "--json", action="store_true", help="stdout 仅输出 JSON 结果，终端日志改写入 stderr"
        )
        if command == "status":
            modes = sub.add_mutually_exclusive_group()
            modes.add_argument("--task-id", help="查看指定任务及完整查询配置")
            modes.add_argument("--sample", action="store_true", help="查看按天采样的整体进度")
            sub.add_argument("--limit", type=positive_int, default=20, help="最近任务数，默认 20")
            continue
        if command not in SAMPLE_COMMANDS:
            sub.add_argument(
                "--max-items",
                type=nonnegative_int,
                default=1000,
                help="每游戏/任务本次最多处理的匹配条目数，默认 1000；0 为不限",
            )
        sub.add_argument(
            "--max-pages",
            type=nonnegative_int,
            default=0,
            help=(
                "本次采样跨所有日期最多获取的页数，默认 0 不限"
                if command in SAMPLE_COMMANDS
                else "本次最多获取的页数，默认 0 不限；达到上限可续采"
            ),
        )
        sub.add_argument(
            "--interval", type=nonnegative_float, default=2, help="请求最小间隔秒数，默认 2"
        )
        sub.add_argument(
            "--jitter", type=nonnegative_float, default=0.5, help="附加随机间隔 0～N 秒，默认 0.5"
        )
        sub.add_argument("--timeout", type=positive_float, default=30, help="请求超时秒数，默认 30")
        sub.add_argument("--retries", type=nonnegative_int, default=4, help="失败重试次数，默认 4")
        sub.add_argument("--proxy", help="HTTP/SOCKS 代理；也支持 HTTP_PROXY/HTTPS_PROXY 环境变量")
        sub.add_argument("--ca-bundle", type=Path, help="自定义 PEM CA 文件；默认使用系统信任证书")
        sub.add_argument(
            "--user-agent",
            default=f"steam-feedback-crawler/{__version__}",
            help="自定义 User-Agent",
        )
        sub.add_argument(
            "--quiet", action="store_true", help="终端只显示警告、错误和结果，文件保留完整日志"
        )
        if command == "resume":
            sub.add_argument("task_id", help="status 输出的任务 ID")
            continue
        if command == "resume-sample":
            continue
        if command == "sample-reviews":
            sub.add_argument("--total", type=positive_int, required=True, help="采样目标条数")
            sub.add_argument(
                "--quota",
                choices=["duration", "equal"],
                default="duration",
                help="按覆盖时长分配（duration，默认）或按天等量（equal）",
            )
            sub.add_argument(
                "--fill",
                choices=["none", "month", "all"],
                default="all",
                help="缺额补齐：不补、仅同月、先同月再跨月（默认）",
            )
            sub.add_argument("--plan-only", action="store_true", help="保存配额计划，暂不采集")
        sub.add_argument(
            "--app-id",
            type=positive_int,
            action="append",
            required=True,
            help=(
                "Steam appid，每个采样计划对应一款游戏"
                if command == "sample-reviews"
                else "Steam appid，可重复，例如 --app-id 570 --app-id 730"
            ),
        )
        sub.add_argument(
            "--since",
            required=command == "sample-reviews",
            help="起始时间（包含）：YYYY-MM-DD 或 ISO 8601",
        )
        sub.add_argument("--until", help="结束时间：日期包含整天；带时间时为排除边界；默认启动时刻")
        sub.add_argument(
            "--timezone",
            default="Asia/Shanghai",
            help="无时区时间/日期所用时区，默认 Asia/Shanghai；分区使用 UTC",
        )
        sub.add_argument(
            "--page-size",
            type=positive_int,
            default=50 if command == "news" else 100,
            help="每页条目数，评价 1～100，公告 1～1000",
        )
        sub.add_argument(
            "--keyword",
            action="append",
            default=[],
            help="包含关键词，可重复，任意命中即可（不区分大小写）",
        )
        sub.add_argument(
            "--exclude-keyword",
            action="append",
            default=[],
            help="排除关键词，可重复，任意命中即排除",
        )
        sub.add_argument("--gzip", action="store_true", help="输出 .jsonl.gz，默认 .jsonl")
        if command != "news":
            sub.add_argument(
                "--language",
                action="append",
                help="语言，可重复或逗号分隔；默认 all，采集全部语言",
            )
            if command == "reviews":
                sub.add_argument(
                    "--time-field",
                    choices=["created", "updated"],
                    default="created",
                    help="时间筛选依据，默认 created；增量捕获旧评价修改可选 updated",
                )
            else:
                sub.set_defaults(time_field="created")
            sub.add_argument(
                "--review-type", choices=["all", "positive", "negative"], default="all"
            )
            sub.add_argument(
                "--purchase-type", choices=["all", "steam", "non-steam"], default="all"
            )
            sub.add_argument(
                "--exclude-offtopic",
                dest="include_offtopic",
                action="store_false",
                help="排除 Steam 标记的跑题评价（默认包含）",
            )
            sub.add_argument("--min-playtime-hours", type=nonnegative_int)
            sub.add_argument("--max-playtime-hours", type=nonnegative_int)
        else:
            feeds = sub.add_mutually_exclusive_group()
            feeds.add_argument(
                "--feed",
                action="append",
                help="新闻源，可重复或逗号分隔；默认 steam_community_announcements",
            )
            feeds.add_argument(
                "--all-feeds", action="store_true", help="包含全部新闻源（可能包含第三方媒体）"
            )
    export = commands.add_parser("export", help="将已采集的评价和公告导出为未压缩 JSONL")
    export.add_argument("--output", type=Path, required=True, help="保存 JSONL 文件的目录")
    export.add_argument("--input", type=Path, help="两类数据共用的采集目录；未指定来源时用 ./data")
    export.add_argument("--reviews-from", type=Path, help="单独指定评价的采集目录")
    export.add_argument("--announcements-from", type=Path, help="单独指定公告的采集目录")
    export.add_argument("--app-id", type=positive_int, help="只导出指定游戏；省略时不限")
    export.add_argument("--overwrite", action="store_true", help="覆盖同名导出文件")
    export.add_argument("--json", action="store_true", help="以 JSON 输出文件路径和条数")
    return parser


def make_queries(args: argparse.Namespace) -> list[Query]:
    since = parse_boundary(args.since, args.timezone) or 0
    until = parse_boundary(args.until, args.timezone, end=True)
    if until is None:
        until = int(datetime.now(UTC).timestamp())
    if since >= until:
        raise ValueError("起始时间必须早于结束时间")
    source = "announcements" if args.command == "news" else "reviews"
    if args.command == "sample-reviews" and len(args.app_id) != 1:
        raise ValueError("每个采样计划只能指定一个 --app-id")
    if args.page_size > (100 if source == "reviews" else 1000):
        raise ValueError("评价 page-size 最大 100，公告 page-size 最大 1000")
    fields: dict[str, Any] = {}
    if source == "reviews":
        minimum, maximum = args.min_playtime_hours, args.max_playtime_hours
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("最小游玩时长不能大于最大游玩时长")
        fields = {
            "languages": normalize_languages(args.language),
            "time_field": args.time_field,
            "review_type": args.review_type,
            "purchase_type": args.purchase_type,
            "include_offtopic": args.include_offtopic,
            "min_playtime_hours": minimum,
            "max_playtime_hours": maximum,
        }
    else:
        fields = {
            "feeds": []
            if args.all_feeds
            else (split_values(args.feed) or ["steam_community_announcements"]),
        }
    if any(appid > 4_294_967_295 for appid in args.app_id):
        raise ValueError("appid 超出 uint32 范围")
    return [
        Query(
            source=source,
            appid=appid,
            since=since,
            until=until,
            page_size=args.page_size,
            keywords=args.keyword,
            exclude_keywords=args.exclude_keyword,
            **fields,
        )
        for appid in dict.fromkeys(args.app_id)
    ]


def public_task(task: dict[str, Any], *, verbose: bool = False) -> dict[str, Any]:
    result = {
        key: task[key]
        for key in (
            "task_id",
            "source",
            "appid",
            "status",
            "pages",
            "scanned",
            "matched",
            "written",
            "batches",
            "created_at",
            "updated_at",
            "error",
        )
    }
    result["unchanged"] = task["matched"] - task["written"]
    if "this_run" in task:
        result["this_run"] = task["this_run"]
    if verbose:
        result["config"] = task["config"]
        result["position"] = task["checkpoint"]["position"]
        pending = task["checkpoint"]["pending"]
        result["pending_items"] = len(pending["items"]) - pending["offset"] if pending else 0
    return result


def print_results(
    tasks: list[dict[str, Any]],
    *,
    as_json: bool,
    verbose: bool = False,
    paths: dict[str, str] | None = None,
) -> None:
    values = [public_task(task, verbose=verbose) for task in tasks]
    if as_json:
        if paths:
            for task in values:
                task.update(paths)
        print(json.dumps(values, ensure_ascii=False, indent=2))
        return
    if not values:
        print("暂无采集任务。")
    for task in values:
        current = task.get("this_run")
        delta = f"；本次匹配 {current['matched']}，写入 {current['written']}" if current else ""
        print(
            f"{task['task_id']}  [{task['status']}]  页={task['pages']} "
            f"匹配={task['matched']} 写入={task['written']} 未变化={task['unchanged']}{delta}"
        )
        if task["error"]:
            print(f"  错误：{task['error']}")
        if verbose:
            print(
                json.dumps(
                    {
                        "config": task["config"],
                        "position": task["position"],
                        "pending_items": task["pending_items"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


def collection_paths(root: Path, *, sampled: bool) -> dict[str, str]:
    paths = {
        "output_dir": str(root),
        "log_file": str(root / LOG_FILE),
        "raw_dir": str(root / "raw"),
    }
    if sampled:
        paths.update(
            plan_file=str(root / PLAN_FILE),
            progress_file=str(root / PROGRESS_FILE),
            sample_path=str(root / RESULT_FILE),
        )
    return paths


def print_sample(result: dict[str, Any], *, as_json: bool, root: Path) -> None:
    if as_json:
        print(
            json.dumps(
                {**result, **collection_paths(root, sampled=True)}, ensure_ascii=False, indent=2
            )
        )
        return
    print(
        f"采样 [{result['status']}]  已写入 {result['written']}/{result['total']} 条，"
        f"覆盖 {result['days_with_data']}/{result['days_total']} 天"
    )
    if result.get("result_file"):
        print(f"样本文件：{root / result['result_file']}")
    if result.get("error"):
        print(f"错误：{result['error']}")


def make_client(args: argparse.Namespace) -> SteamClient:
    return SteamClient(
        interval=args.interval,
        jitter=args.jitter,
        timeout=args.timeout,
        retries=args.retries,
        proxy=args.proxy,
        ca_bundle=str(args.ca_bundle) if args.ca_bundle else None,
        api_key=os.environ.get("STEAM_API_KEY"),
        user_agent=args.user_agent,
    )


def run_export(args: argparse.Namespace, output: Path) -> int:
    if args.app_id is not None and args.app_id > 4_294_967_295:
        raise CrawlError("appid 超出 uint32 范围")
    explicit = {
        kind: path
        for kind, path in zip(KINDS, (args.reviews_from, args.announcements_from), strict=True)
        if path is not None
    }
    common = args.input or (Path("data") if not explicit else None)
    inputs = {kind: common for kind in KINDS} if common is not None else {}
    inputs.update(explicit)
    files = export_data(
        {kind: path.resolve() for kind, path in inputs.items()},
        output,
        required=set(explicit),
        appid=args.app_id,
        overwrite=args.overwrite,
    )
    if args.json:
        print(json.dumps({"output_dir": str(output), "files": files}, ensure_ascii=False, indent=2))
    else:
        print(f"导出目录：{output}")
        for result in files.values():
            print(f"{result['path']}：{result['written']:,} 条")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        queries = (
            make_queries(args) if args.command in ("reviews", "news", "sample-reviews") else []
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        root = args.output.resolve()
        if args.command == "export":
            return run_export(args, root)
        if args.command == "status":
            if args.sample:
                print_sample(read_progress(root), as_json=args.json, root=root)
                return 0
            if not (root / "state" / "crawler.db").exists():
                if args.task_id:
                    raise CrawlError("数据目录中没有任务数据库")
                print_results([], as_json=args.json)
                return 0
            with Store(root) as store:
                tasks = [store.task(args.task_id)] if args.task_id else store.list_tasks(args.limit)
                print_results(tasks, as_json=args.json, verbose=bool(args.task_id))
            return 0
        if args.command == "resume-sample" and not (root / PLAN_FILE).is_file():
            raise CrawlError("找不到采样计划，请检查 --output")
        root.mkdir(parents=True, exist_ok=True)
        with FileLock(root / ".crawler.lock", timeout=0):
            return run_collection(args, queries, root)
    except Timeout:
        print("错误：该数据目录已有采集程序运行；请等待完成或选择不同 --output。", file=sys.stderr)
        return 1
    except (CrawlError, OSError, sqlite3.Error) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if args.command == "export":
            print("已取消导出。", file=sys.stderr)
            return 130
        resume = "resume-sample" if args.command in SAMPLE_COMMANDS else "resume"
        print(f"已中断；可使用 {resume} 从保存的进度继续。", file=sys.stderr)
        return 130


def run_collection(args: argparse.Namespace, queries: list[Query], root: Path) -> int:
    sampled = args.command in SAMPLE_COMMANDS
    with collection_logging(root / LOG_FILE, as_json=args.json, quiet=args.quiet):
        LOGGER.info("输出目录：%s", root)
        LOGGER.info("日志文件：%s", root / LOG_FILE)
        LOGGER.info("原始数据目录：%s", root / "raw")
        if sampled:
            LOGGER.info("采样计划：%s；进度文件：%s", root / PLAN_FILE, root / PROGRESS_FILE)
            LOGGER.info("样本导出路径：%s", root / RESULT_FILE)
        try:
            with Store(root) as store:
                if sampled:
                    return run_sample(args, queries, store)
                if (root / PLAN_FILE).exists():
                    raise CrawlError("该目录用于按天采样，请使用 resume-sample 或另选 --output")
                with make_client(args) as client:
                    return run_regular_tasks(args, queries, store, client)
        except httpx.InvalidURL:
            LOGGER.error("代理地址格式无效，请检查 --proxy")
            return 2
        except (CrawlError, OSError, sqlite3.Error) as exc:
            LOGGER.error("%s", exc)
            return 1
        except KeyboardInterrupt:
            resume = "resume-sample" if sampled else "resume"
            LOGGER.warning("已中断；可使用 %s 从保存的进度继续", resume)
            return 130


def run_sample(args: argparse.Namespace, queries: list[Query], store: Store) -> int:
    if args.command == "sample-reviews":
        plan = SamplePlan(queries[0], args.timezone, args.total, args.quota, args.fill, args.gzip)
        create_plan(store, plan)
    else:
        plan = read_plan(store.root)
    sampler = Sampler(store, plan, args.max_pages)
    if getattr(args, "plan_only", False):
        result = sampler.progress("planned")
    else:
        with make_client(args) as client:
            result = sampler.run(client)
    print_sample(result, as_json=args.json, root=store.root)
    return 1 if result["status"] == "shortfall" else 0


def run_regular_tasks(
    args: argparse.Namespace, queries: list[Query], store: Store, client: SteamClient
) -> int:
    if args.command == "resume":
        task_ids = [args.task_id]
        store.task(args.task_id)
    else:
        task_ids = [store.create_task(query, compressed=args.gzip) for query in queries]
    results = []
    failed = False
    for task_id in task_ids:
        try:
            results.append(
                run_task(store, client, task_id, max_items=args.max_items, max_pages=args.max_pages)
            )
        except CrawlError as exc:
            LOGGER.error("任务 %s：%s", task_id, exc)
            results.append(store.task(task_id))
            failed = True
    print_results(results, as_json=args.json, paths=collection_paths(store.root, sampled=False))
    return 1 if failed else 0
