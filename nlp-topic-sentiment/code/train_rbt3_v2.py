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
from datetime import datetime

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
    """读训练/验证数据，返回 [(review_id, text, key, label), ...]。

    review_id 直接取 CSV 里的 review_id 列（prepare_gold_3way / prepare_rbt3_data_v2 都会写出）。
    旧文件没有该列时置空串，由调用方决定是否拒绝——留出集不能靠文本回查标注集来反推，
    因为条目/主题的 evidence 文本只是整条评论的一个片段，回查命中率是 0。
    """
    rows = []
    id_col = "aspect" if task == "aspect" else "item_id"
    valid = ASPECT_PROMPT if task == "aspect" else set(ITEMS.keys())
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            text = (r.get("review_text") or "").strip()
            key = (r.get(id_col) or "").strip()
            sent = (r.get("sentiment") or "").strip()
            if text and key in valid and sent in SENT2ID:
                rows.append(((r.get("review_id") or "").strip(), text, key, SENT2ID[sent]))
    return rows


def make_prompt(task, key):
    return f"评价方面：{ASPECT_PROMPT[key]}" if task == "aspect" else item_prompt(key)


def load_and_validate(args):
    """读入训练/验证数据并完成全部前置校验，返回 (train_rows, eval_rows, holdout_ids)。

    任何一项不通过都直接 SystemExit，且此时还没有创建模型目录、更没有写权重，
    已有模型不会被破坏。校验项：
      1. 训练集非空
      2. --eval 指定的文件存在且解析后非空
      3. train / eval 评论文本集合不相交
      4. train / eval 原始 review_id 集合不相交
      5. eval 必须带 review_id（留出集只能来自本轮实际使用的验证数据）
    """
    train_rows = read_csv(args.train, args.task)
    if not train_rows:
        raise SystemExit(f"训练集为空，检查 --train 与列名(review_id,review_text,"
                         f"{'aspect' if args.task == 'aspect' else 'item_id'},sentiment)")

    eval_rows, holdout_ids = [], []
    if not args.eval:
        print("[提示] 未提供 --eval，本次不产出留出集 ID；严格留出集评估将无法进行。")
        return train_rows, eval_rows, holdout_ids

    if not os.path.exists(args.eval):
        raise SystemExit(f"[ERROR] --eval 文件不存在: {args.eval}")
    eval_rows = read_csv(args.eval, args.task)
    if not eval_rows:
        raise SystemExit(f"[ERROR] 验证集为空或列名不合法: {args.eval}")

    train_texts = {r[1] for r in train_rows}
    eval_texts = {r[1] for r in eval_rows}
    overlap_text = train_texts & eval_texts
    if overlap_text:
        raise SystemExit(
            f"[ERROR] 训练集与验证集有 {len(overlap_text)} 条相同评论文本，不能作为严格留出集。"
            f" 请用 prepare_rbt3_data_v2.py / prepare_gold_3way.py 按评论分组切分。"
        )

    eval_id_list = [r[0] for r in eval_rows]
    missing_id = sum(1 for x in eval_id_list if not x)
    if missing_id:
        raise SystemExit(
            f"[ERROR] 验证集有 {missing_id}/{len(eval_id_list)} 行缺少 review_id 列。"
            f" 留出集 ID 必须来自验证数据本身，不能再靠 review_text 回查标注集"
            f"（证据文本是评论片段，回查命中率为 0，会静默沿用上一轮的旧留出集）。"
            f" 请用当前版本的 prepare_gold_3way.py / prepare_rbt3_data_v2.py 重新生成数据。"
        )

    train_ids = {r[0] for r in train_rows if r[0]}
    eval_ids = {x for x in eval_id_list if x}
    overlap_id = train_ids & eval_ids
    if overlap_id:
        raise SystemExit(
            f"[ERROR] 训练集与验证集有 {len(overlap_id)} 条相同 review_id，同一评论跨了 train/eval"
            f"（常见于按三元组随机切分）。请按 review_id 分组切分。"
        )

    bad = [x for x in eval_ids if not x.isdigit()]
    if bad:
        raise SystemExit(f"[ERROR] 验证集 review_id 含非数字值: {bad[:5]}")
    holdout_ids = sorted({int(x) for x in eval_ids})

    print(f"留出校验: train/eval 文本零重叠（{len(train_texts)} / {len(eval_texts)}），"
          f"review_id 零重叠（{len(train_ids)} / {len(holdout_ids)}）")
    return train_rows, eval_rows, holdout_ids


def _ids_sha256(ids):
    import hashlib
    return hashlib.sha256(json.dumps(sorted(ids)).encode("utf-8")).hexdigest()


def _rel_to_root(path):
    """元数据里的路径优先存成相对模块根的形式。

    否则 task_meta.json 会写进本机绝对路径（C:\\Users\\xxx\\...），
    既泄露个人目录，又让别人重跑一次就产生整份 diff。
    """
    if not path:
        return ""
    root = os.path.normpath(os.path.join(HERE, ".."))
    p = os.path.normpath(os.path.abspath(path))
    try:
        rel = os.path.relpath(p, root)
    except ValueError:      # 跨盘符无法求相对路径
        return p
    return p if rel.startswith("..") else rel.replace(os.sep, "/")


