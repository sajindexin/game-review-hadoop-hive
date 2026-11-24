-- ============================================================
-- 中文 NLP 与专题统计 —— Hive 指标 SQL（v2：两层标签）
-- 数据版本：game_reviews v1（原始评论表 game_reviews.game_reviews_v1）
-- 两层标签：第一层 通用主题(11类) topic_sentiment_detail
--           第二层 具体更新条目(75项) item_sentiment_detail
-- 本模块产出的结果表需先导入 Hive（见 part 0）
-- 所有时间口径 UTC；字段名与 rule_labeler.py / item_matcher.py 输出一致
-- ============================================================

-- ------------------------------------------------------------
-- part 0：导入本模块结果表（一次性，dedicated database）
-- 本模块产出：topic_sentiment_detail.csv / topic_sentiment_review_summary.csv
-- ------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS nlp_topic;
USE nlp_topic;

-- 主题情感明细表（一条评论 × 一个主题 一行）
DROP TABLE IF EXISTS topic_sentiment_detail;
CREATE EXTERNAL TABLE topic_sentiment_detail (
  review_id      STRING  COMMENT '评论ID',
  appid          STRING  COMMENT '游戏AppID',
  game_name      STRING  COMMENT '游戏名',
  created_date   STRING  COMMENT '评论日期',
  game_version   STRING  COMMENT '版本号（黑神话/GTA5有值）',
  aspect         STRING  COMMENT '主题ID',
  name_zh        STRING  COMMENT '主题中文名',
  sentiment      STRING  COMMENT 'positive/negative/neutral',
  confidence     DOUBLE  COMMENT '规则置信度',
  sentiment_word STRING  COMMENT '命中的情感词',
  evidence       STRING  COMMENT '证据子句'
) ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
  STORED AS TEXTFILE LOCATION '/data/nlp_topic/topic_sentiment_detail';

-- 评论级汇总表
DROP TABLE IF EXISTS topic_sentiment_review;
CREATE EXTERNAL TABLE topic_sentiment_review (
  review_id          STRING,
  appid              STRING,
  game_name          STRING,
  created_date       STRING,
  game_version       STRING,
  voted_up           BOOLEAN,
  n_aspects          INT,
  pos_aspects        INT,
  neg_aspects        INT,
  primary_sentiment  STRING,
  aspects_json       STRING
) ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
  STORED AS TEXTFILE LOCATION '/data/nlp_topic/topic_sentiment_review';

-- 具体更新条目情感明细表（一条评论 × 一个具体条目 一行；第二层）
DROP TABLE IF EXISTS item_sentiment_detail;
CREATE EXTERNAL TABLE item_sentiment_detail (
  review_id      STRING,
  appid          STRING,
  game_name      STRING,
  created_date   STRING,
  game_version   STRING,
  patch_id       STRING  COMMENT '所属版本(黄静雯版本建模分支)',
  window_bucket  STRING  COMMENT 'pre_14d/release_day/post_14d/out_of_window',
  item_id        STRING  COMMENT '官方更新条目ID',
  item_title     STRING  COMMENT '官方更新条目标题',
  aspect         STRING  COMMENT '该条目归属的通用主题',
  sentiment      STRING,
  confidence     DOUBLE,
  matched_term   STRING  COMMENT '命中的官方别名',
  evidence       STRING
) ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
  STORED AS TEXTFILE LOCATION '/data/nlp_topic/item_sentiment_detail';

-- 更新条目字典表（75项，来自黄静雯版本建模分支 items.jsonl）
DROP TABLE IF EXISTS item_dict;
CREATE EXTERNAL TABLE item_dict (
  item_id     STRING,
  title       STRING,
  appid       STRING,
  game        STRING,
  aspect      STRING,
  description STRING
) ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
  STORED AS TEXTFILE LOCATION '/data/nlp_topic/item_dict';


