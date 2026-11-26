# -*- coding: utf-8 -*-
"""
RBT3 两层标签训练数据准备（v2）

第一层(aspect)：review_text, aspect, sentiment  —— 通用主题 11 类
第二层(item)   ：review_text, item_id, sentiment —— 具体更新条目 75 项（按 appid 约束）

数据来源两种：
  gold : annotation_tasks.csv 里人工填好的 gold_aspects_json / gold_items_json
  rule : 规则弱标注结果（银标，冷启动演示用）
         - 主题层来自 topic_sentiment_detail.csv
         - 条目层来自 item_sentiment_detail.csv，需回填原文（用 evidence）

gold_items_json 格式：
  [{"item_id":"S15_01","sentiment":"positive"},{"item_id":"S16_04","sentiment":"negative"}]

输出（data/annotation/）：
  rbt3_aspect_train.csv / rbt3_aspect_eval.csv
  rbt3_item_train.csv   / rbt3_item_eval.csv
"""
from __future__ import annotations
import os
import csv
import gzip
import json
import random
import argparse
import pyarrow.csv as pacsv

HERE = os.path.dirname(os.path.abspath(__file__))
ANN_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
RESULT_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "result"))


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["gold", "rule"], default="gold")
    ap.add_argument("--ratio", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--include-neutral", action="store_true",
                    help="是否保留 neutral 样本（默认去掉以降低噪声；gold 模式固定三分类）")
    ap.add_argument("--neg-per-review", type=int, default=3,
                    help="gold 模式下每条评论抽多少个未提及主题作 neutral 负例")
    ap.add_argument("--limit", type=int, default=0,
                    help="每层最多保留多少三元组（随机抽样），0=全部；银标演示建议 20000")
    ap.add_argument("--item-only", action="store_true",
                    help="只生成条目层银标数据，不触碰主题层金标 train/eval（推荐用于银标演示）")
    return ap.parse_args()


def _resolve_result(result_dir, name):
    """返回实际存在的结果表路径：优先未压缩 csv，其次 .csv.gz（干净 clone 只有 gz）。"""
    for cand in (os.path.join(result_dir, name), os.path.join(result_dir, name + ".gz")):
        if os.path.exists(cand):
            return cand
    return ""