def publish_model(out_dir, save_fn, meta):
    """先把模型与配套元数据全部写到临时目录，成功后再整体替换 out_dir。

    这样"权重"与"holdout_ids.json / task_meta.json"永远同批发布：
    中途失败只会留下一个临时目录，已有模型保持原状。
    """
    import shutil
    import time

    parent = os.path.dirname(os.path.abspath(out_dir)) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{os.path.basename(out_dir)}.tmp-{os.getpid()}-{int(time.time())}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    backup = None
    try:
        save_fn(tmp)
        with open(os.path.join(tmp, "task_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        if os.path.exists(out_dir):
            backup = f"{out_dir}.old-{int(time.time())}"
            os.replace(out_dir, backup)
        # Windows 上目录仍被其他进程占用时这里会抛异常：旧模型此时已经被改名，
        # 必须回滚回去，否则"保存失败"会变成"模型目录凭空消失"。
        os.replace(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if backup and os.path.exists(backup) and not os.path.exists(out_dir):
            try:
                os.replace(backup, out_dir)
                print(f"[警告] 发布失败，已回滚到原模型目录: {out_dir}")
            except OSError as e:
                print(f"[严重] 发布失败且回滚失败，旧模型仍保留在: {backup}（{e}）")
        raise
    if backup:
        shutil.rmtree(backup, ignore_errors=True)
    return out_dir


def main():
    args = parse_args()
    random.seed(args.seed)
    if not args.out:
        args.out = os.path.join(HERE, "..", "data",
                                "rbt3_model_aspect" if args.task == "aspect" else "rbt3_model_item")

    # ===== 第 1 步：读数据 + 完成全部校验 =====
    # 必须在训练、尤其是落盘保存之前做完：旧实现把重叠校验放在 save_pretrained 之后，
    # 校验失败退出时新权重已经覆盖旧模型，而 holdout_ids.json 仍是上一轮的 ID，
    # 于是"训练时说不行、评估时却拿旧留出集当模型没见过的数据"。
    train_rows, eval_rows, holdout_ids = load_and_validate(args)
    print(f"任务: {args.task} | 训练样本: {len(train_rows)}, 验证样本: {len(eval_rows)}")

    try:
        import numpy as np
        import torch
        from torch.utils.data import Dataset, DataLoader
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except ImportError as e:
        print("缺少依赖，请先运行（在 nlp-topic-sentiment 目录下）：")
        print("  pip install torch --index-url https://download.pytorch.org/whl/cpu")
        print("  pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements-train.txt")
        raise SystemExit(f"ImportError: {e}")

    np.random.seed(args.seed)
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
            _rid, text, key, label = self.data[i]
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

    # ===== 第 3 步：训练与评估都通过后，才把权重和配套留出 ID 一起发布 =====
    meta = {
        "task": args.task,
        # --model 是本地目录时才转相对路径；HF 仓库 id（hfl/rbt3）原样保留
        "model": _rel_to_root(args.model) if os.path.exists(args.model) else args.model,
        "max_len": args.max_len,
        "train_rows": len(train_rows), "eval_rows": len(eval_rows),
        "train_reviews": len({r[0] for r in train_rows if r[0]}),
        "holdout_reviews": len(holdout_ids),
        # 留出 ID 的指纹写进 task_meta，评估时可校验"这组 ID 是否与当前权重同批发布"
        "holdout_sha256": _ids_sha256(holdout_ids) if holdout_ids else "",
        "train_file": _rel_to_root(args.train),
        "eval_file": _rel_to_root(args.eval),
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    def _save_to(d):
        model.save_pretrained(d)
        tokenizer.save_pretrained(d)
        if holdout_ids:
            with open(os.path.join(d, "holdout_ids.json"), "w", encoding="utf-8") as f:
                json.dump(holdout_ids, f)

    publish_model(args.out, _save_to, meta)

    if holdout_ids:
        print(f"留出集 ID 已随模型同批发布: {len(holdout_ids)} 条（来源：本次 --eval 文件）")
        # 与金标留出 ID 对照，只提示不强制（银标/自定义训练时两者本就不同）
        ids_path = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "gold_eval_ids.json"))
        if os.path.exists(ids_path):
            with open(ids_path, encoding="utf-8") as f:
                gold_ids = set(json.load(f))
            if set(holdout_ids) != gold_ids:
                print(f"提示: 本次 eval 评论({len(holdout_ids)}条)与 gold_eval_ids.json"
                      f"({len(gold_ids)}条)不同，已按实际 eval 生成留出 ID（银标/自定义训练属正常情况）。")
    else:
        print("警告: 本次训练未产出 holdout_ids.json，严格留出集评估将拒绝在该模型上运行。")

    print(f"\n模型已保存: {args.out}")


if __name__ == "__main__":
    main()
