# Hadoop / Hive 数据验收记录

验收日期：2026-11-28（北京时间）

验收对象：《基于 Hadoop 与 Hive 的游戏更新反馈分析智能系统》组内数据仓库

## 1. 连接与验收范围

| 检查项               | 实测结果                                                       |
| -------------------- | -------------------------------------------------------------- |
| HiveServer2 地址     | `hive.hujiayuan.com:10000`                                     |
| Tailscale 内网地址   | `100.125.28.104`                                               |
| DNS 解析及 TCP 端口  | 通过                                                           |
| Hive 查询 `SELECT 1` | 返回 `1`                                                       |
| 数据库与表发现       | `SHOW DATABASES`、`SHOW TABLES` 正常                           |
| 外部 Parquet 表      | `DESCRIBE FORMATTED` 显示相应 HDFS 存储位置及 Parquet 输入格式 |

本次通过 HiveServer2 执行只读 SQL，核对各表实际行数、逐游戏评测数量、覆盖表评测 ID 唯一性以及组员交付清单。HDFS 位置依据 Hive 元数据记录。

已发现 `game_reviews`、`hadoop_delivery`、`game_review_upload`、`nlp_topic`、`steam_dw` 五个业务数据库，以及 Hive 的 `default` 数据库。

## 2. 清洗评测验收

`game_reviews.reviews_v1` 为外部 Parquet 表，存储于 `hdfs://namenode:8020/datasets/game_reviews/v1`。`game_reviews.reviews_latest` 为视图。

逐游戏行数与组内 v1 `cleaning_reconciliation.csv` 一致。`hadoop_delivery.review_coverage` 中每款游戏的总行数与不同 `review_id` 数量相等，且逐游戏数量与清洗表一致。

| 游戏                           |   AppID |    原始样本数 |   v1 清洗预期 | Hive 清洗实测 | 覆盖表实测（不同评测 ID） |
| ------------------------------ | ------: | ------------: | ------------: | ------------: | ------------------------: |
| Counter-Strike 2               |     730 |       500,000 |       497,646 |       497,646 |                   497,646 |
| Rust                           |  252490 |       400,000 |       400,000 |       400,000 |                   400,000 |
| Stardew Valley                 |  413150 |       400,000 |       400,000 |       400,000 |                   400,000 |
| Black Myth: Wukong             | 2358720 |       400,000 |       399,196 |       399,196 |                   399,196 |
| Team Fortress 2                |     440 |       400,000 |       398,837 |       398,837 |                   398,837 |
| Garry's Mod                    |    4000 |       400,000 |       398,699 |       398,699 |                   398,699 |
| Tom Clancy's Rainbow Six Siege |  359550 |       400,000 |       398,628 |       398,628 |                   398,628 |
| Terraria                       |  105600 |       400,000 |       398,611 |       398,611 |                   398,611 |
| ELDEN RING                     | 1245620 |       400,000 |       398,514 |       398,514 |                   398,514 |
| Left 4 Dead 2                  |     550 |       400,000 |       398,491 |       398,491 |                   398,491 |
| Dota 2                         |     570 |       400,000 |       398,437 |       398,437 |                   398,437 |
| Grand Theft Auto V Legacy      |  271590 |       400,000 |       397,521 |       397,521 |                   397,521 |
| **合计**                       |         | **4,900,000** | **4,884,580** | **4,884,580** |             **4,884,580** |

验收结果：清洗评测 12 款游戏的行数全部符合 v1 交付口径。覆盖表内 `(appid, review_id)` 唯一性通过，覆盖表与清洗表的逐游戏行数一致。

## 3. 版本目录、Hive 统计与模型证据

数据库 `hadoop_delivery` 的外部 Parquet 表位于 `hdfs://namenode:8020/datasets/project_deliveries/2026-11-27/hadoop_delivery/` 下。以下十张表均已实际查询行数。

| 表名               | 内容                           | Hive 实测行数 |
| ------------------ | ------------------------------ | ------------: |
| `patches`          | 正式版本目录                   |            26 |
| `patch_items`      | 改动条目目录                   |            75 |
| `app_languages`    | 游戏与语种分组                 |           109 |
| `patch_calendar`   | 版本分析窗口日历               |           754 |
| `patch_daily`      | 逐日反馈统计                   |         6,612 |
| `patch_phase`      | 更新前、当日、更新后分阶段统计 |           684 |
| `patch_comparison` | 更新前后对比统计               |           228 |
| `review_coverage`  | 全量评测覆盖明细               |     4,884,580 |
| `candidate_scores` | 评测与改动候选模型分数         |     6,622,542 |
| `verified_quotes`  | 已复核评测与改动关联           |           469 |

