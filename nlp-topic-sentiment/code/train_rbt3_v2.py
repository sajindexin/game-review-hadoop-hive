# -*- coding: utf-8 -*-
"""
RBT3 两层标签微调（v2）：同一套 hfl/rbt3 编码器，支持两种任务

  --task aspect : 第一层，通用主题情感（11 类）
                  prompt = "评价方面：内容玩法"，输入 [prompt, 评论]，3 分类
  --task item   : 第二层，具体更新条目情感（75 项，游戏特定）
                  prompt = "这条评论是否在讨论该更新？姜岛探索：探索姜岛农场..."
                  输入 [prompt, 评论]，3 分类（neg/neutral/pos）

为什么条目层用"配对分类"而不是 75 类分类？
  75 项条目分属 12 款游戏，每项样本极少（很多只有几十条），直接做 75 类分类
  会严重长尾。配对分类把问题变成"评论与某条更新是否相关、态度如何"，
  所有条目共享同一个编码器，样本量利用率高，且天然支持一条评论命中多条更新。

推理时对一条评论：
  主题层遍历 11 个 prompt；条目层只遍历该游戏 appid 的候选条目（4~19 个），
  用 APPID_ITEMS 约束，避免跨游戏误判。

依赖：
  pip install -i https://mirrors.aliyun.com/pypi/simple torch transformers scikit-learn

用法：
  python train_rbt3_v2.py --task aspect --train ../data/annotation/rbt3_aspect_train.csv --eval ../data/annotation/rbt3_aspect_eval.csv
  python train_rbt3_v2.py --task item   --train ../data/annotation/rbt3_item_train.csv   --eval ../data/annotation/rbt3_item_eval.csv
"""
from __future__ import annotations
import os
import csv
import sys
import json
import argparse
import random
import numpy as np

# 国内网络：HuggingFace 直连常超时，默认走 hf-mirror 镜像（可用 HF_ENDPOINT 覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from item_lexicon import ITEMS

ASPECT_PROMPT = {
    "content": "内容玩法", "level_design": "地图关卡设计", "multiplayer": "联机服务器",
    "graphics": "画质美术", "performance": "性能优化", "story": "剧情叙事",
    "controls": "操作手感", "monetization": "付费氪金", "cheating": "外挂反作弊",
    "audio": "音效音乐", "community": "运营社区",
}
SENT2ID = {"negative": 0, "neutral": 1, "positive": 2}
ID2SENT = {v: k for k, v in SENT2ID.items()}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["aspect", "item"], required=True)
    ap.add_argument("--train", required=True)
    ap.add_argument("--eval", default="")
    ap.add_argument("--model", default="hfl/rbt3")
    ap.add_argument("--out", default="")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=160)
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def item_prompt(item_id):
    meta = ITEMS.get(item_id, {})
    title = meta.get("title", item_id)
    desc = (meta.get("description") or "")[:60]
    return f"这条评论是否在讨论该游戏更新：{title}。{desc}"


def read_csv(path, task):
    rows = []
    id_col = "aspect" if task == "aspect" else "item_id"
    valid = ASPECT_PROMPT if task == "aspect" else set(ITEMS.keys())
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            text = (r.get("review_text") or "").strip()
            key = (r.get(id_col) or "").strip()
            sent = (r.get("sentiment") or "").strip()
            if text and key in valid and sent in SENT2ID:
                rows.append((text, key, SENT2ID[sent]))
    return rows


def make_prompt(task, key):
    return f"评价方面：{ASPECT_PROMPT[key]}" if task == "aspect" else item_prompt(key)


