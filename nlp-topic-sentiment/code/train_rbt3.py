# -*- coding: utf-8 -*-
"""
RBT3 方面级情感分类微调（多标签多分类）

模型：hfl/rbt3（哈工大 RoBERTa-small 中文，~39M 参数）
任务：给定一条评论，对每个主题输出 positive/negative/neutral。
实现：把"方面 + 评论"拼成输入 [CLS] 主题提示 [SEP] 评论 [SEP]，
      每个方面独立做 3 分类（共享一个 RBT3 编码器），
      这样天然支持"一条评论多主题、情感可相反"，且比 11×3 个二分类器更省。

训练数据：来自标注金标 gold_labels.csv（列：review_text, aspect, sentiment）。
         可用 annotation_tasks.csv 人工标注后转换而来（见 prepare_rbt3_data.py）。

依赖安装（本机 GPU 可选）：
  pip install -i https://mirrors.aliyun.com/pypi/simple torch transformers scikit-learn

用法：
  python train_rbt3.py --train data/annotation/rbt3_train.csv --eval data/annotation/rbt3_eval.csv --epochs 3
  python train_rbt3.py --help
"""
from __future__ import annotations
import os
import csv
import argparse
import random
import numpy as np

# 国内网络：默认走 hf-mirror 镜像（可用 HF_ENDPOINT 覆盖）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True, help="训练集CSV: review_text,aspect,sentiment")
    ap.add_argument("--eval", default="", help="验证集CSV，同格式")
    ap.add_argument("--model", default="hfl/rbt3", help="预训练模型名或本地路径")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "rbt3_model"))
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


ASPECT_PROMPT = {
    "content": "内容玩法", "level_design": "地图关卡设计", "multiplayer": "联机服务器",
    "graphics": "画质美术", "performance": "性能优化", "story": "剧情叙事",
    "controls": "操作手感", "monetization": "付费氪金", "cheating": "外挂反作弊",
    "audio": "音效音乐", "community": "运营社区",
}
SENT2ID = {"negative": 0, "neutral": 1, "positive": 2}
ID2SENT = {v: k for k, v in SENT2ID.items()}


def read_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            text = (r.get("review_text") or "").strip()
            aspect = (r.get("aspect") or "").strip()
            sent = (r.get("sentiment") or "").strip()
            if text and aspect in ASPECT_PROMPT and sent in SENT2ID:
                rows.append((text, aspect, SENT2ID[sent]))
    return rows


def build_inputs(rows):
    """把 (text, aspect, label) 转成 (句子对, 标签)。"""
    data = []
    for text, aspect, label in rows:
        prompt = f"评价方面：{ASPECT_PROMPT[aspect]}"
        data.append((prompt, text, label))
    return data


def main():
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    try:
        import torch
        from torch.utils.data import Dataset, DataLoader
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except ImportError as e:
        print("缺少依赖，请先运行：")
        print("  pip install -i https://mirrors.aliyun.com/pypi/simple torch transformers scikit-learn")
        raise SystemExit(f"ImportError: {e}")

    train_rows = read_csv(args.train)
    eval_rows = read_csv(args.eval) if args.eval and os.path.exists(args.eval) else []
    print(f"训练样本: {len(train_rows)}, 验证样本: {len(eval_rows)}")
    if not train_rows:
        raise SystemExit("训练集为空，请检查 --train 路径与CSV列名(review_text,aspect,sentiment)")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

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
            p, t, label = self.data[i]
            enc = tokenizer(p, t, truncation=True, max_length=args.max_len,
                            padding="max_length", return_tensors="pt")
            return {k: v.squeeze(0) for k, v in enc.items()}, label

    train_loader = DataLoader(ABSAData(build_inputs(train_rows)),
                              batch_size=args.batch_size, shuffle=True)
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

    # 验证
    if eval_rows:
        from sklearn.metrics import classification_report, f1_score
        eval_loader = DataLoader(ABSAData(build_inputs(eval_rows)), batch_size=args.batch_size)
        preds, golds = [], []
        model.eval()
        with torch.no_grad():
            for batch, labels in eval_loader:
                batch = {k: v.to(device) for k, v in batch.items()}
                logits = model(**batch).logits
                preds.extend(logits.argmax(-1).cpu().tolist())
                golds.extend(labels.tolist())
        print("\n=== 验证集效果 ===")
        present = sorted(set(golds) | set(preds))
        print(classification_report(golds, preds, labels=present,
              target_names=[ID2SENT[i] for i in present], digits=4, zero_division=0))
        print("macro-F1:", round(f1_score(golds, preds, average="macro"), 4))

    os.makedirs(args.out, exist_ok=True)
    model.save_pretrained(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"\n模型已保存: {args.out}")


if __name__ == "__main__":
    main()
