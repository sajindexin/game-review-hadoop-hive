# data_sources 来源说明

这些文件不是本模块生成的，是上游交付物，**原样复制、不要修改**。

## verified_quotes.jsonl

- 来源：`feat/game-update-feedback-analysis` 分支
  `game-update-feedback-analysis/上游版本分析模块_全组评测改动关联/窗口内已复核可引用关联.jsonl`
- 提交：`c26c38a`（2026-11-25 00:55 +08:00，message「补充 Agent 可用的已复核引文」）
- SHA-256：`84b4174a42f21a52413de5fd75f571e2982f626d88fc51e1d138ea9fc2f6f9d1`
  （与 PR2 `全量核验说明与验收记录.zip` 里 README 声明的值一致，本地实测相同）
- 行数：469 对，涉及 **445 条不同评测**（24 条评测命中多个改动，所以行数 > 评测数）
- 覆盖：26 个版本中的 **20 个**、75 项改动中的 **37 项**；
  6 个版本、38 项改动暂无引文，19 项只有 1–2 对（与 PR2 的统计一致）
- 字节保证：仓库 `.gitattributes` 写了 `data_sources/** -text`，Git 不做换行归一化；
  `git cat-file blob` 出来的内容 SHA-256 仍是 `84b4…`（不是二次加工版）
- 内容：20 个版本、37 项官方改动、469 条 `AI逐条核对原文` 的引文

## catalog/*.jsonl / 更新前后对比.csv

- 来源：同分支 `上游版本分析模块_全组版本与改动/` 与 `上游版本分析模块_全组Hive统计/`
- 提交：`f02b0d5`（2026-11-25 10:20 +08:00）时的分支内容
- 用途：版本目录（26 个版本）、官方改动（75 项）、窗口指标（all / chinese / en）

## 重建方式

```powershell
python -m tools.build_knowledge --parquet "<v1>\releases\v1\data\cleaned_reviews.parquet" --out data
```

默认就会读本目录；`data/` 是生成物，可随时删掉重建（约 10 秒）。

## 如果上游更新了

上游版本分析模块后续若提交了新的已复核引文（例如全量核验后导出更多对），把新文件覆盖到本目录，
重新跑一次构建即可；`data/verified.json` 会跟着变，Agent 不需要改代码。
