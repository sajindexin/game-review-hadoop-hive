# 原始采集层 490 万评测交付（Parquet）

负责人：张成鑫

## Hive 入仓与验收材料

- [字段字典](field-dictionary.md)
- [建表 SQL](create_tables.sql)
- [验收 SQL](acceptance.sql)
- [入仓验收报告](acceptance-report.md)
- [入仓清单](manifest_verified.json) · [HDFS 哈希明细](hdfs_sha256_verified.tsv)
- [Hive 验收输出](hive_acceptance_stdout.log)
- [原始来源清单](source_manifest.json) · [逐游戏统计](game_counts.csv)

复现时先确认原始 Parquet 已上传到建表 SQL 指定的 HDFS 版本路径，再执行建表 SQL 和验收 SQL。原始 Parquet、完整采集批次清单及 `sample_reconciliation.json` 由原始数据交付包提供。

## 交付规模

- 12 款游戏原始评测 4,900,000 条。
- 12 款游戏官方公告 4,934 条。
- 采集任务及来源分组 33,967 个。

Parquet 采用 Snappy 压缩。此次按历史 v1 样本规模交付，其他十款游戏的全部评测及全部公告保持原样。

## TF2 与 Dota 2 抽样对账

| 游戏 | v1 有效原始记录 | 同源补入无效记录 | 交付原始评测 |
| --- | ---: | ---: | ---: |
| TF2 | 398,837 | 1,163 | 400,000 |
| Dota 2 | 398,437 | 1,563 | 400,000 |

两款游戏的历史原始 40 万条抽样文件未找到。依据用户确认的方案，保留 v1 全部有效评测对应的原始记录，经 review_id、原文、清洗文本及创建时间逐条对账一致。使用原 v1 清洗程序校验同源无效记录，并以固定随机种子 42 蓄水池抽样补足。历史抽样中被剔除的具体 2,726 条记录仍未知，补入记录来自完整原始文件，均符合 v1 无效评测剔除规则。具体记录 ID、源文件行号、原因及抽样规则见 sample_reconciliation.json。

## 逐游戏行数

| 游戏 | AppID | 原始评测 | 公告 |
| --- | ---: | ---: | ---: |
| Counter-Strike 2 | 730 | 500,000 | 461 |
| Rust | 252490 | 400,000 | 970 |
| Stardew Valley | 413150 | 400,000 | 72 |
| Black Myth: Wukong | 2358720 | 400,000 | 21 |
| Team Fortress 2 | 440 | 400,000 | 745 |
| Garry's Mod | 4000 | 400,000 | 202 |
| Tom Clancy's Rainbow Six Siege | 359550 | 400,000 | 776 |
| Terraria | 105600 | 400,000 | 149 |
| ELDEN RING | 1245620 | 400,000 | 98 |
| Left 4 Dead 2 | 550 | 400,000 | 299 |
| Dota 2 | 570 | 400,000 | 774 |
| Grand Theft Auto V Legacy | 271590 | 400,000 | 367 |
| 合计 | | 4,900,000 | 4,934 |

## 交付文件

| 文件 | 行数 | 用途 |
| --- | ---: | --- |
| `raw_reviews.parquet` | 4,900,000 | 合并原始评测 |
| `raw_announcements.parquet` | 4,934 | 合并官方公告 |
| `game_counts.parquet` | 12 | 逐游戏行数统计 |
| `collection_batches.parquet` | 33,967 | 采集批次及来源分组清单 |
| `game_counts.csv` / `collection_batches.csv` | | 便于查看的统计清单 |
| `source_manifest.json` | | 完整原件来源及抽取记录数 |
| `sample_reconciliation.json` | | TF2/Dota 2 抽样重建及验证详情 |
| `manifest.json` | | Parquet 文件行数、字段、SHA-256 |

## 字段与校验

`raw_json` 保留所选原始评测和公告的完整 JSON 对象。`appid`、`review_id` / `announcement_id`、正文、时间和常用字段可直接查询。`source_file`、`source_member`、`source_row` 指向原始文件和非空记录序号。所有时间口径 UTC。两款补录游戏的原始信封 appid 补齐情况沿用全量交付。

回读全部所选评测的 raw_json 验证逐行内容哈希和逐游戏行数，公告 Parquet 哈希与全量包一致，批次统计与交付数量对账一致。
