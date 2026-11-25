# data_sources 来源说明

这些文件不是本模块生成的，是上游交付物，**原样复制、不要修改**。

## verified_quotes.jsonl

- 来源：`feat/game-update-feedback-analysis` 分支
  `game-update-feedback-analysis/黄静雯_全组评测改动关联/窗口内已复核可引用关联.jsonl`
- 提交：`c26c38a`（2026-11-25 00:55 +08:00，message「补充 Agent 可用的已复核引文」）
- git blob sha1：`043c048a16da4997a5a8202f509e48ebd817c329`
- 行数：469
- 本地校验：本目录文件按 git blob 规则计算的 sha1 与上面一致（逐字节相同），不是二次加工版
- 内容：20 个版本、37 项官方改动、469 条 `AI逐条核对原文` 的引文

## catalog/*.jsonl / 更新前后对比.csv

- 来源：同分支 `黄静雯_全组版本与改动/` 与 `黄静雯_全组Hive统计/`
- 提交：`f02b0d5`（2026-11-25 10:20 +08:00）时的分支内容
- 用途：版本目录（26 个版本）、官方改动（75 项）、窗口指标（all / chinese / en）

## 重建方式

```powershell
python -m tools.build_knowledge --parquet "<v1>\releases\v1\data\cleaned_reviews.parquet" --out data
```

默认就会读本目录；`data/` 是生成物，可随时删掉重建（约 10 秒）。

## 如果上游更新了

黄静雯后续若提交了新的已复核引文（例如全量核验后导出更多对），把新文件覆盖到本目录，
重新跑一次构建即可；`data/verified.json` 会跟着变，Agent 不需要改代码。
