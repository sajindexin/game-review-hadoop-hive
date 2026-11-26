# -*- coding: utf-8 -*-
"""回归测试：切分、留出集守卫、模型发布（不需要 torch，可在干净 clone 直接跑）。

覆盖的必修项（按复审轮次）：
  1. 分组切分：同一 review_id 的多条目、不同 review_id 的相同文本，都不得跨 train/eval；
     金标与银标共用 split_utils，金标切分后必须自检零重叠。
  2. 严格评估必须能验证留出集与模型配套：task_meta 缺失 / {} / 缺 holdout_sha256 /
     空串 / 指纹不符，一律非零退出；显式放行时只能标为「参考评估」。
  3. 训练前置校验要求 train/eval 两侧 review_id 都完整合法，并按整数比较
     （"0161798229" 与 "161798229" 是同一评论）。
  4. 模型发布：保留输出目录中的非模型文件；路径尾部带分隔符可正常发布；
     发布中途失败必须回滚，旧模型与用户文件都不能丢。

用法：
  python code/verify_split_guard.py
退出码 0 表示全部通过（可直接接进 CI 或交付前自检）。
"""
from __future__ import annotations
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

OK = 0
FAIL = 0


def check(cond, msg):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK] {msg}")
    else:
        FAIL += 1
        print(f"  [FAIL] {msg}")
    return cond


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def run(args, cwd=ROOT):
    """跑子进程，返回 (returncode, stdout+stderr)。"""
    p = subprocess.run([sys.executable, *args], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_header(title):
    print(f"\n=== {title} ===")


def main():
    check_header("1. 分组切分：同一 review / 同一文本不可跨集合")
    from split_utils import group_by_review

    triples = [
        ("102625626", "姜岛探索很好玩", "S15_01", "positive"),
        ("102625626", "新增农场区域很大", "S14_02", "positive"),
        ("102625626", "鱼塘能养鱼了", "S15_04", "positive"),
        ("200001", "联机老是掉线", "S15_05", "negative"),
        ("200002", "画面变好了", "D01", "positive"),
    ]
    groups = group_by_review(triples)
    hit = [g for g in groups.values() if any(t[0] == "102625626" for t in g)]
    check(len(hit) == 1, f"同一 review 的 3 个条目落在同一分组（实际 {len(hit)} 个分组）")
    check(len(hit[0]) == 3, f"该分组包含全部 3 条三元组（实际 {len(hit[0])}）")

    dup = [
        ("300001", "一模一样的话", "content", "positive"),
        ("300002", "一模一样的话", "content", "negative"),
    ]
    g2 = group_by_review(dup)
    check(len(g2) == 1, f"相同文本的不同评论被并入同一分组（实际 {len(g2)} 个分组）")

    g3 = group_by_review([("", "只有文本", "content", "neutral")])
    check(len(g3) == 1, "缺 review_id 时退化按文本分组，不崩溃")

    check_header("2. 真实银标数据：train/eval 零重叠")
    st = os.path.join(ROOT, "data", "annotation", "rbt3_item_silver_train.csv")
    se = os.path.join(ROOT, "data", "annotation", "rbt3_item_silver_eval.csv")
    if os.path.exists(st) and os.path.exists(se):
        with open(st, encoding="utf-8-sig") as f:
            tr = list(csv.DictReader(f))
        with open(se, encoding="utf-8-sig") as f:
            ev = list(csv.DictReader(f))
        check("review_id" in (tr[0] if tr else {}), "银标训练集含 review_id 列")
        tid = {r["review_id"] for r in tr}
        eid = {r["review_id"] for r in ev}
        ttx = {r["review_text"] for r in tr}
        etx = {r["review_text"] for r in ev}
        check(not (tid & eid), f"评论 ID 零重叠（交集 {len(tid & eid)}）")
        check(not (ttx & etx), f"文本零重叠（交集 {len(ttx & etx)}）")
    else:
        print("  [skip] 银标文件不在仓库（.gitignore 已排除），跳过真实数据检查")

    tmp = tempfile.mkdtemp(prefix="splitguard_")
    hdr = ["review_id", "review_text", "aspect", "sentiment"]

    check_header("3. 金标切分：重复文本与真实标注数据")
    import prepare_gold_3way as g3mod
    ann_tmp = os.path.join(tmp, "gold_ann")
    os.makedirs(ann_tmp, exist_ok=True)
    real_tasks, real_ann = g3mod.TASKS, g3mod.ANN
    shutil.copy(real_tasks, os.path.join(ann_tmp, "annotation_tasks.csv"))
    g3mod.TASKS = os.path.join(ann_tmp, "annotation_tasks.csv")
    g3mod.ANN = ann_tmp
    try:
        gt_train, gt_eval = g3mod.build(seed=1)
    finally:
        g3mod.TASKS, g3mod.ANN = real_tasks, real_ann
    gt_ids = {rid for rid, _ in gt_train}
    ge_ids = {rid for rid, _ in gt_eval}
    gt_tx = {t for _r, rv in gt_train for t, _a, _s in rv}
    ge_tx = {t for _r, rv in gt_eval for t, _a, _s in rv}
    check(not (gt_ids & ge_ids), f"金标 review_id 零重叠（交集 {len(gt_ids & ge_ids)}）")
    check(not (gt_tx & ge_tx), f"金标文本零重叠（交集 {len(gt_tx & ge_tx)}）")
    # 复审实例：两条不同 review_id、文本完全相同
    dup_ids = (93940703, 33393799)     # build() 内部把 review_id 统一成 int
    same_side = (all(i in gt_ids for i in dup_ids) or all(i in ge_ids for i in dup_ids))
    check(same_side, "重复文本评论 93940703 / 33393799 落在同一侧")
    check(len(gt_train) + len(gt_eval) == 240,
          f"金标评论总数仍为 240（实际 {len(gt_train) + len(gt_eval)}）")

    check_header("4. 训练前置校验：失败即退出，且不创建模型目录")
    write_csv(os.path.join(tmp, "train.csv"), hdr, [
        ("1", "姜岛探索很好玩", "content", "positive"),
        ("2", "联机老是掉线", "multiplayer", "negative"),
        ("3", "画面不错", "graphics", "positive"),
    ])

    write_csv(os.path.join(tmp, "eval_textoverlap.csv"), hdr, [
        ("9", "姜岛探索很好玩", "content", "positive"),
    ])
    out_a = os.path.join(tmp, "model_a")
    rc, log = run(["code/train_rbt3_v2.py", "--task", "aspect",
                   "--train", os.path.join(tmp, "train.csv"),
                   "--eval", os.path.join(tmp, "eval_textoverlap.csv"),
                   "--out", out_a])
    check(rc != 0, f"文本重叠时退出码非零（实际 {rc}）")
    check("相同评论文本" in log, "报错信息指出评论文本重叠")
    check(not os.path.exists(out_a), "校验失败后没有创建模型目录")

    write_csv(os.path.join(tmp, "eval_idoverlap.csv"), hdr, [
        ("1", "姜岛探索太好玩了", "content", "positive"),
        ("8", "音乐很好听", "audio", "positive"),
    ])
    out_b = os.path.join(tmp, "model_b")
    rc, log = run(["code/train_rbt3_v2.py", "--task", "aspect",
                   "--train", os.path.join(tmp, "train.csv"),
                   "--eval", os.path.join(tmp, "eval_idoverlap.csv"),
                   "--out", out_b])
    check(rc != 0, f"review_id 重叠时退出码非零（实际 {rc}）")
    check("相同 review_id" in log, "报错信息指出 review_id 交叉")
    check(not os.path.exists(out_b), "校验失败后没有创建模型目录")

    write_csv(os.path.join(tmp, "eval_noid.csv"), ["review_text", "aspect", "sentiment"], [
        ("音乐很好听", "audio", "positive"),
    ])
    out_c = os.path.join(tmp, "model_c")
    rc, log = run(["code/train_rbt3_v2.py", "--task", "aspect",
                   "--train", os.path.join(tmp, "train.csv"),
                   "--eval", os.path.join(tmp, "eval_noid.csv"),
                   "--out", out_c])
    check(rc != 0, f"验证集缺 review_id 时退出码非零（实际 {rc}）")
    check("缺少 review_id" in log, "报错信息提示缺 review_id 列")
    check(not os.path.exists(out_c), "校验失败后没有创建模型目录")

    # 训练集缺 ID：旧实现会跳过这些行，导致两边比不出交集
    write_csv(os.path.join(tmp, "train_noid.csv"), hdr, [
        ("", "姜岛探索很好玩", "content", "positive"),
        ("2", "联机老是掉线", "multiplayer", "negative"),
    ])
    out_e = os.path.join(tmp, "model_e")
    rc, log = run(["code/train_rbt3_v2.py", "--task", "aspect",
                   "--train", os.path.join(tmp, "train_noid.csv"),
                   "--eval", os.path.join(tmp, "eval_idoverlap.csv"),
                   "--out", out_e])
    check(rc != 0, f"训练集缺 review_id 时退出码非零（实际 {rc}）")
    check("训练集有" in log and "缺少 review_id" in log, "报错信息指出训练集缺 ID")
    check(not os.path.exists(out_e), "校验失败后没有创建模型目录")

    # 前导零：字符串比较会漏，"0161798229" 与 "161798229" 是同一评论
    write_csv(os.path.join(tmp, "train_zeropad.csv"), hdr, [
        ("0161798229", "姜岛探索很好玩", "content", "positive"),
        ("2", "联机老是掉线", "multiplayer", "negative"),
    ])
    write_csv(os.path.join(tmp, "eval_plain.csv"), hdr, [
        ("161798229", "姜岛探索太好了一样好玩", "content", "positive"),
        ("8", "音乐很好听", "audio", "positive"),
    ])
    out_f = os.path.join(tmp, "model_f")
    rc, log = run(["code/train_rbt3_v2.py", "--task", "aspect",
                   "--train", os.path.join(tmp, "train_zeropad.csv"),
                   "--eval", os.path.join(tmp, "eval_plain.csv"),
                   "--out", out_f])
    check(rc != 0, f"前导零 ID 被识别为同一评论（退出码 {rc}）")
    check("相同 review_id" in log, "报错信息指出 ID 交叉（按整数比较）")

    out_d = os.path.join(tmp, "model_d")
    os.makedirs(out_d, exist_ok=True)
    fake_w = os.path.join(out_d, "model.safetensors")
    with open(fake_w, "wb") as f:
        f.write(b"ORIGINAL-WEIGHTS")
    before = os.stat(fake_w).st_mtime_ns
    rc, _ = run(["code/train_rbt3_v2.py", "--task", "aspect",
                 "--train", os.path.join(tmp, "train.csv"),
                 "--eval", os.path.join(tmp, "eval_textoverlap.csv"),
                 "--out", out_d])
    with open(fake_w, "rb") as f:
        after_bytes = f.read()
    check(rc != 0 and after_bytes == b"ORIGINAL-WEIGHTS" and os.stat(fake_w).st_mtime_ns == before,
          "校验失败时已有模型权重原样保留（未覆盖）")

    check_header("5. 严格留出集评估：异常必须非零退出")
    noids = os.path.join(tmp, "model_noids")
    os.makedirs(noids, exist_ok=True)
    rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", noids])
    check(rc != 0, f"模型目录缺 holdout_ids.json 时退出码非零（实际 {rc}）")
    check("holdout_ids.json" in log, "报错信息指出缺少 holdout_ids.json")

    empty = os.path.join(tmp, "empty_ids.json")
    with open(empty, "w", encoding="utf-8") as f:
        json.dump([], f)
    rc, log = run(["code/evaluate_full.py", "--heldout", "--eval-ids", empty])
    check(rc != 0, f"留出集为空时退出码非零（实际 {rc}）")
    check("留出集为空" in log, "报错信息指出留出集为空")

    # 5c. 元数据四种异常：缺失 / {} / 缺字段 / 空串，都不得放行
    for name in ("missing", "empty_obj", "no_sha", "blank_sha"):
        d = os.path.join(tmp, "meta_" + name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "holdout_ids.json"), "w", encoding="utf-8") as f:
            json.dump([161798229], f)
        if name == "missing":
            pass
        elif name == "empty_obj":
            with open(os.path.join(d, "task_meta.json"), "w", encoding="utf-8") as f:
                f.write("{}")
        else:
            meta = {"task": "aspect", "holdout_sha256": "a" * 64}
            if name == "no_sha":
                meta.pop("holdout_sha256")
            else:
                meta["holdout_sha256"] = ""
            with open(os.path.join(d, "task_meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f)
        rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", d])
        check(rc != 0 and "严格留出集评估已中止" in log,
              f"元数据异常（{name}）时中止（退出码 {rc}）")

    # 5d. 指纹不符（有效但错误的值）
    badhash = os.path.join(tmp, "model_badhash")
    os.makedirs(badhash, exist_ok=True)
    with open(os.path.join(badhash, "holdout_ids.json"), "w", encoding="utf-8") as f:
        json.dump([161798229], f)
    with open(os.path.join(badhash, "task_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"task": "aspect", "holdout_sha256": "0" * 64}, f)
    rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", badhash])
    check(rc != 0, f"留出集与模型指纹不符时退出码非零（实际 {rc}）")
    check("指纹与模型不符" in log, "报错信息指出指纹不符")

    # 5e. --eval-ids 指向训练评论（指纹按内容校验，不按路径）
    ids_bad = os.path.join(tmp, "ids_train.json")
    with open(ids_bad, "w", encoding="utf-8") as f:
        json.dump([161798229], f)
    rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", badhash,
                   "--eval-ids", ids_bad])
    check(rc != 0, f"--eval-ids 与模型指纹不符时中止（退出码 {rc}）")

    # 5f. 显式允许未验证 → 必须有"参考评估"字样，且不得出现"严格留出集评估"
    rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", badhash,
                   "--eval-ids", ids_bad, "--allow-unverified-holdout"])
    check("参考评估" in log, "--allow-unverified-holdout 时标为参考评估")
    check("严格留出集评估：" not in log, "参考评估时不再宣称严格留出集口径")

    check_header("6. 模型发布：保留用户文件、尾部路径、失败回滚")
    import train_rbt3_v2 as train_mod

    def save_new(d):
        with open(os.path.join(d, "model.safetensors"), "wb") as f:
            f.write(b"NEW-WEIGHTS")

    # 6a. 目录内已有用户文件：必须保留
    out_keep = os.path.join(tmp, "model_keep")
    os.makedirs(out_keep, exist_ok=True)
    with open(os.path.join(out_keep, "model.safetensors"), "wb") as f:
        f.write(b"OLD-WEIGHTS")
    for name, content in [("train.csv", "user train"), ("eval.csv", "user eval"), ("notes.txt", "我的笔记")]:
        with open(os.path.join(out_keep, name), "w", encoding="utf-8") as f:
            f.write(content)
    train_mod.publish_model(out_keep, save_new, {"task": "aspect", "holdout_sha256": ""})
    with open(os.path.join(out_keep, "model.safetensors"), "rb") as f:
        w_after = f.read()
    kept = all(os.path.exists(os.path.join(out_keep, n)) for n in ("train.csv", "eval.csv", "notes.txt"))
    check(w_after == b"NEW-WEIGHTS", "发布后权重已更新")
    check(kept, "目录内非模型文件（train.csv/eval.csv/notes.txt）全部保留")
    with open(os.path.join(out_keep, "notes.txt"), encoding="utf-8") as f:
        check(f.read() == "我的笔记", "保留文件内容未被改写")

    # 6b. 输出路径尾部带分隔符（旧实现拼出目录内部的备份路径，Windows 报 WinError 87）
    out_slash = os.path.join(tmp, "model_slash") + os.sep
    returned = train_mod.publish_model(out_slash, save_new, {"task": "aspect", "holdout_sha256": ""})
    check(os.path.isdir(os.path.join(tmp, "model_slash")),
          "尾部带分隔符的输出路径可正常发布")
    check(os.path.basename(returned.rstrip("\\/")) == "model_slash",
          f"返回值已规范化（{os.path.basename(returned)}）")

    # 6c. 发布中途失败：旧权重与用户文件都不能丢
    out_rb = os.path.join(tmp, "model_rollback")
    os.makedirs(out_rb, exist_ok=True)
    with open(os.path.join(out_rb, "model.safetensors"), "wb") as f:
        f.write(b"OLD-WEIGHTS")
    with open(os.path.join(out_rb, "keep.txt"), "w", encoding="utf-8") as f:
        f.write("keep me")
    real_move = shutil.move

    def flaky_move(src, dst):
        # 只拒绝"从临时目录移入目标目录"，回滚（从备份移回）要放行
        if ".tmp-" in os.path.abspath(src):
            raise OSError("模拟写入目标目录失败")
        return real_move(src, dst)

    # train_rbt3_v2 在函数内 import shutil，它是同一个模块对象，直接打桩模块级即可
    shutil.move = flaky_move
    try:
        raised = False
        try:
            train_mod.publish_model(out_rb, save_new, {"task": "aspect", "holdout_sha256": ""})
        except OSError:
            raised = True
    finally:
        shutil.move = real_move
    with open(os.path.join(out_rb, "model.safetensors"), "rb") as f:
        rb_weights = f.read()
    check(raised, "发布失败时抛异常，不会静默报成功")
    check(rb_weights == b"OLD-WEIGHTS", "失败后旧权重已回滚")
    check(os.path.exists(os.path.join(out_rb, "keep.txt")), "失败后用户文件仍在")
    leftovers = [d for d in os.listdir(tmp) if ".tmp-" in d or ".old-" in d]
    check(not leftovers, f"失败后临时/备份目录已清理（残留 {leftovers}）")

    check_header("7. 数据准备脚本产物格式")
    gt = os.path.join(ROOT, "data", "annotation", "rbt3_aspect_train.csv")
    if os.path.exists(gt):
        with open(gt, encoding="utf-8-sig") as f:
            cols = next(csv.reader(f))
        check(cols[:1] == ["review_id"], f"金标训练集首列为 review_id（实际 {cols[:2]}）")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    print(f"通过 {OK} 项，失败 {FAIL} 项")
    if FAIL:
        print("结论: 存在失败项 ✗")
        raise SystemExit(1)
    print("结论: 全部通过 ✓")


if __name__ == "__main__":
    main()
