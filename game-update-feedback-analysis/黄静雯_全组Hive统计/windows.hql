CREATE DATABASE IF NOT EXISTS hjw_group_windows_v1;
USE hjw_group_windows_v1;
CREATE EXTERNAL TABLE IF NOT EXISTS reviews_v1 (
  appid STRING, review_id STRING, created_date STRING, updated_ts BIGINT,
  language_std STRING, voted_up BOOLEAN
) STORED AS PARQUET LOCATION '${hivevar:stage_uri}/reviews';
CREATE EXTERNAL TABLE IF NOT EXISTS patch_calendar (
  appid STRING, patch_id STRING, utc_day STRING, day_offset INT, window_end_excl BIGINT
) ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t' LOCATION '${hivevar:stage_uri}/calendar';
CREATE EXTERNAL TABLE IF NOT EXISTS app_languages (
  appid STRING, lang_group STRING
) ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t' LOCATION '${hivevar:stage_uri}/languages';

CREATE TABLE IF NOT EXISTS patch_day_atomic (
  appid STRING, patch_id STRING, utc_day STRING, lang STRING,
  reviews BIGINT, recommended BIGINT, late_edits BIGINT
) STORED AS ORC;
INSERT OVERWRITE TABLE patch_day_atomic
SELECT c.appid,c.patch_id,c.utc_day,r.language_std,COUNT(*),
       SUM(IF(r.voted_up,1,0)),SUM(IF(r.updated_ts>=c.window_end_excl,1,0))
FROM reviews_v1 r JOIN patch_calendar c ON r.appid=c.appid AND r.created_date=c.utc_day
GROUP BY c.appid,c.patch_id,c.utc_day,r.language_std;

CREATE TABLE IF NOT EXISTS dws_patch_day (
  appid STRING, patch_id STRING, utc_day STRING, day_offset INT, lang_group STRING,
  reviews BIGINT, recommended BIGINT, late_edits BIGINT
) STORED AS ORC;
INSERT OVERWRITE TABLE dws_patch_day
SELECT c.appid,c.patch_id,c.utc_day,c.day_offset,l.lang_group,
       COALESCE(g.n,0),COALESCE(g.y,0),COALESCE(g.e,0)
FROM patch_calendar c JOIN app_languages l ON c.appid=l.appid
LEFT JOIN (
  SELECT a.patch_id,a.utc_day,grp,SUM(a.reviews) n,SUM(a.recommended) y,SUM(a.late_edits) e
  FROM patch_day_atomic a
  LATERAL VIEW explode(CASE WHEN a.lang IN ('zh-Hans','zh-Hant')
                           THEN array('all','chinese',a.lang) ELSE array('all',a.lang) END) t AS grp
  GROUP BY a.patch_id,a.utc_day,grp
) g ON c.patch_id=g.patch_id AND c.utc_day=g.utc_day AND l.lang_group=g.grp;

CREATE VIEW IF NOT EXISTS ads_patch_window AS
SELECT appid,patch_id,lang_group,period,COUNT(*) AS window_days,
       SUM(IF(reviews>0,1,0)) AS covered_days,SUM(reviews) AS reviews,
       SUM(recommended) AS recommended,
       CASE WHEN SUM(reviews)>0 THEN CAST(SUM(recommended) AS DOUBLE)/SUM(reviews) END AS recommendation_rate,
       SUM(late_edits) AS late_edits
FROM (
 SELECT d.*, CASE WHEN day_offset<0 THEN 'before' WHEN day_offset=0 THEN 'release' ELSE 'after' END AS period
 FROM dws_patch_day d
) days_with_period
GROUP BY appid,patch_id,lang_group,period;
SELECT 'DAY_ROW',appid,patch_id,utc_day,day_offset,lang_group,reviews,recommended,late_edits FROM dws_patch_day;
SELECT 'WINDOW_ROW',appid,patch_id,lang_group,period,window_days,covered_days,reviews,recommended,recommendation_rate,late_edits FROM ads_patch_window;
