# -*- coding: utf-8 -*-
"""最终交付校验：结果表完整性 + 关键统计"""
import os
import pyarrow.csv as pacsv
import pyarrow.compute as pc

RESULT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "result"))

detail = pacsv.read_csv(os.path.join(RESULT, "topic_sentiment_detail.csv"))
review = pacsv.read_csv(os.path.join(RESULT, "topic_sentiment_review_summary.csv"))
dist = pacsv.read_csv(os.path.join(RESULT, "topic_distribution.csv"))

print("=== 结果表行数 ===")
print(f"  主题情感明细: {detail.num_rows:,}")
print(f"  评论级汇总:   {review.num_rows:,}")
print(f"  主题分布:     {dist.num_rows:,}")

print("\n=== 明细表字段 ===")
print("  ", detail.column_names)

# 验证核心示例：content+positive 和 multiplayer+negative 确实存在
d = detail.to_pydict()
content_pos = sum(1 for a, s in zip(d["aspect"], d["sentiment"]) if a == "content" and s == "positive")
multi_neg = sum(1 for a, s in zip(d["aspect"], d["sentiment"]) if a == "multiplayer" and s == "negative")
print(f"\n=== 关键统计 ===")
print(f"  内容/玩法-正向: {content_pos:,}")
print(f"  联机/服务器-负向: {multi_neg:,}")

# 混合情感评论数
r = review.to_pydict()
mixed = sum(1 for p, n in zip(r["pos_aspects"], r["neg_aspects"]) if p and n and p > 0 and n > 0)
print(f"  混合情感评论(同时有正有负主题): {mixed:,}")

# 有版本号的评论数
with_ver = sum(1 for v in r["game_version"] if v)
print(f"  带版本号的中文评论: {with_ver:,}")

# 各游戏中文评论数
from collections import Counter
gc = Counter(r["game_name"])
print(f"\n=== 各游戏中文评论数 ===")
for g, c in gc.most_common():
    print(f"  {g}: {c:,}")

print("\nPASS: 所有结果表可正常读取")
