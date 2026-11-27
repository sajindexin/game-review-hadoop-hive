# -*- coding: utf-8 -*-
"""
构造人工标注集：从中文评论按游戏分层抽样，生成两层标签的待标注 CSV 和 gold 样例。

两层标签：
  第一层 通用主题(11类)：gold_aspects_json
  第二层 具体更新条目(75项)：gold_items_json（只在该游戏的候选条目里标注）

输出：
  data/annotation/annotation_tasks.csv   待标注任务（含规则预填 + 候选条目列表）
  data/annotation/tasks_embed.js         本地标注工具内嵌数据（含候选条目标题）
  data/annotation/gold_samples.csv       gold 演示样例（含条目层）
"""
from __future__ import annotations
import os
import sys
import csv
import json
import random
import argparse
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
DEFAULT_PARQUET = r"D:\QQFiles\team_package_v1\team_package_v1\游戏评论交付\releases\v1\data\cleaned_reviews.parquet"

sys.path.insert(0, HERE)
from rule_labeler import label_review
from item_lexicon import ITEMS, APPID_ITEMS, APPID_GAME, ITEM2ASPECT

COLS = ["appid", "game_name", "review_id", "language_std", "review_text",
        "created_date", "game_version", "voted_up", "review_len"]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=DEFAULT_PARQUET)
    ap.add_argument("--per-game", type=int, default=60, help="每款游戏抽取条数")
    ap.add_argument("--item-quota", type=int, default=20, help="每款游戏额外抽取的『命中条目』评论条数")
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def candidate_list(appid):
    """该游戏的候选条目（id/标题/通用主题），供本地标注工具展示。"""
    out = []
    for iid in APPID_ITEMS.get(str(appid), []):
        meta = ITEMS[iid]
        out.append({"id": iid, "title": meta.get("title", ""), "aspect": ITEM2ASPECT.get(iid, "")})
    return out


