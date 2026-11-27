# -*- coding: utf-8 -*-
"""
交付物完整性校验：检查两层标签体系所有产物的存在性、行数、关键字段、跨游戏约束。
"""
from __future__ import annotations
import os
import sys
import json
import csv
import gzip

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

def check(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond

def main():
    ok = True
    print("=== 1. 两层标签体系 ===")
    from item_lexicon import ITEMS, APPID_ITEMS, ITEM2ASPECT, APPID_GAME
    ok &= check(len(ITEMS) == 75, f"条目数=75 (实际 {len(ITEMS)})")
    ok &= check(len(APPID_ITEMS) == 12, f"游戏数=12 (实际 {len(APPID_ITEMS)})")
    ok &= check(len(ITEM2ASPECT) == 75, f"条目->主题映射=75 (实际 {len(ITEM2ASPECT)})")
    ok &= check(all(v.startswith(("content","level_design","multiplayer","graphics",
                                   "performance","story","controls","monetization",
                                   "cheating","audio","community")) for v in ITEM2ASPECT.values()),
                 "所有条目归属合法通用主题")

    print("\n=== 2. taxonomy 文件 ===")
    tax = os.path.join(ROOT, "data", "taxonomy")
    for f in ("items_enriched.json", "patches.json", "appid_game_map.json"):
        p = os.path.join(tax, f)
        ok &= check(os.path.exists(p) and os.path.getsize(p) > 100, f"{f} 存在且非空")

    print("\n=== 3. 规则引擎两层打标 ===")
    from rule_labeler import label_review
    r1 = label_review("新关卡好玩，但联机老掉线")
    ok &= check(len(r1["aspects"]) == 2 and
                {a["aspect"] for a in r1["aspects"]} == {"content", "multiplayer"},
                "第一层：一句多主题情感相反")
    r2 = label_review("姜岛探索很好玩，但是八人联机老是掉线", appid=413150)
    ok &= check(len(r2["items"]) == 2, f"第二层：两条目命中 (实际 {len(r2['items'])})")
    r3 = label_review("姜岛探索很好玩", appid=730)
    ok &= check(len(r3["items"]) == 0, "跨游戏约束：CS2下姜岛不命中")

    print("\n=== 4. 结果表 ===")
    res = os.path.join(ROOT, "data", "result")
    specs = [("topic_sentiment_detail.csv", 500000), ("topic_sentiment_review_summary.csv", 1000000),
             ("topic_distribution.csv", 20), ("item_sentiment_detail.csv", 100),
             ("item_distribution.csv", 10), ("item_patch_summary.csv", 5)]
    for f, min_rows in specs:
        # 大表只提交 .csv.gz（未压缩版被 .gitignore 排除），干净 clone 里需要用 gz 兜底
        p = os.path.join(res, f)
        if not os.path.exists(p):
            p_gz = p + ".gz"
            p = p_gz if os.path.exists(p_gz) else p
        if os.path.exists(p):
            opener = gzip.open if p.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8-sig") as fh:
                n = sum(1 for _ in fh) - 1
            tag = "（gz）" if p.endswith(".gz") else ""
            ok &= check(n >= min_rows, f"{f}: {n} 行{tag}")
        else:
            ok &= check(False, f"{f} 不存在")

    # 条目层口径：无 none，分布合计等于明细；汇总固定四窗口列
    with open(os.path.join(res, "item_sentiment_detail.csv"), encoding="utf-8-sig") as fh:
        item_rows = list(csv.DictReader(fh))
    ok &= check(all(r["sentiment"] in ("positive", "negative", "neutral") for r in item_rows),
                "条目明细情感无 none（已回退 neutral）")
    with open(os.path.join(res, "item_distribution.csv"), encoding="utf-8-sig") as fh:
        dist_sum = sum(int(r["count"]) for r in csv.DictReader(fh))
    ok &= check(dist_sum == len(item_rows),
                f"条目分布合计={dist_sum} 与明细 {len(item_rows)} 一致")
    with open(os.path.join(res, "item_patch_summary.csv"), encoding="utf-8-sig") as fh:
        patch_cols = next(csv.reader(fh))
    need_windows = {"pre_14d", "release_day", "post_14d", "out_of_window"}
    ok &= check(need_windows.issubset(set(patch_cols)), "条目汇总含四个固定窗口列")

    print("\n=== 5. 标注集与文档 ===")
    ann = os.path.join(ROOT, "data", "annotation")
    tasks_p = os.path.join(ann, "annotation_tasks.csv")
    with open(tasks_p, encoding="utf-8-sig") as f:
        cols = next(csv.reader(f))
    ok &= check("gold_aspects_json" in cols and "gold_items_json" in cols,
                "标注集含两层 gold 列")
    for rel in ("data/result/topic_sentiment_detail.csv.gz",
                "data/result/topic_sentiment_review_summary.csv.gz",
                "data/taxonomy/item_dict.csv",
                "data/rbt3_model_aspect/holdout_ids.json"):
        ok &= check(os.path.exists(os.path.join(ROOT, *rel.split("/"))),
                    f"提交文件 {rel} 存在")
    for f in ("两层标签体系说明.md", "标注规范.md", "结果表字段说明.md", "效果对比方案.md"):
        ok &= check(os.path.exists(os.path.join(ROOT, "docs", f)), f"文档 {f} 存在")
    sql = open(os.path.join(ROOT, "sql", "hive_metrics.sql"), encoding="utf-8").read()
    ok &= check("item_sentiment_detail" in sql and "item_dict" in sql,
                "Hive SQL 含第二层建表与指标")
    ok &= check("OpenCSVSerde" in sql and "skip.header.line.count" in sql,
                "Hive SQL 使用引号 CSV SerDe 并跳过表头")
    ok &= check("主题层匹配数" in sql and "主题层未匹配数" in sql,
                "Hive 指标 11 输出两层匹配/未匹配数")

    print("\n=== 5b. 本轮复审修复不变量 ===")
    # 1) escapeChar 不能是反斜杠（否则 OpenCSV 2.3 破坏 JSON 里的 \"）
    import re
    esc_vals = re.findall(r'"escapeChar"\s*=\s*"([^"]*)"', sql)
    ok &= check(bool(esc_vals) and all(v != "\\\\" for v in esc_vals),
                f"Hive escapeChar 非反斜杠（实际 {esc_vals}）")
    # 2) 指标 10 显式按 window_bucket 过滤窗口内阶段
    ok &= check("window_bucket IN ('pre_14d', 'release_day', 'post_14d')" in sql,
                "Hive 指标 10 已限定窗口内阶段（排除 out_of_window）")
    # 3) 所有中文别名均已加反引号
    unquoted = [l.strip() for l in sql.splitlines()
                if re.search(r'AS\s+[\u4e00-\u9fff]', l) or re.search(r'ORDER BY\s+[\u4e00-\u9fff]', l)]
    ok &= check(not unquoted, "Hive 中文别名全部加反引号")
    # 4) 训练/评估统一从标注集读 gold，不再依赖固定 GOLD 字典
    src_train = open(os.path.join(HERE, "prepare_gold_3way.py"), encoding="utf-8").read()
    src_eval = open(os.path.join(HERE, "evaluate_full.py"), encoding="utf-8").read()
    ok &= check("from gold_annotations import GOLD" not in src_train and
                "from gold_annotations import GOLD" not in src_eval,
                "训练与评估均不再使用固定 GOLD 字典")
    ok &= check("def load_gold" in src_train and "def load_gold" in src_eval,
                "训练与评估均从标注集读取 gold")
    # 5) 银标写独立文件，不覆盖金标
    src_prep = open(os.path.join(HERE, "prepare_rbt3_data_v2.py"), encoding="utf-8").read()
    ok &= check('suffix="_silver"' in src_prep and "--item-only" in src_prep,
                "银标写独立 *_silver 文件并支持 --item-only")
    # 6) 评估失败返回非零
    ok &= check("raise SystemExit(2)" in src_eval,
                "--rbt3 评估失败时返回非零退出码")

    print("\n=== 5c. 切分与留出集守卫（第三轮复审）===")
    src_train_v2 = open(os.path.join(HERE, "train_rbt3_v2.py"), encoding="utf-8").read()
    # 1) 先校验、后保存：校验调用必须出现在发布调用之前
    i_val = src_train_v2.find("load_and_validate(args)")
    i_save = src_train_v2.find("publish_model(args.out")
    ok &= check(0 <= i_val < i_save, "训练先完成数据校验再发布模型（校验在保存之前）")
    ok &= check("def publish_model" in src_train_v2 and
                "save_pretrained(args.out)" not in src_train_v2,
                "模型权重走原子发布，失败不覆盖旧模型")
    # 2) 留出 ID 取验证集 review_id 列，不再靠文本回查标注集
    ok &= check('r.get("review_id")' in src_train_v2 and
                "annotation_tasks.csv" not in src_train_v2,
                "留出 ID 直接取验证集 review_id，不再回查标注集")
    # 3) 金标与银标共用 split_utils 的分组切分（含按组抽样）
    src_gold = open(os.path.join(HERE, "prepare_gold_3way.py"), encoding="utf-8").read()
    ok &= check("from split_utils import" in src_prep and "_sample_by_group" in src_prep,
                "银标复用 split_utils 分组切分，并支持按组抽样")
    ok &= check("from split_utils import" in src_gold and "split_grouped" in src_gold,
                "金标同样按组切分（不同 review_id 的相同文本不跨集合）")
    # 3b) 训练两侧 ID 都要完整合法、按整数比较
    ok &= check("def _check_ids" in src_train_v2 and "int(x)" in src_train_v2,
                "训练前置校验检查两侧 review_id 并按整数比较")
    # 3c) 发布只替换模型产物，保留用户文件
    ok &= check("def _is_model_artifact" in src_train_v2 and "normpath(os.path.abspath(out_dir))" in src_train_v2,
                "模型发布只替换模型产物（保留用户文件、规范化输出路径）")
    # 4) 严格评估：必须有可用指纹，且按实际选中的 ID 集合校验
    ok &= check("def _load_meta" in src_eval and "holdout_sha256" in src_eval,
                "严格评估必须有可用的模型留出指纹（缺失/空串不再放行）")
    ok &= check("_sha256_ids(eval_ids)" in src_eval,
                "留出指纹按实际选中的 ID 集合校验（不依赖文件路径）")
    ok &= check("allow_unverified_holdout" in src_eval and "参考评估" in src_eval,
                "显式放行未验证留出集时标为参考评估")
    # 5) 训练数据首列带 review_id
    gt = os.path.join(ROOT, "data", "annotation", "rbt3_aspect_train.csv")
    if os.path.exists(gt):
        with open(gt, encoding="utf-8-sig") as f:
            ok &= check(next(csv.reader(f))[:1] == ["review_id"], "金标训练集首列为 review_id")
    # 6) 真跑一遍守卫回归测试
    import subprocess
    guard = subprocess.run([sys.executable, os.path.join(HERE, "verify_split_guard.py")],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    ok &= check(guard.returncode == 0,
                "verify_split_guard.py 全部通过（分组切分/前置校验/留出集守卫）")
    if guard.returncode != 0:
        print((guard.stdout or "")[-1500:])
        print((guard.stderr or "")[-800:])

    print("\n=== 6. 脚本可导入 ===")
    for m in ("item_matcher", "build_item_results", "build_item_taxonomy",
              "prepare_rbt3_data_v2", "train_rbt3_v2", "infer_rbt3_v2", "merge_gold"):
        try:
            __import__(m)
            ok &= check(True, f"{m}.py 导入成功")
        except Exception as e:
            ok &= check(False, f"{m}.py 导入失败: {e}")

    print("\n" + "=" * 50)
    print("总体:", "全部通过 ✓" if ok else "存在失败项 ✗")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