def _open_maybe_gz(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return open(path, encoding="utf-8-sig", newline="")


def _sample_by_group(triples, limit):
    """按 review 分组抽样到不超过 limit 条三元组（整组取，不拆散评论）。"""
    if len(triples) <= limit:
        return triples
    groups = group_by_review(triples)
    keys = sorted(groups.keys())
    random.shuffle(keys)
    picked = []
    for k in keys:
        grp = groups[k]
        if len(picked) + len(grp) > limit and picked:
            break
        picked.extend(grp)
        if len(picked) >= limit:
            break
    return picked


def split_write(triples, prefix, ratio, suffix=""):
    """按『分组』整组切分，suffix='' 写金标文件；suffix='_silver' 写银标独立文件。

    一条评论会命中多个主题/条目，三元组数远多于评论数；若直接按三元组随机切分，
    同一条评论的不同条目会分别落进 train 和 eval，等于让模型背答案。
    这里用并查集把下面两类节点连成一组，整组同进同出：
      - 同一个原始 review_id 的所有样本
      - 文本完全相同的样本（不同评论也可能有相同证据文本）
    这样 train/eval 的评论 ID 集合与文本集合都严格不相交。
    """
    total = len(triples)
    size = int(total * ratio)
    groups = group_by_review(triples)
    keys = sorted(groups.keys())
    random.shuffle(keys)

    train, ev = [], []
    for k in keys:
        grp = groups[k]
        # 贪心装箱：train 未到目标就继续装，装够后剩余整组进 eval
        if len(train) < size:
            train.extend(grp)
        else:
            ev.extend(grp)

    if not train or not ev:
        raise SystemExit(
            f"[ERROR] 无法按组切分 {prefix} 层数据：共 {total} 条三元组、{len(groups)} 个分组，"
            f"train/eval 至少一侧为空。请增大数据量或检查 review_id 是否缺失。")

    cols = {"aspect": ["review_id", "review_text", "aspect", "sentiment"],
            "item": ["review_id", "review_text", "item_id", "sentiment"]}[prefix]
    for name, rows in [(f"rbt3_{prefix}{suffix}_train.csv", train),
                       (f"rbt3_{prefix}{suffix}_eval.csv", ev)]:
        path = os.path.join(ANN_DIR, name)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(rows)
        print(f"    {name}: {len(rows)}")
    tr_ids = {r[0] for r in train if r[0]}
    ev_ids = {r[0] for r in ev if r[0]}
    tr_tx = {r[1] for r in train}
    ev_tx = {r[1] for r in ev}
    print(f"    分组 {len(groups)} 个 | 评论 ID 重叠 {len(tr_ids & ev_ids)} | "
          f"文本重叠 {len(tr_tx & ev_tx)}")
    return len(train), len(ev)


def group_by_review(triples):
    """并查集：把 review_id 与文本连成连通分量，返回 {组根: [三元组...]}。"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:      # 路径压缩
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for rid, text, _key, _sent in triples:
        # 没有 review_id 时退化为纯按文本分组
        union(("r", rid) if rid else ("t", text), ("t", text))

    groups = {}
    for t in triples:
        rid, text = t[0], t[1]
        node = ("r", rid) if rid else ("t", text)
        groups.setdefault(find(node), []).append(t)
    return groups


def main():
    args = parse_args()
    random.seed(args.seed)
    keep_sent = {"positive", "negative", "neutral"} if args.include_neutral else {"positive", "negative"}

    aspect_triples: list[tuple[str, str, str]] = []
    item_triples: list[tuple[str, str, str]] = []

    if args.source == "gold":
        # 主题层：复用按评论切分的三分类脚本，保证 train/eval 评论零重叠
        from prepare_gold_3way import build as build_gold_3way
        asp_train_reviews, asp_eval_reviews = build_gold_3way(
            args.neg_per_review, args.ratio, args.seed)
        print(f"主题层: 复用 prepare_gold_3way 按评论切分 "
              f"train/eval={len(asp_train_reviews)}/{len(asp_eval_reviews)} 条评论")

        # 条目层：当前仓库没有条目级 gold，明确提示用规则模式验收，不生成空训练文件
        src = os.path.join(ANN_DIR, "annotation_tasks.csv")
        with open(src, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                text = (r.get("review_text") or "").strip()
                ij = (r.get("gold_items_json") or "").strip()
                if not (text and ij):
                    continue
                try:
                    for it in json.loads(ij):
                        if it.get("sentiment") in ("positive", "negative", "neutral"):
                            item_triples.append(
                                (r.get("review_id", "").strip(), text, it["item_id"], it["sentiment"]))
                except Exception:
                    pass
    else:
        # 银标：用标准 csv 流式逐行读，避免 pyarrow 一次性载入大表导致 OOM
        # 大表只提交 .csv.gz，这里统一走 gz 兜底，干净 clone 也能取到数据
        apath = _resolve_result(RESULT_DIR, "topic_sentiment_detail.csv")
        if apath:
            with _open_maybe_gz(apath) as f:
                for r in csv.DictReader(f):
                    sent = (r.get("sentiment") or "").strip()
                    if sent not in keep_sent:
                        continue
                    text = (r.get("evidence") or "").strip()
                    if text:
                        aspect_triples.append(
                            (r.get("review_id", "").strip(), text, r["aspect"], sent))
        # 银标：条目层
        ipath = _resolve_result(RESULT_DIR, "item_sentiment_detail.csv")
        if ipath:
            with _open_maybe_gz(ipath) as f:
                for r in csv.DictReader(f):
                    sent = (r.get("sentiment") or "").strip()
                    if sent not in keep_sent:
                        continue
                    text = (r.get("evidence") or "").strip()
                    if text:
                        item_triples.append(
                            (r.get("review_id", "").strip(), text, r["item_id"], sent))

    # 可选随机抽样（控制训练规模）。按整组抽样，保证同一条评论的样本不被拆散，
    # 否则抽样后 train/eval 的评论结构会与原始分布不一致。
    if args.limit and args.limit > 0:
        aspect_triples = _sample_by_group(aspect_triples, args.limit)
        item_triples = _sample_by_group(item_triples, args.limit)

    print(f"来源: {args.source}")
    if args.source == "gold":
        print("第一层(主题): 已由 prepare_gold_3way 写入 train/eval，留出 ID 同步更新")
        if item_triples:
            print(f"第二层(条目)gold 三元组: {len(item_triples)}")
            split_write(item_triples, "item", args.ratio)
        else:
            for name in ("rbt3_item_train.csv", "rbt3_item_eval.csv"):
                p = os.path.join(ANN_DIR, name)
                if os.path.exists(p):
                    os.remove(p)
                    print(f"  删除空/旧条目训练集: {name}")
            print("第二层(条目): 当前无条目级人工金标，不生成空训练文件。")
            print("  条目层验收请使用规则模式：infer_rbt3_v2.py --rule-fallback")
            print("  如需训练条目模型，请先补充 gold_items_json 再重跑本脚本。")
    else:
        # 银标一律写独立 *_silver 文件，绝不覆盖金标 train/eval 与 gold_eval_ids.json
        n1 = (0, 0)
        if not args.item_only:
            print(f"第一层(主题)银标三元组: {len(aspect_triples)}")
            n1 = split_write(aspect_triples, "aspect", args.ratio, suffix="_silver")
        else:
            print("第一层(主题): --item-only，跳过主题层（金标 train/eval 保持不变）")
        print(f"第二层(条目)银标三元组: {len(item_triples)}")
        n2 = split_write(item_triples, "item", args.ratio, suffix="_silver")
        print(f"\n完成: 主题层银标 train/eval={n1}, 条目层银标 train/eval={n2}")
        print("注意: 银标文件独立保存，金标文件与 gold_eval_ids.json 未被改动；")
        print("      训练时请用 --train/--eval 显式指向银标文件，"
              "留出 ID 将直接取 eval 文件的 review_id 列。")


if __name__ == "__main__":
    main()
