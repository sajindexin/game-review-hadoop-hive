"""按版本 UTC 窗口聚合中英文主题情感规则标注。"""
from __future__ import annotations

import csv
import gzip
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

FILES = {
    "chinese": "topic_sentiment_detail.csv.gz",
    "en": "topic_sentiment_detail_en.csv.gz",
}
SENTIMENTS = ("positive", "negative", "neutral")


def build_topic_stats(patches: list[dict], results: Path) -> dict:
    windows = defaultdict(list)
    for patch in patches:
        release = date.fromisoformat(patch["release_date"])
        start = date.fromisoformat(patch["window_start"])
        end = date.fromisoformat(patch["window_end"])
        for offset in range((end - start).days + 1):
            day = start + timedelta(days=offset)
            phase = "before" if day < release else "after" if day > release else "release"
            windows[(str(patch["appid"]), day.isoformat())].append((patch["patch_id"], phase))

    counts = Counter()
    names = {}
    sources = {}
    for language, filename in FILES.items():
        path = results / filename
        if not path.exists():
            continue
        sources[language] = filename
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                matches = windows.get((row["appid"], row["created_date"]))
                if not matches:
                    continue
                aspect = row["aspect"]
                names[aspect] = row["name_zh"]
                sentiment = row["sentiment"]
                if sentiment not in SENTIMENTS:
                    raise ValueError(f"未知情感标签：{sentiment}")
                for patch_id, phase in matches:
                    counts[patch_id, language, phase, aspect, sentiment] += 1

    rows = [
        {"patch_id": patch, "language": lang, "phase": phase,
         "aspect": aspect, "name": names[aspect], "sentiment": sentiment, "count": count}
        for (patch, lang, phase, aspect, sentiment), count in sorted(counts.items())
    ]
    return {"method": "rule", "sources": sources, "rows": rows}
