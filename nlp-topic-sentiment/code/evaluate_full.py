# -*- coding: utf-8 -*-
"""
在 240 条语义金标上做三方法对比：
  1) baseline_voted_up : 把整体情感赋给所有提及主题（朴素基线）
  2) rule_absm         : 规则方面级打标
  3) rbt3              : 金标微调后的 RBT3（aspect 层）
输出方面级 micro/macro P/R/F1，可直接填进效果对比方案.md。
"""
from __future__ import annotations
import os, csv, json, sys, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rule_labeler import label_review

ANN = os.path.normpath(os.path.join(HERE, "..", "data", "annotation"))
TASKS = os.path.join(ANN, "annotation_tasks.csv")


def load_gold():
    """从标注集 gold_aspects_json 读取主题层金标（与训练数据同一来源）。

    merge_gold.py 合并进来的新增标注会同时进入训练与评估，避免两者口径不一致。
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


def to_set(aspects):
    return {(a["aspect"], a["sentiment"]) for a in aspects if a.get("sentiment") != "neutral"}


def prf(preds, golds):
    tp = fp = fn = 0
    for p, g in zip(preds, golds):
        tp += len(p & g); fp += len(p - g); fn += len(g - p)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def macro_prf(preds, golds):
    """按 aspect 分组算 F1 再平均（macro，避免大主题主导）。"""
    from collections import defaultdict
    per = defaultdict(lambda: [0, 0, 0])  # aspect -> [tp, fp, fn]
    for p, g in zip(preds, golds):
        pa = {a for a, s in p}; ga = {a for a, s in g}
        for a in pa & ga:
            per[a][0] += int((a, "positive") in p and (a, "positive") in g) or \
                         int((a, "negative") in p and (a, "negative") in g)
        # 更精确：按 (aspect,sentiment) 三元组归类
    # 重新按 (aspect,sentiment) 累加
    per2 = defaultdict(lambda: [0, 0, 0])
    for p, g in zip(preds, golds):
        for t in p - g: per2[t[0]][1] += 1
        for t in g - p: per2[t[0]][2] += 1
        for t in p & g: per2[t[0]][0] += 1
    f1s = []
    for a, (tp, fp, fn) in per2.items():
        pr = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        f = 2 * pr * rc / (pr + rc) if pr + rc else 0
        f1s.append(f)
    return sum(f1s) / len(f1s) if f1s else 0.0


def load_data():
    gold_all = load_gold()
    data = []
    with open(TASKS, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = int(r["review_id"])
            if rid not in gold_all:
                continue
            data.append({"id": rid, "text": r["review_text"],
                         "voted_up": r.get("voted_up", ""),
                         "gold": set(gold_all[rid])})
    return data


def _sha256_ids(ids):
    import hashlib
    return hashlib.sha256(json.dumps(sorted(ids)).encode("utf-8")).hexdigest()


def _load_meta(model_dir):
    """读取模型元数据，返回 (meta, 问题描述)。问题描述为空串表示指纹可用。"""
    p = os.path.join(model_dir, "task_meta.json")
    if not os.path.exists(p):
        return None, "模型目录缺少 task_meta.json"
    try:
        with open(p, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        return None, f"task_meta.json 解析失败（{type(e).__name__}）"
    if not isinstance(meta, dict):
        return None, "task_meta.json 不是 JSON 对象"
    want = str(meta.get("holdout_sha256") or "").strip()
    if not want:
        return meta, "task_meta.json 缺少可用的 holdout_sha256"
    if len(want) != 64 or any(c not in "0123456789abcdef" for c in want.lower()):
        return meta, "task_meta.json 的 holdout_sha256 格式非法"
    return meta, ""


def resolve_holdout_ids(args):
    """确定严格评估用的留出集 ID，并校验它确实属于当前模型。

    返回 (ids_path, eval_ids, verified, reason)：
      verified=True  → 留出集指纹与模型记录一致，可作严格口径
      verified=False → 只能作参考评估（调用方必须把结论改成"参考"）

    与旧实现的关键差别：
      - 指纹校验**必须**有：task_meta.json 缺失 / 内容为 {} / 缺 holdout_sha256 /
        值为空串，都视为无法验证，默认直接中止。旧实现只在"文件存在且字段非空"时
        才比较，四种异常状态全都静默放行，训练评论会被当成留出集。
      - 校验对象是**实际选中的 ID 集合**（用该集合现算 sha256 与模型记录比对），
        而不是"文件路径是否等于模型目录那一份"。这样 --eval-ids 指向别处、
        或把过期文件复制到其他路径，同样会被识破。
      - 确实需要未验证的 ID 时，必须显式 --allow-unverified-holdout（或沿用
        --allow-fallback-ids），此时输出明确标为参考评估。
    """
    model_dir = args.rbt3_model if os.path.isdir(args.rbt3_model) else \
        os.path.dirname(os.path.abspath(args.rbt3_model))
    model_ids = os.path.join(model_dir, "holdout_ids.json")
    fallback = os.path.join(ANN, "gold_eval_ids.json")

    if args.eval_ids:
        ids_path, src = args.eval_ids, "--eval-ids 显式指定"
    elif os.path.exists(model_ids):
        ids_path, src = model_ids, "模型目录 holdout_ids.json"
    elif args.allow_fallback_ids and os.path.exists(fallback):
        ids_path, src = fallback, "gold_eval_ids.json（--allow-fallback-ids 回退）"
        print("[警告] 模型目录没有 holdout_ids.json，已按 --allow-fallback-ids 回退到 "
              "gold_eval_ids.json；这组评论是否参与过该模型训练无法证明。")
    else:
        raise SystemExit(
            f"[ERROR] 模型目录缺少 holdout_ids.json：{model_dir}\n"
            f"        无法证明留出集是该模型未见过的评论，严格评估已中止。\n"
            f"        处理方式：用新版 train_rbt3_v2.py 重训（留出 ID 与权重同批写出），\n"
            f"        或用 --eval-ids 显式指定，或加 --allow-fallback-ids 接受旧口径。")

    if not os.path.exists(ids_path):
        raise SystemExit(f"[ERROR] 留出集 ID 文件不存在：{ids_path}")
    try:
        with open(ids_path, encoding="utf-8") as f:
            eval_ids = {int(x) for x in json.load(f)}
    except Exception as e:
        raise SystemExit(f"[ERROR] 留出集 ID 文件无法解析（{type(e).__name__}: {e}）：{ids_path}")
    if not eval_ids:
        raise SystemExit(f"[ERROR] 留出集为空：{ids_path}；0 条评论的评估没有意义，"
                         f"--eval-ids 指向空列表时不会再退出码 0 静默通过。")

    meta, meta_err = _load_meta(model_dir)
    want = str((meta or {}).get("holdout_sha256") or "").strip()
    got = _sha256_ids(eval_ids)
    verified = bool(want) and got == want
    reason = ""
    if not verified:
        reason = meta_err if not want else (
            f"留出集 ID 指纹与模型不符（本次选中 {len(eval_ids)} 条，指纹 {got[:16]}… ≠ "
            f"模型记录 {want[:16]}…）")

    if not verified:
        allow = args.allow_unverified_holdout or args.allow_fallback_ids
        head = (f"[警告] 无法验证这组留出集是否与当前模型配套：{reason}\n"
                f"        留出集来源: {src}（{len(eval_ids)} 条）\n"
                f"        模型目录: {model_dir}")
        if not allow:
            raise SystemExit(
                f"[ERROR] 严格留出集评估已中止。\n"
                f"        {reason}\n"
                f"        说明：task_meta.json 里的 holdout_sha256 是训练时与权重同批写出的，\n"
                f"        缺失或为空就无法证明留出集配套（旧实现会静默放行，把训练见过的评论\n"
                f"        当成模型没见过）。\n"
                f"        处理方式：① 用新版 train_rbt3_v2.py 重训；② 若只是想要参考数字，\n"
                f"        加 --allow-unverified-holdout，输出会明确标为「参考评估」。")
        flag = "--allow-unverified-holdout" if args.allow_unverified_holdout else "--allow-fallback-ids"
        print(head)
        print(f"        已按 {flag} 继续，但本次只能作为参考评估，")
        print("        不能声称这些评论是模型未见过的。")
    return ids_path, eval_ids, verified, reason


def main():
    ap = argparse.ArgumentParser()
    default_model = os.path.normpath(os.path.join(HERE, "..", "data", "rbt3_model_aspect"))
    ap.add_argument("--rbt3", action="store_true", help="是否评估 RBT3 模型")
    ap.add_argument("--rbt3-model", default="",
                    help=f"RBT3 模型目录（默认 {default_model}，仅用于留出集指纹校验）；"
                         f"显式指定时自动启用 RBT3 评估")
    ap.add_argument("--heldout", action="store_true",
                    help="严格口径：只在模型训练时未见过的 eval 评论上评估三个方法")
    ap.add_argument("--eval-ids", default="",
                    help="留出集 ID JSON；默认读取模型目录的 holdout_ids.json")
    ap.add_argument("--allow-fallback-ids", action="store_true",
                    help="模型目录没有 holdout_ids.json 时，允许回退到标注集 gold_eval_ids.json（不保证与模型对应，输出标为参考评估）")
    ap.add_argument("--allow-unverified-holdout", action="store_true",
                    help="留出集无法通过模型指纹校验时仍继续评估；输出会明确标为「参考评估」")
    ap.add_argument("--allow-partial-holdout", action="store_true",
                    help="留出集 ID 有部分在金标里找不到时仍继续（默认在严格模式下报错）")
    args = ap.parse_args()

    # 显式给 --rbt3-model <目录> 就意味着"要评估这个模型"，与 --rbt3 等价。
    # 旧实现只认 --rbt3：两者一起用没问题，但只给模型目录时会静默跳过第三方法，
    # 而结论文案仍按"三方法"输出——干净 clone（无权重）下实测过这个假结论。
    if args.rbt3_model:
        args.rbt3 = True
    else:
        args.rbt3_model = default_model

    data = load_data()
    verified = False
    if args.heldout:
        ids_path, eval_ids, verified, reason = resolve_holdout_ids(args)
        data = [d for d in data if d["id"] in eval_ids]
        matched = {d["id"] for d in data}
        missing = eval_ids - matched
        if not data:
            raise SystemExit(f"[ERROR] 留出集 ID（{len(eval_ids)} 个）与语义金标样本没有交集，无法评估。")
        if missing:
            print(f"[警告] 留出集有 {len(missing)}/{len(eval_ids)} 条 ID 在标注集中找不到对应金标："
                  f"{sorted(missing)[:5]}{' …' if len(missing) > 5 else ''}")
            if verified and not args.allow_partial_holdout:
                raise SystemExit(
                    f"[ERROR] 严格口径要求留出集 ID 全部能匹配金标，实际缺失 {len(missing)} 条。\n"
                    f"        常见原因：留出集来自另一份标注数据，或标注集被裁剪过，\n"
                    f"        照此评估会悄悄少评一部分评论。确认可接受时加 --allow-partial-holdout。")
        print(f"留出集匹配金标: {len(matched)}/{len(eval_ids)} 条")
        if verified:
            print(f"严格留出集评估：模型未见过的 {len(data)} 条评论（各方法同口径）")
        else:
            print(f"参考评估：{len(data)} 条评论（留出集未通过模型指纹校验，"
                  f"不能声称模型未见过这些评论）")
            print(f"未通过原因: {reason}")
        print(f"留出集来源: {ids_path}\n")
    else:
        print(f"语义金标样本: {len(data)} 条\n")

    # ---- 1. baseline_voted_up ----
    # 整体情感正/负 -> 赋给该评论提及的每个主题
    up_preds, up_golds = [], []
    for d in data:
        overall = "positive" if str(d["voted_up"]).lower() in ("true", "1", "yes") else "negative"
        aspects = {a for a, s in d["gold"]}
        up_preds.append({(a, overall) for a in aspects})
        up_golds.append(d["gold"])
    p, r, f1 = prf(up_preds, up_golds)
    mf1 = macro_prf(up_preds, up_golds)
    print("=== ① baseline_voted_up（整体情感当主题情感）===")
    print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
    print(f"  macro-F1:     {mf1:.4f}\n")

    # ---- 2. rule_absm ----
    rule_preds, rule_golds = [], []
    for d in data:
        res = label_review(d["text"])
        rule_preds.append(to_set(res["aspects"]))
        rule_golds.append(d["gold"])
    p, r, f1 = prf(rule_preds, rule_golds)
    mf1 = macro_prf(rule_preds, rule_golds)
    print("=== ② rule_absm（规则方面级打标）===")
    print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
    print(f"  macro-F1:     {mf1:.4f}\n")

    # ---- 3. rbt3 ----
    rbt3_failed = False
    rbt3_done = False        # 真的跑出 ③ 的结果才置 True；"没要求跑"不能当成跑成功
    if args.rbt3:
        try:
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch
            from lexicon import ASPECTS
            tok = AutoTokenizer.from_pretrained(args.rbt3_model)
            model = AutoModelForSequenceClassification.from_pretrained(args.rbt3_model)
            model.eval()
            id2sent = {0: "negative", 1: "neutral", 2: "positive"}
            asp_ids = list(ASPECTS.keys())
            prompts = [f"评价方面：{ASPECTS[a]['name_zh']}" for a in asp_ids]

            rbt3_preds, rbt3_golds = [], []
            with torch.no_grad():
                for d in data:
                    pred = set()
                    # 模型对全部 11 主题三分类，不依赖规则门控
                    for i, a in enumerate(asp_ids):
                        enc = tok(prompts[i], d["text"], truncation=True, max_length=160, return_tensors="pt")
                        out = model(**enc)
                        sid = int(torch.argmax(out.logits, dim=-1))
                        sent = id2sent.get(sid, "neutral")
                        if sent != "neutral":
                            pred.add((a, sent))
                    rbt3_preds.append(pred)
                    rbt3_golds.append(d["gold"])
            p, r, f1 = prf(rbt3_preds, rbt3_golds)
            mf1 = macro_prf(rbt3_preds, rbt3_golds)
            print("=== ③ rbt3（金标微调，全主题三分类，无规则门控）===")
            print(f"  micro-P/R/F1: {p:.4f} / {r:.4f} / {f1:.4f}")
            print(f"  macro-F1:     {mf1:.4f}\n")
            rbt3_done = True
        except Exception as e:
            rbt3_failed = True
            print(f"RBT3 评估失败: {e}\n", file=sys.stderr)

    if args.heldout:
        if rbt3_failed:
            print("说明：本次仅完成 ① voted_up 与 ② rule_absm 两个基线；RBT3 评估失败，")
            print("      三项对比尚未完成，不能据此写'三方法均已评估'的结论。")
        elif not rbt3_done:
            # 没要求跑 RBT3：此时只剩两个基线，不能说"三方法都在留出集上评估"
            print(f"说明：严格留出集口径，但本次只评估了 ① 与 ② 两个基线"
                  f"（加 --rbt3 或显式 --rbt3-model <权重目录> 才会跑 ③）。")
            print("      留出集指纹与模型 task_meta.json 一致，但三项对比尚未完成，"
                  "不能写成三方法均已评估。")
        elif verified:
            print(f"说明：严格留出集口径。三方法都在 RBT3 未见过的 {len(data)} 条 eval 评论上评估，")
            print("      留出集指纹与模型 task_meta.json 一致，可直接写进报告。")
        else:
            print("说明：参考评估。留出集未能与模型指纹对上（见上方警告），")
            print("      这些评论是否参与过该模型训练无法证明，不可作为严格留出集结论写入报告。")
    else:
        print(f"说明：在全体 {len(data)} 条语义金标上评估（含 RBT3 训练用过的评论，数字偏乐观）。")
        print("      写报告请用 --heldout 的严格留出集数字。")

    # 显式要求 --rbt3 却没跑成：返回非零退出码，避免自动验收把失败当成成功
    if args.rbt3 and rbt3_failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