-- ============================================================
-- 指标 1：主题情感总体分布（每个主题 正/负/中 条数与占比）
-- 演示："更新后评论里，明确谈到新地图（content）的有多少、情感如何"
-- ============================================================
USE nlp_topic;
SELECT
  name_zh AS 主题,
  aspect  AS 主题ID,
  COUNT(*) AS 标注总数,
  SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) AS 正向,
  SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) AS 负向,
  SUM(CASE WHEN sentiment='neutral'  THEN 1 ELSE 0 END) AS 中性,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比,
  ROUND(SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 负向占比
FROM topic_sentiment_detail
GROUP BY name_zh, aspect
ORDER BY 标注总数 DESC;


-- ============================================================
-- 指标 2：按游戏 × 主题的情感分布（12 款游戏横向对比）
-- ============================================================
SELECT
  game_name AS 游戏,
  name_zh   AS 主题,
  COUNT(*)  AS 提及数,
  SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END) AS 正向,
  SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END) AS 负向,
  ROUND(AVG(confidence), 3) AS 平均置信度
FROM topic_sentiment_detail
GROUP BY game_name, name_zh
ORDER BY game_name, 提及数 DESC;


-- ============================================================
-- 指标 3：主题情感随时间趋势（按天，可看更新前后变化）
-- ============================================================
SELECT
  created_date AS 日期,
  name_zh      AS 主题,
  COUNT(*)     AS 提及数,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比
FROM topic_sentiment_detail
WHERE created_date >= '2026-10-01'
GROUP BY created_date, name_zh
ORDER BY 主题, 日期;


