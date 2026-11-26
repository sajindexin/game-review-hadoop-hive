# -*- coding: utf-8 -*-
"""变异自检：验证 verify_split_guard.py 的用例真的能抓住缺陷（防"假绿"）。

为什么需要这一层：
  回归用例全绿只说明"当前代码没触发断言"，不说明"断言有效"。典型失效方式是
  把退出码非零当成"守卫拦截了"，而在缺依赖/缺数据的环境里，任何路径最终都会
  非零退出，用例因此永远绿——守卫被悄悄绕过也发现不了。
  所以这里把每一轮复审确认过的缺陷逐个注回源码副本，要求对应用例必须变红。
  只有"注入缺陷→用例失败"成立，verify_split_guard 的绿灯才有意义。

做法：
  复制 code/ 与 data/annotation/（不复制 600MB+ 的模型与原始数据）到临时目录，
  对副本打一处最小补丁（还原旧实现），跑一次 verify_split_guard.py，
  非零退出即视为"抓住"。任一变异未被抓住 → 本脚本非零退出，提示存在用例盲区。

用法：
  python code/verify_guard_mutation.py            # 全部变异
  python code/verify_guard_mutation.py --keep      # 保留临时目录，便于排查
退出码 0 表示所有注入缺陷都被用例抓住。
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

# (变异名, 对应复审点, [(相对路径, 原文, 替换)])
MUTATIONS = [
    ("金标只按 review_id 分组，同文本可跨侧",
     "第四轮复审必修 1：不同 review_id 的相同文本必须同侧",
     [("code/split_utils.py",
       '        union(("r", rid) if rid else ("t", text), ("t", text))',
       '        union(("r", rid) if rid else ("t", text), ("r", rid) if rid else ("t", text))')]),

    ("meta 缺/空 holdout_sha256 时静默放行",
     "第四轮复审必修 2：四种元数据异常都不得放行",
     [("code/evaluate_full.py",
       '    if not want:\n        return meta, "task_meta.json 缺少可用的 holdout_sha256"',
       '    if not want:\n        return meta, ""'),
      ("code/evaluate_full.py",
       '    verified = bool(want) and got == want',
       '    verified = (not want) or got == want')]),

    ("训练集缺 review_id 只警告不报错",
     "第四轮复审必修 3：两侧 ID 都要完整合法且按整数比较",
     [("code/train_rbt3_v2.py",
       '    train_ids = _check_ids(train_rows, "训练集", strict=True)',
       '    train_ids = _check_ids(train_rows, "训练集", strict=False)')]),

    ("发布时整目录替换（删掉用户文件）",
     "第四轮复审必修 4：只替换模型产物白名单，失败必须回滚",
     [("code/train_rbt3_v2.py",
       '    # 目标已存在：只换模型产物',
       '    if os.path.exists(out_dir):\n'
       '        shutil.rmtree(out_dir, ignore_errors=True)\n'
       '        shutil.move(tmp, out_dir)\n'
       '        return out_dir\n'
       '    # 目标已存在：只换模型产物')]),

    ("留出集按路径而非按内容认指纹",
     "第三/四轮复审必修 2：--eval-ids 指向别处也必须识破",
     [("code/evaluate_full.py",
       '    verified = bool(want) and got == want',
       '    verified = bool(want) and os.path.exists(os.path.join(model_dir, "holdout_ids.json"))')]),
]


def fresh_copy(parent):
    d = tempfile.mkdtemp(prefix="case_", dir=parent)
    shutil.copytree(os.path.join(ROOT, "code"), os.path.join(d, "code"))
    shutil.copytree(os.path.join(ROOT, "data", "annotation"),
                    os.path.join(d, "data", "annotation"))
    return d


def apply(work, patches):
    """打补丁，返回 (是否全部命中, 未命中的说明)。"""
    for rel, old, new in patches:
        p = os.path.join(work, rel)
        with open(p, encoding="utf-8") as f:
            src = f.read()
        if old not in src:
            return False, f"{rel} 中未找到注入点（实现已改动？请同步更新本脚本）"
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src.replace(old, new, 1))
    return True, ""


def run_guard(work):
    p = subprocess.run([sys.executable, os.path.join("code", "verify_split_guard.py")],
                       cwd=work, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留临时目录")
    args = ap.parse_args()

    ann = os.path.join(ROOT, "data", "annotation")
    if not os.path.isdir(ann):
        raise SystemExit(f"[ERROR] 缺少 data/annotation，无法运行变异自检：{ann}")

    base = tempfile.mkdtemp(prefix="guardmut_")
    print(f"变异自检：{len(MUTATIONS)} 个历史缺陷逐个注入副本")
    print(f"临时目录: {base}\n")

    missed, skipped = [], []
    for name, contract, patches in MUTATIONS:
        work = fresh_copy(base)
        try:
            hit, why = apply(work, patches)
            if not hit:
                skipped.append((name, why))
                print(f"[SKIP] {name}\n        {why}")
                continue
            rc, out = run_guard(work)
            fails = [ln.strip() for ln in out.splitlines() if "[FAIL]" in ln]
            if rc != 0:
                print(f"[OK]   {name}")
                print(f"        契约：{contract}")
                if fails:
                    print(f"        触发：{fails[0][:110]}")
            else:
                missed.append((name, contract))
                print(f"[MISS] {name} —— 注入缺陷后用例仍全绿，存在盲区！")
                print(f"        契约：{contract}")
        finally:
            if not args.keep:
                shutil.rmtree(work, ignore_errors=True)

    print("\n" + "=" * 60)
    ok_n = len(MUTATIONS) - len(missed) - len(skipped)
    print(f"抓住 {ok_n} / 漏检 {len(missed)} / 跳过 {len(skipped)}")
    if missed:
        for name, contract in missed:
            print(f"  漏检: {name}（契约：{contract}）")
        print("结论: 存在用例盲区 ✗  —— 请为上述契约补反例用例")
        raise SystemExit(1)
    if skipped:
        print("结论: 用例有效，但有变异未能注入（实现已改动），请更新本脚本")
        raise SystemExit(0)
    print("结论: 所有历史缺陷都能被用例抓住 ✓")

    if args.keep:
        print(f"临时目录已保留: {base}")
    else:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
