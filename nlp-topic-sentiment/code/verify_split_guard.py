# -*- coding: utf-8 -*-
"""回归测试：训练/验证切分与留出集守卫（不需要 torch，可在干净 clone 直接跑）。

对应复审提出的三处必修：
  1. 银标切分必须按原始 review_id 分组，同一评论的多条目不能跨 train/eval
  2. 重复文本也不能跨 train/eval
  3. 训练的前置校验必须在保存模型之前；校验失败时不得创建或覆盖模型目录
  4. 严格留出集评估在缺少 holdout_ids.json、留出集为空、留出集与模型指纹不符时
     必须非零退出，不能再静默套用旧留出集

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


def main():
    print("=== 1. 分组切分：同一 review / 同一文本不可跨集合 ===")
    from prepare_rbt3_data_v2 import group_by_review

    # 同一条评论命中 3 个条目（复审实例 102625626 的形态）
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

    # 不同评论但证据文本完全相同：必须同组，否则文本会跨集合
    dup = [
        ("300001", "一模一样的话", "content", "positive"),
        ("300002", "一模一样的话", "content", "negative"),
    ]
    g2 = group_by_review(dup)
    check(len(g2) == 1, f"相同文本的不同评论被并入同一分组（实际 {len(g2)} 个分组）")

    # 无 review_id 时退化为按文本分组，不能抛异常
    g3 = group_by_review([("", "只有文本", "content", "neutral")])
    check(len(g3) == 1, "缺 review_id 时退化按文本分组，不崩溃")

    print("\n=== 2. 真实银标数据：train/eval 零重叠 ===")
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

    print("\n=== 3. 训练前置校验：失败即退出，且不创建模型目录 ===")
    tmp = tempfile.mkdtemp(prefix="splitguard_")
    hdr = ["review_id", "review_text", "aspect", "sentiment"]
    write_csv(os.path.join(tmp, "train.csv"), hdr, [
        ("1", "姜岛探索很好玩", "content", "positive"),
        ("2", "联机老是掉线", "multiplayer", "negative"),
        ("3", "画面不错", "graphics", "positive"),
    ])

    # 3a. 文本重叠（不同 review_id，但文本相同）
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

    # 3b. review_id 重叠（同一评论换了个文本片段）
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

    # 3c. eval 缺 review_id 列（旧数据形态）
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

    # 3d. 已有模型目录时，校验失败不得改动其中任何文件
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

    print("\n=== 4. 严格留出集评估：异常必须非零退出 ===")
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

    badhash = os.path.join(tmp, "model_badhash")
    os.makedirs(badhash, exist_ok=True)
    with open(os.path.join(badhash, "holdout_ids.json"), "w", encoding="utf-8") as f:
        json.dump([161798229], f)
    with open(os.path.join(badhash, "task_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"task": "aspect", "holdout_sha256": "0" * 64}, f)
    rc, log = run(["code/evaluate_full.py", "--heldout", "--rbt3-model", badhash])
    check(rc != 0, f"留出集与模型指纹不符时退出码非零（实际 {rc}）")
    check("指纹不一致" in log, "报错信息指出指纹不一致")

    print("\n=== 5. 数据准备脚本产物格式 ===")
    gt = os.path.join(ROOT, "data", "annotation", "rbt3_aspect_train.csv")
    if os.path.exists(gt):
        with open(gt, encoding="utf-8-sig") as f:
            cols = next(csv.reader(f))
        check(cols[:1] == ["review_id"], f"金标训练集首列为 review_id（实际 {cols[:2]}）")

    print("\n=== 6. 原子发布：替换目标目录失败时必须回滚，不能把旧模型弄丢 ===")
    import train_rbt3_v2 as train_mod
    out = os.path.join(tmp, "model_rollback")
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "model.safetensors"), "wb") as f:
        f.write(b"OLD-WEIGHTS")
    real_replace = train_mod.os.replace
    attempts = {"n": 0}

    def flaky_replace(src, dst):
        # 允许"旧目录 → 备份"这一步，只在第一次"临时目录 → 目标目录"时抛错，
        # 模拟 Windows 上目标目录被其他进程短暂占用（os.replace 抛 OSError）。
        # 回滚那一步要放行，否则测的就不是回滚而是回滚失败分支了。
        if os.path.abspath(dst) == os.path.abspath(out):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("模拟目标目录被占用")
        return real_replace(src, dst)

    def save_ok(d):
        with open(os.path.join(d, "model.safetensors"), "wb") as f:
            f.write(b"NEW-WEIGHTS")

    train_mod.os.replace = flaky_replace
    try:
        raised = False
        try:
            train_mod.publish_model(out, save_ok, {"task": "aspect", "holdout_sha256": ""})
        except OSError:
            raised = True
    finally:
        train_mod.os.replace = real_replace
    check(raised, "替换失败时抛异常，不会静默报成功")
    with open(os.path.join(out, "model.safetensors"), "rb") as f:
        check(f.read() == b"OLD-WEIGHTS", "旧模型目录已回滚，权重未被覆盖")
    check(not [d for d in os.listdir(tmp) if d.startswith(".model_rollback.tmp-")],
          "失败后临时目录已清理")

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    print(f"通过 {OK} 项，失败 {FAIL} 项")
    if FAIL:
        print("结论: 存在失败项 ✗")
        raise SystemExit(1)
    print("结论: 全部通过 ✓")


if __name__ == "__main__":
    main()
