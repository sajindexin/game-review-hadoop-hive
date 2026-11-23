"""为本次复算展开数据位置和新的 Hive 库名，保留原始课程 SQL。"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4


SOURCE = Path(__file__).resolve().parents[1] / "黄静雯_全组Hive统计/windows.hql"


def prepare(stage_uri, database, output):
    location = stage_uri.rstrip("/")
    uri = urlsplit(location)
    if (uri.scheme not in {"hdfs", "viewfs", "file"} or not uri.path.startswith("/") or
            any(c in location for c in "'\\\r\n\t") or uri.query or uri.fragment):
        raise ValueError("请输入本机 file:/// 或集群 hdfs:///、viewfs:/// 的绝对目录 URI。")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,100}", database) or database in {"default","hjw_group_windows_v1"}:
        raise ValueError("复算库名需使用小写字母、数字、下划线，与原库分开。")
    sql = SOURCE.read_text(encoding="utf-8-sig")
    header = "CREATE DATABASE IF NOT EXISTS hjw_group_windows_v1;\nUSE hjw_group_windows_v1;"
    if sql.count(header) != 1 or sql.count("${hivevar:stage_uri}") != 3:
        raise ValueError("课程 SQL 的输入或建库语句已变动，请先核对版本。")
    sql = sql.replace(header, f"CREATE DATABASE {database};\nUSE {database};")
    sql = sql.replace("${hivevar:stage_uri}", location)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(sql)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage-uri", required=True, help="父目录中需有 calendar、languages、reviews 三个子目录")
    ap.add_argument("--database", default=None, help="新建的独立复算库名，默认自动生成")
    ap.add_argument("--output", type=Path, required=True, help="新 SQL 文件的路径")
    args = ap.parse_args()
    name = args.database or ("hjw_replay_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "_" + uuid4().hex[:8])
    prepare(args.stage_uri, name, args.output)
    print(f"复算库 {name}  |  SQL {args.output}")
