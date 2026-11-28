# 原始采集层数据验收报告

日期：2026-11-28；数据库：`game_review_raw`；版本：`v1_490`。

源 ZIP：`原始采集层_Parquet_490.zip`；SHA-256：`a3b438f337089f3204cf004099e9f92f17df88d79929a4b62aecb3706668a6a9`。源 ZIP 保持不变。

## 四表结果

| Hive 表 | Hive 行数 | 源清单行数 | HDFS SHA-256 |
|---|---:|---:|---|
| `game_review_raw.raw_reviews` | 4,900,000 | 4,900,000 | 与源文件一致 |
| `game_review_raw.raw_announcements` | 4,934 | 4,934 | 与源文件一致 |
| `game_review_raw.game_counts` | 12 | 12 | 与源文件一致 |
| `game_review_raw.collection_batches` | 33,967 | 33,967 | 与源文件一致 |

四张表均为 HDFS 版本化路径上的 Parquet 外部表。`hdfs_sha256_verified.tsv` 记录源与远端完整哈希；Hive 查询通过 Tailscale 地址 `node.taile33391.ts.net:10000` 执行。

## 分布与质量

- 评测覆盖 12 个 AppID；CS2 500,000 行，其余 11 款各 400,000 行。逐游戏 Hive 数量与源清单完全一致。
- 公告逐游戏 Hive 数量与源清单完全一致，共 4,934 行。
- `game_counts` 汇总得到评测 4,900,000 行、公告 4,934 行。
- `collection_batches` 的分组行数分别汇总为评测 4,900,000 行、公告 4,934 行。
- 源 Parquet 独立检查：评测/公告的主键缺失数、`raw_json` 缺失数、按 `(appid, ID)` 的重复数均为 0。
- HDFS 检查与 Hive 建表过程不修改原有 `game_reviews` 清洗表。

## 数据口径与限制

- 这 490 万条是交付抽样，不表示所有原始采集历史记录。
- TF2 与 Dota 2 的历史 40 万抽样原件未找到；交付者保留历史 v1 有效记录，并从同源原始文件补入 1,163 和 1,563 条符合无效评测规则的记录。原历史样本中具体被剔除的 2,726 条记录无法还原。详见源包 `sample_reconciliation.json`。
- 此处“零重复”按 `(appid, review_id)` 和 `(appid, announcement_id)` 计算；不代表跨不同游戏的 ID 必须唯一。
- 组员在另一台电脑上连接 Hive 的验收仍需实际测试。

## 查询示例

```sql
SHOW TABLES IN game_review_raw;
SELECT appid, COUNT(*) FROM game_review_raw.raw_reviews GROUP BY appid ORDER BY appid;
SELECT COUNT(*) FROM game_review_raw.raw_announcements;
```
