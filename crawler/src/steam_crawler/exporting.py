from __future__ import annotations

import gzip
import json
import sqlite3
from collections.abc import Generator
from contextlib import ExitStack, closing
from itertools import chain, groupby
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .models import CrawlError
from .sampling import RESULT_FILE

KINDS = ("reviews", "announcements")


def read_records(path: Path) -> Generator[tuple[dict[str, Any], bytes], None, None]:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rb") as stream:
            for number, line in enumerate(stream, 1):
                try:
                    record = json.loads(line.decode("utf-8"))
                except ValueError as exc:
                    raise CrawlError(f"无效的 JSONL：{path} 第 {number} 行") from exc
                if not isinstance(record, dict):
                    raise CrawlError(f"预期为 JSON 对象：{path} 第 {number} 行")
                yield record, line if line.endswith(b"\n") else line + b"\n"
    except EOFError as exc:
        raise CrawlError(f"gzip 文件不完整：{path}") from exc


def export_lines(root: Path, kind: str, appid: int | None) -> Generator[bytes, None, None]:
    sample = root / RESULT_FILE
    if kind == "reviews" and sample.is_file():
        with closing(read_records(sample)) as records:
            for record, line in records:
                if appid is None or record.get("appid") == appid:
                    yield line
        return

    database = root / "state" / "crawler.db"
    if not database.is_file():
        return
    select = "SELECT file_path, appid, item_id FROM latest_items WHERE source = ?"
    params: tuple[str | int, ...] = (kind,)
    if appid is not None:
        select += " AND appid = ?"
        params += (appid,)
    select += " ORDER BY file_path"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        for filename, keys in groupby(db.execute(select, params), key=lambda row: row[0]):
            path = (root / filename).resolve()
            if not path.is_relative_to(root / "raw" / kind):
                raise CrawlError(f"批次文件不在对应的原始数据目录中：{path}")
            remaining = {(app, item) for _, app, item in keys}
            with closing(read_records(path)) as records:
                for record, line in records:
                    key = (record.get("appid"), record.get("item_id"))
                    if key in remaining and record.get("source") == f"steam_{kind}":
                        remaining.remove(key)
                        yield line
            if remaining:
                raise CrawlError(f"批次文件缺少索引中的记录：{path}")


def export_data(
    inputs: dict[str, Path],
    output: Path,
    *,
    required: set[str],
    appid: int | None = None,
    overwrite: bool = False,
) -> dict[str, dict[str, str | int]]:
    staged: list[tuple[Path, Path]] = []
    results: dict[str, dict[str, str | int]] = {}
    with ExitStack() as cleanup:
        for kind, root in inputs.items():
            with closing(export_lines(root, kind, appid)) as lines:
                first = next(lines, None)
                if first is None:
                    if kind in required:
                        raise CrawlError(f"未找到可导出的 {kind} 数据：{root}")
                    continue
                target = output / f"{kind}.jsonl"
                if not overwrite and (target.exists() or target.is_symlink()):
                    raise CrawlError(f"文件已存在：{target}；使用 --overwrite 覆盖")
                output.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=output, prefix=f".{kind}.", delete=False) as stream:
                    temporary = Path(stream.name)
                    cleanup.callback(temporary.unlink, missing_ok=True)
                    written = 0
                    for line in chain((first,), lines):
                        stream.write(line)
                        written += 1
                staged.append((temporary, target))
                results[kind] = {"path": str(target), "written": written}
        if not results:
            roots = "、".join(str(root) for root in dict.fromkeys(inputs.values()))
            raise CrawlError(f"未找到可导出的评价或公告：{roots}")
        for temporary, target in staged:
            temporary.replace(target)
    return results
