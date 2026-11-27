# -*- coding: utf-8 -*-
"""
RBT3 全量回灌：对全量中文评论跑主题层模型推理，输出模型标签结果表。

与规则表（topic_sentiment_detail.csv）平行的模型口径表：
  topic_sentiment_detail_rbt3.csv      主题情感明细（review_id × aspect）
  topic_sentiment_review_summary_rbt3.csv  评论级汇总

设计：
  - 不做规则门控（no gate）：模型独立对 11 个 aspect 判极性，
    这是"从词典升级到模型"的意义所在（模型能发现词典漏掉的主题）。
  - 批量前向：prompt+text 拼对后整批编码，GPU 上 batch 推理。
  - 只保留非 neutral 的 (评论, aspect) 对，输出量可控。
  - 分 row group 流式处理 + 分批落盘，内存占用恒定。

用法：
  python infer_full_rbt3.py --limit 2000            # 试跑测速
  python infer_full_rbt3.py                          # 全量中文
  python infer_full_rbt3.py --batch 256              # 调批量
  python infer_full_rbt3.py --resume                 # 中断后续跑（row group 级断点）

断点机制：每处理完一个 row group 就落盘明细/汇总并写进度文件
  infer_rbt3_progress.json（记录 next_row_group）；--resume 从断点继续，
  已完成的 row group 不重算，输出不会重复。
"""
from __future__ import annotations
import os
import sys
import time
import json
import argparse

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "data", "result"))
DEFAULT_PARQUET = r"D:\QQFiles\team_package_v1\team_package_v1\游戏评论交付\releases\v1\data\cleaned_reviews.parquet"

sys.path.insert(0, HERE)
from lexicon import ASPECTS, ASPECT_IDS

ASPECT_PROMPT = {
    "content": "内容玩法", "level_design": "地图关卡设计", "multiplayer": "联机服务器",
    "graphics": "画质美术", "performance": "性能优化", "story": "剧情叙事",
    "controls": "操作手感", "monetization": "付费氪金", "cheating": "外挂反作弊",
    "audio": "音效音乐", "community": "运营社区",
}
ID2SENT = {0: "negative", 1: "neutral", 2: "positive"}

COLS = ["appid", "game_name", "review_id", "language_std", "review_text",
        "created_date", "game_version", "voted_up"]
MODEL_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "rbt3_model_aspect"))
MAX_LEN = 160          # 与训练/评估一致
THRESHOLD = 0.5


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=DEFAULT_PARQUET)
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条中文评论，0=全部")
    ap.add_argument("--batch", type=int, default=384)
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--overwrite", action="store_true",
                    help="输出文件已存在时删除重写；不加此参数则拒绝运行，避免新旧结果混写")
    ap.add_argument("--resume", action="store_true",
                    help="从进度文件断点续跑（与 --overwrite 互斥）")
    return ap.parse_args()


