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


def _check_ids(rows, side, strict=True):
    """校验一侧数据的 review_id 完整合法，并统一转成 int 集合返回。

    只要求验证集完整是不够的：训练集里缺 ID 的行会被跳过，两边 ID 也就比不出交集，
    同一评论照样能跨集合。所以两侧都要查，且必须转成 int 再比（"0161798229" 与
    "161798229" 是同一评论，字符串比较会漏掉）。

    strict=True  ：缺 ID / 非数字一律报错（提供 --eval 时的两侧都走这条）
    strict=False ：仅无 --eval 的纯训练场景，缺 ID 降为警告，非数字仍报错
    """
    raw = [r[0] for r in rows]
    bad = sorted({x for x in raw if x and not x.isdigit()})
    if bad:
        raise SystemExit(
            f"[ERROR] {side} review_id 含非数字值: {bad[:5]}（共 {len(bad)} 种）。"
            f" review_id 必须是原始评论的整数 ID。")
    missing = sum(1 for x in raw if not x)
    if missing:
        msg = (f"{side}有 {missing}/{len(raw)} 行缺少 review_id。"
               f"留出集必须能按原始评论 ID 核对，缺 ID 会让重叠校验失效。")
        if strict:
            raise SystemExit(
                f"[ERROR] {msg} 请用当前版本的 prepare_gold_3way.py / prepare_rbt3_data_v2.py"
                f" 重新生成数据（两者都会写出 review_id 列）。")
        print(f"[警告] {msg}")
    return {int(x) for x in raw if x}


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
        train_ids = _check_ids(train_rows, "训练集", strict=False)
        return train_rows, eval_rows, holdout_ids, train_ids

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

    # 两侧的 review_id 都必须完整、合法，并统一转成 int 再比较交集。
    # 旧实现只查验证集、且用字符串比较：训练集里缺 ID 会被 {r[0] for r in rows if r[0]} 直接跳过，
    # "0161798229" 与 "161798229" 也被当成不同评论，于是同一评论能同时出现在两边。
    train_ids = _check_ids(train_rows, "训练集", strict=True)
    eval_ids = _check_ids(eval_rows, "验证集", strict=True)

    overlap_id = train_ids & eval_ids
    if overlap_id:
        raise SystemExit(
            f"[ERROR] 训练集与验证集有 {len(overlap_id)} 条相同 review_id，同一评论跨了 train/eval"
            f"（常见于按三元组随机切分，或 ID 前导零/格式不一致）。示例: {sorted(overlap_id)[:5]}"
        )

    holdout_ids = sorted(eval_ids)

    print(f"留出校验: train/eval 文本零重叠（{len(train_texts)} / {len(eval_texts)}），"
          f"review_id 零重叠（{len(train_ids)} / {len(holdout_ids)}）")
    return train_rows, eval_rows, holdout_ids, train_ids


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


# 发布时允许被覆盖/清理的"模型产物"白名单。
# 不在名单内的文件（用户自己放的 train.csv、notes.txt 等）一律保留。
_MODEL_ARTIFACTS = {
    "config.json", "model.safetensors", "pytorch_model.bin",
    "task_meta.json", "holdout_ids.json",
    "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "vocab.txt", "added_tokens.json", "spiece.model", "sentencepiece.bpe.model",
    "generation_config.json", "training_args.bin", "config_sentencepiece.model",
}


def _is_model_artifact(name):
    if name in _MODEL_ARTIFACTS:
        return True
    # 分片权重：model-00001-of-00002.safetensors / pytorch_model-00001-of-00002.bin
    if name.endswith((".safetensors", ".bin")) and \
            (name.startswith("model-") or name.startswith("pytorch_model-")):
        return True
    if name.endswith((".safetensors.index.json", ".bin.index.json")):
        return True
    return False


