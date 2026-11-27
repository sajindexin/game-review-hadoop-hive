# -*- coding: utf-8 -*-
"""
RBT3 全量回灌实时监控面板：每 N 秒刷新进度 / 速率 / ETA / 卡住检测。

用法（另开一个终端窗口跑，不影响推理任务）：
  python monitor_rbt3.py              # 每 10 秒刷新
  python monitor_rbt3.py --every 5    # 每 5 秒刷新
  python monitor_rbt3.py --once       # 只打印一次快照（适合脚本调用）

判断"卡住"：进度文件超过 10 分钟没更新即标黄警告（正常节奏 1~3 分钟/组）。
退出：Ctrl+C。
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT = os.path.normpath(os.path.join(HERE, "..", "data", "result"))
PROG = os.path.join(RESULT, "infer_rbt3_progress.json")
DETAIL = os.path.join(RESULT, "topic_sentiment_detail_rbt3.csv")
SUMMARY = os.path.join(RESULT, "topic_sentiment_review_summary_rbt3.csv")

TOTAL_ZH = 1_656_752          # 中文评论总数（zh-Hans + zh-Hant）
TOTAL_PAIRS = TOTAL_ZH * 11
STALE_S = 600                 # 进度文件超过 600 秒未更新 = 疑似卡住


def read_progress():
    if not os.path.exists(PROG):
        return None
    try:
        with open(PROG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def mb(path):
    try:
        return os.path.getsize(path) / 1e6
    except OSError:
        return 0.0


def gpu_info():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        util, mem = out.stdout.strip().split(",")
        return f"{util.strip()}% / {mem.strip()}MB"
    except Exception:
        return "n/a"


def snapshot(prev=None):
    """prev = 上次快照 dict，用于算瞬时速率。返回 (文本, 当前状态供下轮差分)。"""
    now = time.time()
    prog = read_progress()
    lines = []
    if prog is None:
        lines.append("进度文件不存在 —— 任务未启动或第一个 row group 尚未完成（首组约需几分钟）")
        return "\n".join(lines), {"t": now, "n_pairs": 0}

    rg = prog.get("next_row_group", 0)
    total_g = prog.get("total_groups", 75)
    n_zh = prog.get("n_zh", 0)
    n_pairs = prog.get("n_pairs", 0)
    elapsed = prog.get("elapsed_s", 0)
    age = now - os.path.getmtime(PROG)

    # 进度条（按中文评论数）
    frac = min(1.0, n_zh / TOTAL_ZH)
    bar_len = 30
    filled = int(frac * bar_len)
    bar = "#" * filled + "-" * (bar_len - filled)

    # 瞬时速率与 ETA（按两次快照差分）
    inst = ""
    eta = "估算中…"
    if prev and prev.get("n_pairs") and now > prev.get("t", 0):
        dt = now - prev["t"]
        dp = n_pairs - prev["n_pairs"]
        if dp > 0 and dt > 0:
            rate = dp / dt
            inst = f"{rate:.0f} 对/s"
            remain = (TOTAL_PAIRS - n_pairs) / rate
            eta_h, m = divmod(remain / 3600, 1)
            eta = f"约 {int(eta_h)} 小时 {int(m*60)} 分钟"
    if elapsed > 0 and not inst:
        rate = n_pairs / elapsed
        remain = (TOTAL_PAIRS - n_pairs) / max(rate, 1)
        eta_h, m = divmod(remain / 3600, 1)
        eta = f"约 {int(eta_h)} 小时 {int(m*60)} 分钟（均值）"

    lines.append(f"[{bar}] {frac*100:5.1f}%   row group {rg}/{total_g}")
    lines.append(f"中文评论: {n_zh:,}/{TOTAL_ZH:,}   前向: {n_pairs:,}/{TOTAL_PAIRS:,}")
    lines.append(f"速率: {inst or f'{n_pairs/max(elapsed,1):.0f} 对/s(均值)'}   剩余: {eta}")
    lines.append(f"输出: 明细 {mb(DETAIL):.1f} MB / 汇总 {mb(SUMMARY):.1f} MB   GPU: {gpu_info()}")

    if age > STALE_S:
        lines.append(f"⚠️ 警告：进度文件 {age/60:.0f} 分钟没更新，疑似卡住！")
        lines.append("   确认后杀进程并 --resume 续跑（最多丢 3 分钟进度）")
    else:
        lines.append(f"心跳: 进度文件 {age:.0f} 秒前更新（正常节奏 1~3 分钟）")
    return "\n".join(lines), {"t": now, "n_pairs": n_pairs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=10, help="刷新间隔秒数")
    ap.add_argument("--once", action="store_true", help="只打印一次")
    args = ap.parse_args()

    prev = None
    while True:
        text, prev = snapshot(prev)
        if args.once:
            print(text)
            break
        os.system("cls" if os.name == "nt" else "clear")
        print(text)
        print("\n(Ctrl+C 退出监控，不影响推理任务)")
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            print("\n监控已退出")
            break


if __name__ == "__main__":
    main()
