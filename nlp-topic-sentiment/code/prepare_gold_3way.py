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
from gold_annotations import GOLD

HERE = os.path.dirname(os.path.abspath(__file__))
ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
TASKS = os.path.join(ANN, "annotation_tasks.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--neg-per-review", type=int, default=5,
                    help="每条评论抽多少个未提及主题作 neutral 负例")
    ap.add_argument("--ratio", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    from lexicon import ASPECTS
    asp_ids = list(ASPECTS.keys())

    # 读评论文本
    texts = {}
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = int(r["review_id"])
            if rid in GOLD:
                texts[rid] = r["review_text"].strip()

    random.seed(args.seed)
    reviews = []
    for rid, text in texts.items():
        gold = dict(GOLD[rid])  # aspect -> sentiment
        pos_aspects = set(gold.keys())
        neg_candidates = [a for a in asp_ids if a not in pos_aspects]
        random.shuffle(neg_candidates)
        sampled_neg = neg_candidates[: args.neg_per_review]
        triples = [(text, a, gold[a]) for a in pos_aspects]
        triples += [(text, a, "neutral") for a in sampled_neg]
        reviews.append((rid, triples))

    # 按评论 8:2 划分
    random.shuffle(reviews)
    n_train = int(len(reviews) * args.ratio)
    train, ev = reviews[:n_train], reviews[n_train:]

    def write(path, revs):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f); w.writerow(["review_text", "aspect", "sentiment"])
            for rid, triples in revs:
                for t in triples: w.writerow(t)

    tp = os.path.join(ANN, "rbt3_aspect_train.csv")
    ep = os.path.join(ANN, "rbt3_aspect_eval.csv")
    write(tp, train); write(ep, ev)

    # 输出 eval 评论 id，供“严格留出集”评估使用（模型训练时没见过这些评论）
    with open(os.path.join(ANN, "gold_eval_ids.json"), "w", encoding="utf-8") as f:
        json.dump([rid for rid, _ in ev], f)

    n_pos = sum(1 for rid, rv in train+ev for t in rv if t[2] != "neutral")
    n_neu = sum(1 for rid, rv in train+ev for t in rv if t[2] == "neutral")
    print(f"评论: {len(reviews)} (train {len(train)} / eval {len(ev)})")
    print(f"三元组: {n_pos} 正/负 + {n_neu} neutral -> {tp}")
    print(f"留出集评论 id -> {os.path.join(ANN, 'gold_eval_ids.json')}")
    from collections import Counter
    print("情感分布:", dict(Counter(t[2] for rid, rv in reviews for t in rv)))


if __name__ == "__main__":
    main()