def main():
    import torch
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.csv as pacsv
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    args = parse_args()
    os.makedirs(OUT, exist_ok=True)

    detail_path = os.path.join(OUT, "topic_sentiment_detail_rbt3.csv")
    review_path = os.path.join(OUT, "topic_sentiment_review_summary_rbt3.csv")
    progress_path = os.path.join(OUT, "infer_rbt3_progress.json")
    start_rg = 0
    if args.resume and args.overwrite:
        print("错误：--resume 与 --overwrite 不能同时用")
        sys.exit(1)
    if args.overwrite:
        for p in (detail_path, review_path, progress_path):
            if os.path.exists(p):
                os.remove(p)
        print("已删除旧输出与进度文件，将从头重跑")
    elif args.resume:
        if os.path.exists(progress_path):
            with open(progress_path, encoding="utf-8") as f:
                prog = json.load(f)
            start_rg = int(prog.get("next_row_group", 0))
            # 恢复累计计数器，否则续跑后进度文件/ETA 会从零重算
            resume_n_zh = int(prog.get("n_zh", 0))
            resume_n_tagged = int(prog.get("n_tagged", 0))
            resume_n_pairs = int(prog.get("n_pairs", 0))
            resume_elapsed = float(prog.get("elapsed_s", 0))
            print(f"断点续跑：从 row group {start_rg} 继续"
                  f"（已完成 {resume_n_zh} 条中文 / {resume_n_pairs} 对前向）")
        else:
            print("未找到进度文件，从头开始")
    elif os.path.exists(detail_path) or os.path.exists(review_path):
        print("错误：输出文件已存在（可能是上次试跑/中断的残留）。")
        print("续跑用：python infer_full_rbt3.py --resume")
        print("重跑用：python infer_full_rbt3.py --overwrite")
        sys.exit(1)

    if not torch.cuda.is_available():
        print("警告：CUDA 不可用，将用 CPU 推理（全量预计 40+ 小时，建议先装 CUDA 版 torch）")
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {dev} | " + (torch.cuda.get_device_name(0) if dev.type == "cuda" else ""))

    tok = AutoTokenizer.from_pretrained(args.model)
    mdl = AutoModelForSequenceClassification.from_pretrained(args.model).eval().to(dev)
    prompts = {a: f"评价方面：{ASPECT_PROMPT[a]}" for a in ASPECT_IDS}

    pf = pq.ParquetFile(args.parquet)
    detail_rows: list[dict] = []
    review_rows: list[dict] = []
    # 续跑时输出文件已存在则追加（不写表头）
    first_detail = not (args.resume and os.path.exists(detail_path) and start_rg > 0)
    first_review = not (args.resume and os.path.exists(review_path) and start_rg > 0)
    n_zh = n_tagged = n_pairs = 0
    if args.resume and start_rg > 0:
        n_zh, n_tagged, n_pairs = resume_n_zh, resume_n_tagged, resume_n_pairs
        t0 = time.time() - resume_elapsed
    else:
        t0 = time.time()

    def flush(rows, path, first):
        if not rows:
            return
        tbl = pa.Table.from_pylist(rows)
        opts = pacsv.WriteOptions(include_header=first)
        sink = pa.OSFile(path, "ab" if not first else "wb")
        try:
            pacsv.write_csv(tbl, sink, write_options=opts)
        finally:
            sink.close()

    @torch.no_grad()
    def classify_batch(pairs_texts):
        """pairs_texts: [(prompt, text), ...] -> [(sent, conf), ...]"""
        use_amp = dev.type == "cuda"
        out = []
        for i in range(0, len(pairs_texts), args.batch):
            chunk = pairs_texts[i:i + args.batch]
            enc = tok([p for p, _ in chunk], [t for _, t in chunk],
                      truncation=True, max_length=MAX_LEN, padding=True,
                      return_tensors="pt").to(dev)
            with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
                logits = mdl(**enc).logits
            probs = torch.softmax(logits.float(), -1).cpu().tolist()
            for p_neg, p_neu, p_pos in probs:
                if p_pos >= args.threshold and p_pos >= p_neg:
                    out.append(("positive", round(p_pos, 4)))
                elif p_neg >= args.threshold and p_neg >= p_pos:
                    out.append(("negative", round(p_neg, 4)))
                else:
                    out.append(("neutral", round(max(p_neg, p_neu, p_pos), 4)))
        return out

    n_total_groups = pf.metadata.num_row_groups
    for rg in range(start_rg, n_total_groups):
        t = pf.read_row_group(rg, columns=COLS).to_pydict()
        m = len(t["appid"])
        # 收集本 row group 的中文评论
        reviews = []  # (j, text)
        for j in range(m):
            if args.limit and n_zh >= args.limit:
                break
            if t["language_std"][j] not in ("zh-Hans", "zh-Hant"):
                continue
            n_zh += 1
            text = (t["review_text"][j] or "").strip()
            if not text:
                continue
            reviews.append((j, text))

        if not reviews:
            if args.limit and n_zh >= args.limit:
                break
            continue

        # 全部 (评论 × 11 aspect) 对
        pairs = []
        pair_owner = []  # 与 pairs 对齐 -> reviews 下标
        for ri, (_j, text) in enumerate(reviews):
            for aid in ASPECT_IDS:
                pairs.append((prompts[aid], text))
                pair_owner.append(ri)
        n_pairs += len(pairs)
        results = classify_batch(pairs)

        # 聚合回评论级
        per_review: list[dict[str, tuple[str, float]]] = [dict() for _ in reviews]
        for k, (sent, conf) in enumerate(results):
            aid = ASPECT_IDS[k % len(ASPECT_IDS)]
            per_review[pair_owner[k]][aid] = (sent, conf)

        for ri, (j, _text) in enumerate(reviews):
            aspect_map = per_review[ri]
            pos = sum(1 for s, _ in aspect_map.values() if s == "positive")
            neg = sum(1 for s, _ in aspect_map.values() if s == "negative")
            primary = "negative" if neg > pos else ("positive" if pos > 0
                                                    else ("neutral" if aspect_map else "none"))
            review_rows.append({
                "review_id": t["review_id"][j],
                "appid": t["appid"][j],
                "game_name": t["game_name"][j],
                "created_date": t["created_date"][j],
                "game_version": t["game_version"][j] or "",
                "voted_up": t["voted_up"][j],
                "n_aspects": sum(1 for s, _ in aspect_map.values() if s != "neutral"),
                "pos_aspects": pos,
                "neg_aspects": neg,
                "primary_sentiment": primary,
                "aspects_json": json.dumps(
                    [{"aspect": a, "name_zh": ASPECTS[a]["name_zh"],
                      "sentiment": s, "confidence": c}
                     for a, (s, c) in aspect_map.items() if s != "neutral"],
                    ensure_ascii=False),
            })
            for aid, (sent, conf) in aspect_map.items():
                if sent == "neutral":
                    continue
                detail_rows.append({
                    "review_id": t["review_id"][j],
                    "appid": t["appid"][j],
                    "game_name": t["game_name"][j],
                    "created_date": t["created_date"][j],
                    "game_version": t["game_version"][j] or "",
                    "aspect": aid,
                    "name_zh": ASPECTS[aid]["name_zh"],
                    "sentiment": sent,
                    "confidence": conf,
                    "sentiment_word": "",
                    "evidence": "",
                })
            n_tagged += 1

        # ---- 每个row group处理完即落盘 + 写进度（断点续跑单位 = row group）----
        flush(detail_rows, detail_path, first_detail)
        if detail_rows:
            first_detail = False
        flush(review_rows, review_path, first_review)
        if review_rows:
            first_review = False
        detail_rows = []
        review_rows = []
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump({"next_row_group": rg + 1, "n_zh": n_zh,
                       "n_pairs": n_pairs, "total_groups": n_total_groups,
                       "elapsed_s": round(time.time() - t0, 1)}, f, ensure_ascii=False)
        el = time.time() - t0
        if (rg + 1) % 5 == 0 or rg + 1 == n_total_groups or (args.limit and n_zh >= args.limit):
            print(f"  ...row group {rg+1}/{n_total_groups} | 中文 {n_zh} | "
                  f"前向 {n_pairs} | {el:.0f}s | {n_pairs/max(el,1e-9):.0f} 对/s", flush=True)
        if dev.type == "cuda" and (rg + 1) % 10 == 0:
            torch.cuda.empty_cache()

        if args.limit and n_zh >= args.limit:
            break

    # 收尾：残余缓冲（正常情况下每个 rg 已即时落盘，这里兜底空缓冲即可）
    flush(detail_rows, detail_path, first_detail)
    flush(review_rows, review_path, first_review)

    el = time.time() - t0
    print("\n=== 完成 ===")
    print(f"中文评论扫描: {n_zh}, 实际推理: {n_tagged}")
    print(f"前向对数: {n_pairs} | 耗时: {el:.0f}s | 速率: {n_pairs/max(el,1e-9):.0f} 对/s")
    print(f"输出: {detail_path}")
    print(f"      {review_path}")


if __name__ == "__main__":
    main()
