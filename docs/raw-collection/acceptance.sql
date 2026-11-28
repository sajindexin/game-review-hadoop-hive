-- Raw collection acceptance queries; compare with manifest_verified.json
SHOW TABLES IN game_review_raw;
SELECT 'raw_reviews' AS table_name, COUNT(*) AS actual_rows, 4900000 AS expected_rows FROM game_review_raw.raw_reviews;
SELECT 'raw_announcements' AS table_name, COUNT(*) AS actual_rows, 4934 AS expected_rows FROM game_review_raw.raw_announcements;
SELECT 'game_counts' AS table_name, COUNT(*) AS actual_rows, 12 AS expected_rows FROM game_review_raw.game_counts;
SELECT 'collection_batches' AS table_name, COUNT(*) AS actual_rows, 33967 AS expected_rows FROM game_review_raw.collection_batches;
SELECT appid, COUNT(*) AS raw_review_rows FROM game_review_raw.raw_reviews GROUP BY appid ORDER BY appid;
SELECT appid, COUNT(*) AS announcement_rows FROM game_review_raw.raw_announcements GROUP BY appid ORDER BY appid;
SELECT SUM(raw_review_rows) AS reviews_from_counts, SUM(announcement_rows) AS announcements_from_counts FROM game_review_raw.game_counts;
