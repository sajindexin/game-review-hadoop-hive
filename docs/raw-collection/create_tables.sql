-- Validated raw collection delivery, version v1_490

CREATE DATABASE IF NOT EXISTS game_review_raw;

CREATE EXTERNAL TABLE IF NOT EXISTS game_review_raw.`raw_reviews` (
  `appid` BIGINT,
  `item_id` STRING,
  `source` STRING,
  `task_id` STRING,
  `fetched_at` STRING,
  `schema_version` BIGINT,
  `content_hash` STRING,
  `source_file` STRING,
  `source_member` STRING,
  `source_row` BIGINT,
  `review_id` STRING,
  `language` STRING,
  `review` STRING,
  `voted_up` BOOLEAN,
  `timestamp_created` BIGINT,
  `timestamp_updated` BIGINT,
  `author_steamid` STRING,
  `playtime_at_review` BIGINT,
  `playtime_forever` BIGINT,
  `votes_up` BIGINT,
  `votes_funny` BIGINT,
  `comment_count` BIGINT,
  `weighted_vote_score` DOUBLE,
  `steam_purchase` BOOLEAN,
  `received_for_free` BOOLEAN,
  `refunded` BOOLEAN,
  `written_during_early_access` BOOLEAN,
  `primarily_steam_deck` BOOLEAN,
  `segment_id` STRING,
  `segment_label` STRING,
  `raw_json` STRING
) STORED AS PARQUET LOCATION 'hdfs://namenode:8020/datasets/game_review_raw/v1_490/raw_reviews';

CREATE EXTERNAL TABLE IF NOT EXISTS game_review_raw.`raw_announcements` (
  `appid` BIGINT,
  `item_id` STRING,
  `source` STRING,
  `task_id` STRING,
  `fetched_at` STRING,
  `schema_version` BIGINT,
  `content_hash` STRING,
  `source_file` STRING,
  `source_member` STRING,
  `source_row` BIGINT,
  `announcement_id` STRING,
  `title` STRING,
  `contents` STRING,
  `date` BIGINT,
  `author` STRING,
  `url` STRING,
  `feed_type` BIGINT,
  `feedname` STRING,
  `feedlabel` STRING,
  `is_external_url` BOOLEAN,
  `raw_json` STRING
) STORED AS PARQUET LOCATION 'hdfs://namenode:8020/datasets/game_review_raw/v1_490/raw_announcements';

CREATE EXTERNAL TABLE IF NOT EXISTS game_review_raw.`game_counts` (
  `appid` BIGINT,
  `game_name` STRING,
  `raw_review_rows` BIGINT,
  `historical_v1_sample_rows` BIGINT,
  `raw_review_duplicate_ids` BIGINT,
  `announcement_rows` BIGINT,
  `announcement_duplicate_ids` BIGINT,
  `review_source_status` STRING,
  `announcement_origin` STRING,
  `appid_filled_rows` BIGINT,
  `v1_cleaned_rows` BIGINT,
  `reconstructed_invalid_rows` BIGINT
) STORED AS PARQUET LOCATION 'hdfs://namenode:8020/datasets/game_review_raw/v1_490/game_counts';

CREATE EXTERNAL TABLE IF NOT EXISTS game_review_raw.`collection_batches` (
  `data_type` STRING,
  `appid` BIGINT,
  `source_file` STRING,
  `source_member` STRING,
  `task_id` STRING,
  `rows` BIGINT,
  `fetched_at_min` STRING,
  `fetched_at_max` STRING,
  `event_time_min_utc` STRING,
  `event_time_max_utc` STRING,
  `language_counts` STRING,
  `query` STRING,
  `batch_basis` STRING,
  `segment_id` STRING,
  `segment_label` STRING,
  `appid_filled_rows` BIGINT
) STORED AS PARQUET LOCATION 'hdfs://namenode:8020/datasets/game_review_raw/v1_490/collection_batches';
