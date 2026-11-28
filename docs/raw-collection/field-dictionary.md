# 原始采集层字段字典

来源：`原始采集层_Parquet_490.zip` 内 `manifest.json` 与 Parquet 实际 Schema。时间口径按随包 README 为 UTC。`STRING` 字段保留原值；除特别注明外，未对可空性施加约束。

## `game_review_raw.raw_reviews`

一条原始评测一行；12 款游戏的交付抽样，保留原始 JSON。

预期行数：**4,900,000**。HDFS：`/datasets/game_review_raw/v1_490/raw_reviews/part-00000.parquet`。

| 字段 | Hive 类型 | 含义 |
|---|---|---|
| `appid` | BIGINT | Steam 游戏 AppID |
| `item_id` | STRING | 采集信封中的条目 ID |
| `source` | STRING | 采集来源标识 |
| `task_id` | STRING | 采集任务 ID |
| `fetched_at` | STRING | 采集时间，原始字符串 |
| `schema_version` | BIGINT | 采集信封 Schema 版本 |
| `content_hash` | STRING | 采集内容哈希 |
| `source_file` | STRING | 原始源文件路径 |
| `source_member` | STRING | 压缩包内源文件名；非压缩源可为空 |
| `source_row` | BIGINT | 源文件中的非空记录序号 |
| `review_id` | STRING | Steam 评测 ID |
| `language` | STRING | 评测语言原值 |
| `review` | STRING | 原始评测正文 |
| `voted_up` | BOOLEAN | 是否推荐 |
| `timestamp_created` | BIGINT | 评测创建时间，Unix 秒（UTC） |
| `timestamp_updated` | BIGINT | 评测更新时间，Unix 秒（UTC） |
| `author_steamid` | STRING | 评测作者 Steam ID |
| `playtime_at_review` | BIGINT | 发布评测时游玩分钟数 |
| `playtime_forever` | BIGINT | 累计游玩分钟数 |
| `votes_up` | BIGINT | 有用票数 |
| `votes_funny` | BIGINT | 欢乐票数 |
| `comment_count` | BIGINT | 评论数 |
| `weighted_vote_score` | DOUBLE | Steam 加权投票分数 |
| `steam_purchase` | BOOLEAN | 是否在 Steam 购买 |
| `received_for_free` | BOOLEAN | 是否免费获得 |
| `refunded` | BOOLEAN | 是否退款 |
| `written_during_early_access` | BOOLEAN | 是否抢先体验阶段撰写 |
| `primarily_steam_deck` | BOOLEAN | 是否主要使用 Steam Deck |
| `segment_id` | STRING | 采集分段 ID |
| `segment_label` | STRING | 采集分段标签 |
| `raw_json` | STRING | 所选原始对象完整 JSON 字符串 |

## `game_review_raw.raw_announcements`

一条官方公告一行，保留原始 JSON。

预期行数：**4,934**。HDFS：`/datasets/game_review_raw/v1_490/raw_announcements/part-00000.parquet`。

| 字段 | Hive 类型 | 含义 |
|---|---|---|
| `appid` | BIGINT | Steam 游戏 AppID |
| `item_id` | STRING | 采集信封中的条目 ID |
| `source` | STRING | 采集来源标识 |
| `task_id` | STRING | 采集任务 ID |
| `fetched_at` | STRING | 采集时间，原始字符串 |
| `schema_version` | BIGINT | 采集信封 Schema 版本 |
| `content_hash` | STRING | 采集内容哈希 |
| `source_file` | STRING | 原始源文件路径 |
| `source_member` | STRING | 压缩包内源文件名；非压缩源可为空 |
| `source_row` | BIGINT | 源文件中的非空记录序号 |
| `announcement_id` | STRING | 官方公告 ID |
| `title` | STRING | 公告标题 |
| `contents` | STRING | 公告正文 |
| `date` | BIGINT | 公告发布时间，Unix 秒（UTC） |
| `author` | STRING | 公告作者 |
| `url` | STRING | 公告链接 |
| `feed_type` | BIGINT | Steam 公告 feed 类型代码 |
| `feedname` | STRING | Steam feed 名称 |
| `feedlabel` | STRING | Steam feed 标签 |
| `is_external_url` | BOOLEAN | 公告链接是否指向外部网站 |
| `raw_json` | STRING | 所选原始对象完整 JSON 字符串 |

## `game_review_raw.game_counts`

一个游戏一行；原始、公告与历史清洗数量的对账表。

预期行数：**12**。HDFS：`/datasets/game_review_raw/v1_490/game_counts/part-00000.parquet`。

| 字段 | Hive 类型 | 含义 |
|---|---|---|
| `appid` | BIGINT | Steam 游戏 AppID |
| `game_name` | STRING | 游戏名称 |
| `raw_review_rows` | BIGINT | 本次交付的原始评测行数 |
| `historical_v1_sample_rows` | BIGINT | 历史 v1 采样行数 |
| `raw_review_duplicate_ids` | BIGINT | 原始评测重复 ID 数 |
| `announcement_rows` | BIGINT | 官方公告行数 |
| `announcement_duplicate_ids` | BIGINT | 公告重复 ID 数 |
| `review_source_status` | STRING | 评测来源状态说明 |
| `announcement_origin` | STRING | 公告来源说明 |
| `appid_filled_rows` | BIGINT | 原始信封 AppID 补齐行数 |
| `v1_cleaned_rows` | BIGINT | 历史 v1 清洗后行数 |
| `reconstructed_invalid_rows` | BIGINT | TF2/Dota 2 同源补入的无效记录数 |

## `game_review_raw.collection_batches`

一个采集任务/来源/分段分组一行。

预期行数：**33,967**。HDFS：`/datasets/game_review_raw/v1_490/collection_batches/part-00000.parquet`。

| 字段 | Hive 类型 | 含义 |
|---|---|---|
| `data_type` | STRING | 记录类型：评测或公告 |
| `appid` | BIGINT | Steam 游戏 AppID |
| `source_file` | STRING | 原始源文件路径 |
| `source_member` | STRING | 压缩包内源文件名；非压缩源可为空 |
| `task_id` | STRING | 采集任务 ID |
| `rows` | BIGINT | 该采集分组的行数 |
| `fetched_at_min` | STRING | 分组最早采集时间 |
| `fetched_at_max` | STRING | 分组最晚采集时间 |
| `event_time_min_utc` | STRING | 分组最早事件时间（UTC） |
| `event_time_max_utc` | STRING | 分组最晚事件时间（UTC） |
| `language_counts` | STRING | 语言计数的 JSON 字符串 |
| `query` | STRING | 采集查询条件或来源查询 |
| `batch_basis` | STRING | 采集批次划分依据 |
| `segment_id` | STRING | 采集分段 ID |
| `segment_label` | STRING | 采集分段标签 |
| `appid_filled_rows` | BIGINT | 原始信封 AppID 补齐行数 |

## 数据口径

`raw_reviews` 是交付抽样，不等于所有游戏的历史采集全量。TF2 与 Dota 2 的 40 万历史抽样原件未找到；交付者保留历史 v1 有效记录，并从同源原始文件补入符合清洗剔除规则的记录，分别为 1,163 与 1,563 条。具体依据见 ZIP 内 `sample_reconciliation.json`。