版本、改动、统计数量与 `game_review_upload.gr_manifest` 对账一致。覆盖表、候选分数及复核引文数量与 [版本分析模块交付说明](../game-update-feedback-analysis/README.md) 一致。

## 4. 11 月 28 日上传数据清单对账

`game_review_upload.gr_manifest` 中包含 23 项 Parquet 产物的预期行数。以下各表通过 `COUNT(*)` 查询验证，实测数量与上传清单中的预期数量全部一致。

| `game_review_upload` 表名      |  清单预期 | Hive 实测 |
| ------------------------------ | --------: | --------: |
| `gr_patches`                   |        26 |        26 |
| `gr_items`                     |        75 |        75 |
| `gr_window_coverage`           |        26 |        26 |
| `gr_metrics_patch_compare`     |       228 |       228 |
| `gr_metrics_phase`             |       684 |       684 |
| `gr_metrics_daily`             |     6,612 |     6,612 |
| `gr_long_term_terms`           |        75 |        75 |
| `gr_candidate_terms`           |        40 |        40 |
| `gr_verified_quotes`           |       469 |       469 |
| `gr_lexicon_items`             |        75 |        75 |
| `gr_lexicon_global`            |        58 |        58 |
| `gr_agent_corpus_62007`        |    62,007 |    62,007 |
| `gr_development_selection`     |       138 |       138 |
| `gr_cross_game_eval`           |       708 |       708 |
| `gr_enhanced_validation`       |       538 |       538 |
| `gr_encoder_provenance`        |         7 |         7 |
| `gr_nlp_item_sentiment_detail` |     1,593 |     1,593 |
| `gr_nlp_topic_detail_zh`       |   535,623 |   535,623 |
| `gr_nlp_topic_detail_en`       | 1,120,567 | 1,120,567 |
| `gr_nlp_topic_detail_rbt3`     | 1,543,242 | 1,543,242 |
| `gr_nlp_review_summary_zh`     | 1,656,752 | 1,656,752 |
| `gr_nlp_review_summary_en`     | 2,824,133 | 2,824,133 |
| `gr_nlp_review_summary_rbt3`   | 1,656,752 | 1,656,752 |

验收结果：23 项交付数据的行数均通过。Agent 可使用 62,007 条检索语料、469 对复核引文、版本目录和同义词词典作为后续构建索引的输入。

## 5. NLP 分析数据

数据库 `nlp_topic` 的数据位于 `hdfs://namenode:8020/datasets/project_deliveries/2026-11-27/nlp_topic/` 下。十二张表均可正常查询。

| 表名                                  | 内容                   | Hive 实测行数 |
| ------------------------------------- | ---------------------- | ------------: |
| `item_dict`                           | 改动条目字典           |            75 |
| `item_distribution`                   | 条目情感分布           |            68 |
| `item_patch_summary`                  | 条目版本阶段汇总       |            30 |
| `item_sentiment_detail`               | 条目情感明细           |         1,593 |
| `topic_distribution`                  | 中文主题分布           |            33 |
| `topic_distribution_en`               | 英文主题分布           |            33 |
| `topic_sentiment_detail`              | 中文规则主题情感明细   |       535,623 |
| `topic_sentiment_detail_en`           | 英文规则主题情感明细   |     1,120,567 |
| `topic_sentiment_detail_rbt3`         | 中文 RBT3 主题情感明细 |     1,543,242 |
| `topic_sentiment_review_summary`      | 中文评测级汇总         |     1,656,752 |
| `topic_sentiment_review_summary_en`   | 英文评测级汇总         |     2,824,133 |
| `topic_sentiment_review_summary_rbt3` | 中文 RBT3 评测级汇总   |     1,656,752 |

中文、英文规则结果和中文 RBT3 结果的明细及汇总行数与 11 月 28 日上传清单一致。RBT3 是针对中文评测的另一套模型分析结果，统计时应按相应分析口径选择结果。

## 6. 统一数据仓库 `steam_dw`

数据库外部 Parquet 表的存储目录为 `hdfs://namenode:8020/datasets/steam_dw/core_2026-11-28/`。DIM、ODS 和 ADS 共十二张表均已完成实际行数查询。

