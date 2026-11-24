# -*- coding: utf-8 -*-
"""从标注集分层抽取金标样本（每游戏等量+随机），供助手逐条语义标注。"""
import csv, os, random, argparse
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
SRC = os.path.join(ANN, "annotation_tasks.csv")
OUT = os.path.join(ANN, "gold_to_annotate.csv")

ap = argparse.ArgumentParser()
ap.add_argument("--per-game", type=int, default=20)
ap.add_argument("--seed", type=int, default=42)
args = ap.parse_args()

rows = []
with open(SRC, encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        txt = (r.get("review_text") or "").strip()
        if not (15 <= len(txt) <= 220):   # 太短信息少、太长难标
            continue
        rows.append(r)

by_game = defaultdict(list)
for r in rows:
    by_game[r.get("game_name_zh") or r.get("game_name")].append(r)

random.seed(args.seed)
picked = []
for g, lst in sorted(by_game.items()):
    random.shuffle(lst)
    picked.extend(lst[: args.per_game])

with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["idx", "review_id", "game", "text", "rule_aspects"])
    for i, r in enumerate(picked, 1):
        w.writerow([i, r["review_id"], r.get("game_name_zh") or r.get("game_name"),
                    r["review_text"], r.get("rule_aspects_json", "")])

print(f"抽取 {len(picked)} 条 -> {OUT}")
print("按游戏:", {g: min(len(v), args.per_game) for g, v in sorted(by_game.items())})