-- ============================================================
-- 指标 4：版本前后 14 天对比（仅黑神话/GTA5 有 game_version）
--   若版本字段是日期型标签（GTA5），可直接截取；否则需版本发布日期表。
--   这里给出"以版本标签分组"的通用写法 + "按评论日期前后窗口"的写法。
-- ------------------------------------------------------------
-- 4a 按 game_version 分组看主题情感（版本维度）
-- ============================================================
SELECT
  game_name   AS 游戏,
  game_version AS 版本,
  name_zh     AS 主题,
  COUNT(*)    AS 提及数,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比,
  ROUND(SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 负向占比
FROM topic_sentiment_detail
WHERE game_version IS NOT NULL AND game_version <> ''
GROUP BY game_name, game_version, name_zh
ORDER BY game_name, game_version, 提及数 DESC;

-- ------------------------------------------------------------
-- 4b 更新前/后各 14 天窗口对比（示例：黑神话某版本节点 2026-11-10）
--    用 CASE 打标窗口，再比较两窗口的主题情感占比
-- ============================================================
SELECT
  CASE
    WHEN created_date >= '2026-10-27' AND created_date <  '2026-11-10' THEN '更新前14天'
    WHEN created_date >= '2026-11-10' AND created_date <= '2026-11-23' THEN '更新后14天'
    ELSE '窗口外'
  END AS 窗口,
  name_zh AS 主题,
  COUNT(*) AS 提及数,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比,
  ROUND(SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 负向占比
FROM topic_sentiment_detail
WHERE game_name = 'Black Myth: Wukong'
  AND created_date >= '2026-10-27'
GROUP BY
  CASE
    WHEN created_date >= '2026-10-27' AND created_date <  '2026-11-10' THEN '更新前14天'
    WHEN created_date >= '2026-11-10' AND created_date <= '2026-11-23' THEN '更新后14天'
    ELSE '窗口外'
  END,
  name_zh
ORDER BY 窗口, 提及数 DESC;


-- ============================================================
-- 指标 5：整体推荐率（voted_up）vs 主题级情感 —— 一致性验算
--   用评论级汇总表，检查"整体好评但某主题差"的比例
-- ============================================================
SELECT
  game_name AS 游戏,
  COUNT(*) AS 中文评论数,
  ROUND(AVG(CASE WHEN voted_up THEN 1.0 ELSE 0.0 END), 4) AS 整体推荐率,
  ROUND(AVG(CASE WHEN neg_aspects > 0 THEN 1.0 ELSE 0.0 END), 4) AS 含负向主题占比,
  ROUND(AVG(CASE WHEN pos_aspects > 0 THEN 1.0 ELSE 0.0 END), 4) AS 含正向主题占比,
  ROUND(AVG(n_aspects), 2) AS 平均主题数
FROM topic_sentiment_review
GROUP BY game_name
ORDER BY 中文评论数 DESC;


-- ============================================================
-- 指标 6：混合情感评论数（一句里既有正又有负，如"新关卡好玩但联机掉线"）
-- ============================================================
SELECT
  game_name AS 游戏,
  COUNT(*) AS 混合情感评论数
FROM topic_sentiment_review
WHERE pos_aspects > 0 AND neg_aspects > 0
GROUP BY game_name
ORDER BY 混合情感评论数 DESC;


-- ============================================================
-- 指标 7：主题共现（哪些主题经常被同时提到）—— 用 lateral view 拆 aspects_json
--   依赖：需自定义 JSON 解析 UDF，或用 get_json_object 逐主题展开
--   这里给出针对单个主题的简洁写法（统计"提到新地图的评论里也提到联机的数量"）
-- ============================================================
SELECT
  COUNT(DISTINCT review_id) AS 同时提到内容+联机的评论数
FROM topic_sentiment_detail a
WHERE a.aspect = 'content'
  AND EXISTS (
    SELECT 1 FROM topic_sentiment_detail b
    WHERE b.review_id = a.review_id AND b.aspect = 'multiplayer'
  );


-- ============================================================
-- 【第二层 · 具体更新条目指标】
-- ============================================================

-- ------------------------------------------------------------
-- 指标 8：具体更新条目情感分布（75 项，按命中数排序）
-- 演示："哪个官方改动被讨论最多、态度如何"
-- ------------------------------------------------------------
SELECT
  d.game            AS 游戏,
  i.item_title      AS 更新条目,
  i.aspect          AS 归属主题,
  COUNT(*)          AS 提及数,
  SUM(CASE WHEN i.sentiment='positive' THEN 1 ELSE 0 END) AS 正向,
  SUM(CASE WHEN i.sentiment='negative' THEN 1 ELSE 0 END) AS 负向,
  ROUND(SUM(CASE WHEN i.sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比,
  ROUND(SUM(CASE WHEN i.sentiment='negative' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 负向占比
FROM item_sentiment_detail i
JOIN item_dict d ON i.item_id = d.item_id
GROUP BY d.game, i.item_title, i.aspect
ORDER BY 提及数 DESC;


-- ------------------------------------------------------------
-- 指标 9：更新后有多少评论谈到某条具体改动（用户核心需求）
--   以姜岛探索(S15_01)为例，结合版本窗口看"更新前 vs 更新后"
--   把 'S15_01' 换成任意 item_id 即可复用
-- ------------------------------------------------------------
SELECT
  CASE
    WHEN window_bucket='pre_14d'    THEN '更新前14天'
    WHEN window_bucket='release_day'THEN '更新当天'
    WHEN window_bucket='post_14d'   THEN '更新后14天'
    ELSE '窗口外'
  END AS 窗口,
  COUNT(*) AS 谈到该条目的评论数,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比,
  ROUND(SUM(CASE WHEN sentiment='negative' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 负向占比
FROM item_sentiment_detail
WHERE item_id = 'S15_01'
GROUP BY window_bucket
ORDER BY window_bucket;


-- ------------------------------------------------------------
-- 指标 10：某版本更新后，各具体条目的讨论热度与情感（联动黄静雯版本窗口）
--   以星露谷 1.5 更新(patch)为例，看窗口内每条改动被谈了多少、态度如何
-- ------------------------------------------------------------
SELECT
  patch_id       AS 版本,
  item_title     AS 更新条目,
  window_bucket  AS 窗口,
  COUNT(*)       AS 提及数,
  ROUND(SUM(CASE WHEN sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 正向占比
FROM item_sentiment_detail
WHERE patch_id <> ''
GROUP BY patch_id, item_title, window_bucket
ORDER BY patch_id, 提及数 DESC;


-- ------------------------------------------------------------
-- 指标 11：两层映射验算 —— 具体条目反馈聚合到通用主题
--   检查"姜岛探索(内容)、八人联机(联机)"等条目层结果，聚合后是否与主题层一致
-- ------------------------------------------------------------
SELECT
  i.aspect AS 归属通用主题,
  COUNT(*) AS 条目层标注数,
  COUNT(DISTINCT i.review_id) AS 涉及评论数,
  ROUND(SUM(CASE WHEN i.sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS 条目层正向占比
FROM item_sentiment_detail i
GROUP BY i.aspect
ORDER BY 条目层标注数 DESC;