def main():
    args = parse_args()
    os.makedirs(OUT, exist_ok=True)
    random.seed(args.seed)
    pf = pq.ParquetFile(args.parquet)

    by_game: dict[str, list[dict]] = {}
    for rg in range(pf.metadata.num_row_groups):
        t = pf.read_row_group(rg, columns=COLS).to_pydict()
        for j in range(len(t["appid"])):
            if t["language_std"][j] not in ("zh-Hans", "zh-Hant"):
                continue
            ln = t["review_len"][j] or 0
            if not (20 <= ln <= 300):
                continue
            txt = (t["review_text"][j] or "").strip()
            if not txt:
                continue
            g = t["game_name"][j]
            by_game.setdefault(g, []).append({
                "review_id": t["review_id"][j], "appid": t["appid"][j],
                "game_name": g, "created_date": t["created_date"][j],
                "game_version": t["game_version"][j] or "",
                "voted_up": t["voted_up"][j], "review_text": txt,
            })

    tasks = []
    for g, items in sorted(by_game.items()):
        # 1) 主题层：优先带主题命中的评论
        tagged = [it for it in items if label_review(it["review_text"])["n_aspects"] > 0]
        pool_tagged = random.sample(tagged, min(args.per_game, len(tagged)))
        need = args.per_game - len(pool_tagged)
        rest = [it for it in items if it not in pool_tagged]
        pool_random = random.sample(rest, min(need, len(rest))) if need > 0 else []

        # 2) 条目层：额外抽取命中具体条目的评论（保证标注集覆盖第二层）
        item_hit = [it for it in items
                    if label_review(it["review_text"], appid=it["appid"])["n_items"] > 0]
        pool_item = random.sample(item_hit, min(args.item_quota, len(item_hit)))

        chosen = {it["review_id"]: it for it in pool_tagged + pool_random}
        for it in pool_item:
            chosen.setdefault(it["review_id"], it)

        for it in chosen.values():
            res = label_review(it["review_text"], appid=it["appid"])
            row = dict(it)
            row["n_aspects"] = res["n_aspects"]
            row["rule_aspects_json"] = json.dumps(res["aspects"], ensure_ascii=False)
            row["rule_items_json"] = json.dumps(res["items"], ensure_ascii=False)
            row["rule_primary"] = res["primary_sentiment"]
            row["game_name_zh"] = APPID_GAME.get(str(it["appid"]), g)
            row["gold_aspects_json"] = ""
            row["gold_items_json"] = ""
            row["annotator"] = ""
            row["annotate_note"] = ""
            tasks.append(row)

    out_path = os.path.join(OUT, "annotation_tasks.csv")
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(tasks[0].keys()))
        w.writeheader()
        w.writerows(tasks)

    # 本地标注工具内嵌数据：每条任务带候选条目
    embed = []
    for r in tasks:
        embed.append({
            "id": r["review_id"], "game": r["game_name_zh"], "date": r["created_date"],
            "ver": r["game_version"], "up": r["voted_up"], "text": r["review_text"],
            "rule": r["rule_aspects_json"], "rule_items": r["rule_items_json"],
            "cand": candidate_list(r["appid"]),
        })
    embed_path = os.path.join(OUT, "tasks_embed.js")
    with open(embed_path, "w", encoding="utf-8") as f:
        f.write("const TASKS = ")
        json.dump(embed, f, ensure_ascii=False)
        f.write(";")
    print(f"  写出 {embed_path} ({os.path.getsize(embed_path)//1024} KB)")

    # gold 样例（含条目层）
    gold = [
        {
            "review_id": "DEMO-001", "appid": "413150", "review_text": "新关卡好玩，但联机老掉线",
            "gold_aspects_json": '[{"aspect":"content","sentiment":"positive"},'
                                 '{"aspect":"multiplayer","sentiment":"negative"}]',
            "gold_items_json": "[]",
            "note": "主题层示例：内容正向 + 联机负向；本句无具体条目专名",
        },
        {
            "review_id": "DEMO-002", "appid": "413150",
            "review_text": "姜岛探索很好玩，但是八人联机老是掉线",
            "gold_aspects_json": '[{"aspect":"level_design","sentiment":"positive"},'
                                 '{"aspect":"multiplayer","sentiment":"negative"}]',
            "gold_items_json": '[{"item_id":"S15_01","sentiment":"positive"},'
                               '{"item_id":"S16_04","sentiment":"negative"}]',
            "note": "两层示例：姜岛探索正向 + 八人联机负向（一句多条目、情感相反）",
        },
        {
            "review_id": "DEMO-003", "appid": "252490",
            "review_text": "海浪效果太惊艳了，拖船也很有意思",
            "gold_aspects_json": '[{"aspect":"graphics","sentiment":"positive"}]',
            "gold_items_json": '[{"item_id":"D01","sentiment":"positive"},'
                               '{"item_id":"D02","sentiment":"positive"}]',
            "note": "条目层示例：Rust 深海更新的海浪改版 + 拖船，均正向",
        },
        {
            "review_id": "DEMO-004", "appid": "359550",
            "review_text": "盾牌重做之后平衡性一坨",
            "gold_aspects_json": '[{"aspect":"cheating","sentiment":"negative"}]',
            "gold_items_json": '[{"item_id":"359550_20240312_02","sentiment":"negative"}]',
            "note": "条目层示例：彩虹六号盾牌机制重做，负向",
        },
        {
            "review_id": "DEMO-005", "appid": "730",
            "review_text": "姜岛探索很好玩",
            "gold_aspects_json": '[{"aspect":"level_design","sentiment":"positive"}]',
            "gold_items_json": "[]",
            "note": "跨游戏反例：CS2 评论里出现『姜岛』不算命中星露谷条目，gold_items 为空",
        },
    ]
    gold_path = os.path.join(OUT, "gold_samples.csv")
    with open(gold_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["review_id", "appid", "review_text",
                                          "gold_aspects_json", "gold_items_json", "note"])
        w.writeheader()
        w.writerows(gold)

    has_item = sum(1 for r in tasks if json.loads(r["rule_items_json"]))
    print(f"标注任务数: {len(tasks)}（{len(by_game)} 款游戏）")
    print(f"  其中命中具体条目的任务: {has_item}（条目层标注重点）")
    print(f"输出: {out_path}")
    print(f"输出: {gold_path}")


if __name__ == "__main__":
    main()