def publish_model(out_dir, save_fn, meta):
    """发布模型产物到 out_dir，保留目录内用户自己的文件。

    与旧实现的区别（旧实现是整目录改名→替换→删备份）：
      - 只替换「模型产物」白名单内的文件；train.csv / notes.txt 等不会被删。
      - 先把新产物完整写到同级临时目录，再逐个移入，失败时删除新文件并把备份移回。
      - 输出路径先规范化：`...\\rbt3_model_aspect\\` 这类尾部带分隔符的写法，
        旧实现会拼出 `...\\rbt3_model_aspect\\.rbt3_model_aspect.old-*`（落在原目录内部），
        改名必然失败（Windows 报 WinError 87）。
    """
    import shutil
    import time

    out_dir = os.path.normpath(os.path.abspath(out_dir))
    if os.path.exists(out_dir) and not os.path.isdir(out_dir):
        raise SystemExit(f"[ERROR] 输出路径已存在且不是目录: {out_dir}")

    parent = os.path.dirname(out_dir) or "."
    base = os.path.basename(out_dir)
    os.makedirs(parent, exist_ok=True)
    stamp = int(time.time())
    tmp = os.path.join(parent, f".{base}.tmp-{os.getpid()}-{stamp}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)

    try:
        save_fn(tmp)
        with open(os.path.join(tmp, "task_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    # 目标不存在：整体改名，最干净
    if not os.path.exists(out_dir):
        os.replace(tmp, out_dir)
        return out_dir

    # 目标已存在：只换模型产物
    entries = os.listdir(out_dir)
    stale = sorted(f for f in entries
                   if _is_model_artifact(f) and os.path.isfile(os.path.join(out_dir, f)))
    keep = sorted(f for f in entries if f not in stale)

    backup = os.path.join(parent, f".{base}.old-{stamp}")
    shutil.rmtree(backup, ignore_errors=True)
    os.makedirs(backup, exist_ok=True)
    moved_out, placed = [], []
    try:
        for f in stale:
            shutil.move(os.path.join(out_dir, f), os.path.join(backup, f))
            moved_out.append(f)
        for f in sorted(os.listdir(tmp)):
            shutil.move(os.path.join(tmp, f), os.path.join(out_dir, f))
            placed.append(f)
    except BaseException:
        # 回滚：删掉刚放进去的新产物，把备份移回原位
        for f in placed:
            try:
                os.remove(os.path.join(out_dir, f))
            except OSError:
                pass
        for f in moved_out:
            try:
                shutil.move(os.path.join(backup, f), os.path.join(out_dir, f))
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    if keep:
        print(f"[提示] 输出目录保留了 {len(keep)} 个非模型文件: {keep[:6]}"
              f"{' …' if len(keep) > 6 else ''}")
    return out_dir


def _report_gold_compare(holdout_ids):
    """与 gold_eval_ids.json 做辅助对照，任何异常都只警告（该文件仅供人看）。"""
    ids_path = os.path.normpath(os.path.join(HERE, "..", "data", "annotation", "gold_eval_ids.json"))
    if not os.path.exists(ids_path):
        print("提示: 未找到 gold_eval_ids.json，跳过与金标留出集的对照。")
        return
    try:
        with open(ids_path, encoding="utf-8") as f:
            gold_ids = {int(x) for x in json.load(f)}
    except Exception as e:
        print(f"[警告] gold_eval_ids.json 读取失败（{type(e).__name__}: {e}），已跳过对照；"
              f"该文件只是辅助信息，不影响本次训练与发布。")
        return
    if set(holdout_ids) != gold_ids:
        print(f"提示: 本次 eval 评论({len(holdout_ids)}条)与 gold_eval_ids.json"
              f"({len(gold_ids)}条)不同，已按实际 eval 生成留出 ID（银标/自定义训练属正常情况）。")


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
    train_rows, eval_rows, holdout_ids, train_ids = load_and_validate(args)
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
    # 只固定 random / numpy 不够：dropout 与权重初始化走 torch 的 RNG，
    # 不设种子时同一份数据两次训练会得到不同模型（实测 macro-F1 可差 0.05 以上）。
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
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
        "train_reviews": len(train_ids),
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

    # 与金标留出 ID 的对照只是辅助检查，放在发布之前，且任何异常都降为警告：
    # 旧实现放在发布之后且没有 try/except，gold_eval_ids.json 一旦损坏，
    # 调用方会看到非零退出码，但模型其实已经被替换，很容易误判"训练失败"。
    if holdout_ids:
        _report_gold_compare(holdout_ids)
    else:
        print("警告: 本次训练未产出 holdout_ids.json，严格留出集评估将拒绝在该模型上运行。")

    args.out = publish_model(args.out, _save_to, meta)

    if holdout_ids:
        print(f"留出集 ID 已随模型同批发布: {len(holdout_ids)} 条（来源：本次 --eval 文件）")
    print(f"\n模型已保存: {args.out}")


if __name__ == "__main__":
    main()