| 层级 | 表名                | Hive 实测行数 |
| ---- | ------------------- | ------------: |
| DIM  | `dim_patch`         |            26 |
| DIM  | `dim_item`          |            75 |
| ODS  | `ods_nlp_item`      |         1,593 |
| ODS  | `ods_nlp_review`    |     4,480,885 |
| ODS  | `ods_nlp_topic`     |     1,656,190 |
| ADS  | `ads_patch_compare` |           228 |
| ADS  | `ads_patch_daily`   |         6,612 |
| ADS  | `ads_patch_stage`   |           684 |
| ADS  | `ads_window_check`  |            26 |
| ADS  | `ads_item_dist`     |            68 |
| ADS  | `ads_item_patch`    |            30 |
| ADS  | `ads_topic_dist`    |            66 |

`ods_nlp_review` 行数等于中文与英文规则评测级汇总之和，`ods_nlp_topic` 行数等于中英文规则主题明细之和。版本维度及逐日、阶段和更新对比 ADS 表与上游分析统计数量一致。

## 7. 原始采集层入仓情况

本地已完成 `原始采集层_Parquet_490万交付_20261128.zip` 交付包，经 Parquet 回读、逐游戏统计和哈希校验。包含下列四个文件。

| Parquet 文件                 |  交付行数 | Hive 当前状态        |
| ---------------------------- | --------: | -------------------- |
| `raw_reviews.parquet`        | 4,900,000 | 尚未发现对应 Hive 表 |
| `raw_announcements.parquet`  |     4,934 | 尚未发现对应 Hive 表 |
| `collection_batches.parquet` |    33,967 | 尚未发现对应 Hive 表 |
| `game_counts.parquet`        |        12 | 尚未发现对应 Hive 表 |

原始包中 TF2 与 Dota 2 各保留 40 万条评测，其中 797,274 条有效评测与历史 v1 对齐，另从原始文件补足 2,726 条符合 v1 清洗剔除规则的无效评测，具体记录见交付包中的 `sample_reconciliation.json`。

待入仓验收由 Hadoop/Hive 负责人完成，确认 HDFS 文件上传情况，创建相应的外部表，然后按上述目标行数和逐游戏数量复核。

## 8. 验收复现 SQL

使用 Hive 客户端连接 `hive.hujiayuan.com:10000` 后执行以下查询。

```sql
-- 连通性与表发现
SELECT 1;
SHOW DATABASES;
SHOW TABLES IN game_reviews;
SHOW TABLES IN hadoop_delivery;
SHOW TABLES IN game_review_upload;
SHOW TABLES IN nlp_topic;
SHOW TABLES IN steam_dw;

-- 12 款游戏的清洗明细对账
SELECT appid, COUNT(*) AS row_count
FROM game_reviews.reviews_v1
GROUP BY appid
ORDER BY appid;

-- 覆盖表逐游戏数量与唯一性
SELECT appid, COUNT(*) AS row_count,
       COUNT(DISTINCT review_id) AS unique_review_ids
FROM hadoop_delivery.review_coverage
GROUP BY appid
ORDER BY appid;

-- 核心模型结果
SELECT COUNT(*) FROM hadoop_delivery.candidate_scores;
SELECT COUNT(*) FROM hadoop_delivery.verified_quotes;

-- 已上传产物清单和代表性验收
SELECT `file`, `rows`, `note` FROM game_review_upload.gr_manifest;
SELECT COUNT(*) FROM game_review_upload.gr_agent_corpus_62007;
SELECT COUNT(*) FROM steam_dw.dim_patch;
SELECT COUNT(*) FROM steam_dw.ads_patch_daily;
SELECT COUNT(*) FROM steam_dw.ods_nlp_review;

-- 检查表的 HDFS 位置、字段及格式
DESCRIBE FORMATTED game_reviews.reviews_v1;
DESCRIBE FORMATTED hadoop_delivery.patch_daily;
```

实测多表计数可以使用 `UNION ALL` 分批查询。本次在一次合并九张 `steam_dw` 表的计数时触发 Tez 的 counters 上限（121 > 120），拆分成四张和五张表后均成功。

## 9. 验收结论

- 清洗评测、版本目录、Hive 统计、模型覆盖与候选分数、复核引文、NLP 及 Agent 检索语料的行数验收通过。
- 统一数仓 `steam_dw` 的十二张表均可查询，可作为后端数据访问和前端接口迁移的数据来源。