def main():
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)
    if not args.out:
        args.out = os.path.join(HERE, "..", "data",
                                "rbt3_model_aspect" if args.task == "aspect" else "rbt3_model_item")

    try:
        import torch
        from torch.utils.data import Dataset, DataLoader
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except ImportError as e:
        print("缺少依赖，请先运行：")
        print("  pip install -i https://mirrors.aliyun.com/pypi/simple torch transformers scikit-learn")
        raise SystemExit(f"ImportError: {e}")

    train_rows = read_csv(args.train, args.task)
    eval_rows = read_csv(args.eval, args.task) if args.eval and os.path.exists(args.eval) else []
    print(f"任务: {args.task} | 训练样本: {len(train_rows)}, 验证样本: {len(eval_rows)}")
    if not train_rows:
        raise SystemExit(f"训练集为空，检查 --train 与列名(review_text,"
                         f"{'aspect' if args.task=='aspect' else 'item_id'},sentiment)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")
    if device.type == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model, num_labels=3, id2label=ID2SENT, label2id=SENT2ID
    ).to(device)

    class ABSAData(Dataset):
        def __init__(self, data):
            self.data = data
        def __len__(self):
            return len(self.data)
        def __getitem__(self, i):
            text, key, label = self.data[i]
            enc = tokenizer(make_prompt(args.task, key), text, truncation=True,
                            max_length=args.max_len, padding="max_length", return_tensors="pt")
            return {k: v.squeeze(0) for k, v in enc.items()}, label

    train_loader = DataLoader(ABSAData(train_rows), batch_size=args.batch_size, shuffle=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)

    model.train()
    for epoch in range(args.epochs):
        total_loss = 0.0
        for batch, labels in train_loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            labels = labels.to(device)
            out = model(**batch, labels=labels)
            loss = out.loss
            opt.zero_grad()
            loss.backward()
            opt.step()
            total_loss += loss.item()
        print(f"epoch {epoch+1}/{args.epochs}  loss={total_loss/len(train_loader):.4f}")

    if eval_rows:
        from sklearn.metrics import classification_report, f1_score
        eval_loader = DataLoader(ABSAData(eval_rows), batch_size=args.batch_size)
        preds, golds = [], []
        model.eval()
        with torch.no_grad():
            for batch, labels in eval_loader:
                batch = {k: v.to(device) for k, v in batch.items()}
                logits = model(**batch).logits
                preds.extend(logits.argmax(-1).cpu().tolist())
                golds.extend(labels.tolist())
        print("\n=== 验证集效果 ===")
        # 银标去掉 neutral 时验证集可能只有 2 类，target_names 需按实际类别生成
        present = sorted(set(golds) | set(preds))
        print(classification_report(golds, preds, labels=present,
              target_names=[ID2SENT[i] for i in present], digits=4, zero_division=0))
        print("macro-F1:", round(f1_score(golds, preds, average="macro"), 4))

    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    # 保存任务元信息，推理时自动识别
    with open(os.path.join(args.out, "task_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"task": args.task, "model": args.model, "max_len": args.max_len}, f)

    # 把本次 eval 对应的评论 ID 写进模型目录，评估脚本优先读它，避免模型与留出集错配
    ids_path = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "gold_eval_ids.json"))
    holdout_ids = []
    if args.task == "aspect" and os.path.exists(ids_path):
        # 主题层 eval 由 prepare_gold_3way 按评论切分，gold_eval_ids.json 是权威来源
        with open(ids_path, encoding="utf-8") as f:
            holdout_ids = json.load(f)
    elif args.eval and os.path.exists(args.eval):
        # 条目层/自定义 eval：按原文回查 review_id
        eval_texts = set()
        with open(args.eval, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("review_text"):
                    eval_texts.add(row["review_text"].strip())
        ann = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "annotation_tasks.csv"))
        if os.path.exists(ann) and eval_texts:
            with open(ann, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    if (row.get("review_text") or "").strip() in eval_texts:
                        try:
                            holdout_ids.append(int(row["review_id"]))
                        except (TypeError, ValueError):
                            pass
    if holdout_ids:
        with open(os.path.join(args.out, "holdout_ids.json"), "w", encoding="utf-8") as f:
            json.dump(sorted(set(holdout_ids)), f)
        print(f"留出集 ID 已随模型保存: {len(set(holdout_ids))} 条")

    print(f"\n模型已保存: {args.out}")


if __name__ == "__main__":
    main()
