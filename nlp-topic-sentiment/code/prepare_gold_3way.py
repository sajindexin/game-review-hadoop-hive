# -*- coding: utf-8 -*-
"""
金标三分类训练数据：让 RBT3 学会"该评论是否谈论某主题"。
- 评论中 gold 标注的主题 -> 对应情感 positive/negative（金标里无 neutral）
- 评论未提及的主题 -> neutral（下采样，控制正负比例）
这样模型能自己判别主题相关性，不依赖规则召回门控。
输出 review_text,aspect,sentiment 三列，8:2 按评论划分 train/eval。
"""
import os, csv, json, random, argparse
from collections import defaultdict

from split_utils import group_by_review, split_grouped

HERE = os.path.dirname(os.path.abspath(__file__))
ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
TASKS = os.path.join(ANN, "annotation_tasks.csv")


def load_gold():
    """从标注集 gold_aspects_json 读取主题层金标（唯一权威来源）。

    这样 merge_gold.py 合并进来的新增/修改标注都会生效；不再依赖
    gold_annotations.py 里的固定 GOLD 字典。
    返回 {review_id(int): [(aspect, sentiment), ...]}
    """
    gold = {}
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            gj = (r.get("gold_aspects_json") or "").strip()
            if not gj:
                continue
            try:
                items = json.loads(gj)
            except Exception:
                continue
            pairs = [(a["aspect"], a["sentiment"]) for a in items
                     if a.get("aspect") and a.get("sentiment") in ("positive", "negative", "neutral")]
            # 空数组 [] 表示"该评论已标注、但无可标注主题"，也是一条金标评论，
            # 需保留（旧 GOLD 字典同样含空列表条目），否则训练评论数会少 4 条。
            gold[int(r["review_id"])] = pairs
    return gold


def build(neg_per_review=3, ratio=0.8, seed=42):
    """按评论切分主题层三分类数据，返回 (train_reviews, eval_reviews)。

    prepare_rbt3_data_v2.py --source gold 直接调用本函数，避免按三元组切分导致
    同一条评论同时进入 train/eval。
    """
    from lexicon import ASPECTS
    asp_ids = list(ASPECTS.keys())

    gold_all = load_gold()
    texts = {}
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = int(r["review_id"])
            if rid in gold_all:
                texts[rid] = r["review_text"].strip()

    random.seed(seed)
    reviews = []
    for rid, text in texts.items():
        gold = dict(gold_all[rid])  # aspect -> sentiment（同一 aspect 多条时取最后一条）
        pos_aspects = set(gold.keys())
        neg_candidates = [a for a in asp_ids if a not in pos_aspects]
        random.shuffle(neg_candidates)
        sampled_neg = sorted(neg_candidates[:neg_per_review])
        triples = [(text, a, gold[a]) for a in sorted(pos_aspects)]
        triples += [(text, a, "neutral") for a in sampled_neg]
        reviews.append((rid, triples))

    # 按「评论组」整组切分：把同一个 review_id 与文本完全相同的评论连成一组。
    # 只按 review_id 切分不够——标注集里存在不同 review_id、文本一字不差的两条评论
    # （如 93940703 与 33393799），分开后会分别落进 train/eval，训练前置校验必然拒绝。
    nodes = [(rid, text, 0, 0) for rid, text in texts.items()]
    groups = group_by_review(nodes)
    review_map = dict(reviews)
    grouped = {root: [(n[0], review_map[n[0]]) for n in ns] for root, ns in groups.items()}
    train, ev, target, total = split_grouped(grouped, ratio, seed)

    # 自检：切分结果必须满足"评论 ID 与文本双双不相交"，否则后面的训练一定会被拦。
    tr_ids = {rid for rid, _ in train}
    ev_ids = {rid for rid, _ in ev}
    tr_tx = {t for _rid, rv in train for t, _a, _s in rv}
    ev_tx = {t for _rid, rv in ev for t, _a, _s in rv}
    print(f"    按组切分: {len(groups)} 组 / {total} 条评论，目标 train {target} 条")
    print(f"    train/eval = {len(train)}/{len(ev)} 条评论 | "
          f"评论 ID 重叠 {len(tr_ids & ev_ids)} | 文本重叠 {len(tr_tx & ev_tx)}")
    if tr_ids & ev_ids or tr_tx & ev_tx:
        raise SystemExit("[ERROR] 金标切分仍存在跨集合样本，请检查 split_utils.group_by_review。")

    def write(path, revs):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            # review_id 必须落盘：留出集 ID 应从训练数据直接取，
            # 不能再靠 review_text 回查标注集（证据文本与整条评论常不相等，回查会得到空集）。
            w.writerow(["review_id", "review_text", "aspect", "sentiment"])
            for rid, triples in revs:
                for text, a, sent in triples:
                    w.writerow([rid, text, a, sent])

    tp = os.path.join(ANN, "rbt3_aspect_train.csv")
    ep = os.path.join(ANN, "rbt3_aspect_eval.csv")
    write(tp, train)
    write(ep, ev)

    # 留出集评论 id：评估脚本优先读取模型目录里的同名文件
    with open(os.path.join(ANN, "gold_eval_ids.json"), "w", encoding="utf-8") as f:
        json.dump([rid for rid, _ in ev], f)
    return train, ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neg-per-review", type=int, default=3,
                    help="每条评论抽多少个未提及主题作 neutral 负例")
    ap.add_argument("--ratio", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    train, ev = build(args.neg_per_review, args.ratio, args.seed)

    n_pos = sum(1 for _rid, rv in train + ev for t in rv if t[2] != "neutral")
    n_neu = sum(1 for _rid, rv in train + ev for t in rv if t[2] == "neutral")
    print(f"评论: {len(train) + len(ev)} (train {len(train)} / eval {len(ev)})")
    print(f"三元组: {n_pos} 正/负 + {n_neu} neutral -> {os.path.join(ANN, 'rbt3_aspect_train.csv')}")
    print(f"留出集评论 id -> {os.path.join(ANN, 'gold_eval_ids.json')}")
    from collections import Counter
    print("情感分布:", dict(Counter(t[2] for _rid, rv in train + ev for t in rv)))


if __name__ == "__main__":
    main()
