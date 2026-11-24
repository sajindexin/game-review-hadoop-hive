# -*- coding: utf-8 -*-
"""
规则弱标注引擎（Aspect-Based Sentiment, ABSA）

思路：一条评论先按标点/转折词切成子句；每个子句内：
  1) 命中的主题关键词 -> 该子句涉及的方面
  2) 情感词 + 否定词 + 程度词 -> 该子句的极性
  3) 极性就近绑定到本子句命中的主题（解决"新关卡好玩，但联机老掉线"）
评论级：每个主题取各子句里置信度最高的一条作为该方面的最终标签。

输出：aspects_json = [{"aspect","name_zh","sentiment","confidence","evidence"}, ...]
      + 辅助列 primary_sentiment / n_aspects / neg_aspects / pos_aspects
      + items_json（具体更新条目层，按 appid 约束候选集）
"""
from __future__ import annotations
import re
import json
from lexicon import (
    ASPECTS, POSITIVE_WORDS, NEGATIVE_WORDS, NEGATIONS,
    INTENSIFIERS, CONTRAST_WORDS, CLAUSE_SPLIT_CHARS,
)
from item_matcher import match_items, attach_sentiment

# 预编译：主题关键词 -> aspect（长词优先，避免"新地图"被"地图"抢先）
_KW2ASPECT: list[tuple[str, str]] = []
for _aid, _meta in ASPECTS.items():
    for _kw in _meta["keywords"]:
        _KW2ASPECT.append((_kw, _aid))
_KW2ASPECT.sort(key=lambda x: -len(x[0]))

# 情感词 -> 极性
_SENT_WORD2POL: dict[str, int] = {}
for w in POSITIVE_WORDS:
    _SENT_WORD2POL[w] = 1
for w in NEGATIVE_WORDS:
    _SENT_WORD2POL[w] = -1

_SPLIT_RE = re.compile("[" + re.escape(CLAUSE_SPLIT_CHARS) + "]")


def split_clauses(text: str) -> list[str]:
    """按标点和转折词切分子句；转折词保留在子句开头作为信号。"""
    text = (text or "").strip()
    if not text:
        return []
    raw = [c.strip() for c in _SPLIT_RE.split(text) if c.strip()]
    clauses: list[str] = []
    for seg in raw:
        # 在段内按转折词再切，转折词并回后半句
        parts = re.split(r"(但|但是|不过|然而|只是|就是|可惜|美中不足|唯一的问题是)", seg)
        buf = ""
        for p in parts:
            if not p:
                continue
            if p in CONTRAST_WORDS:
                if buf.strip():
                    clauses.append(buf.strip())
                buf = p
            else:
                buf += p
        if buf.strip():
            clauses.append(buf.strip())
    return clauses or [text]


def _scan_aspects(clause: str) -> list[str]:
    """返回该子句命中的主题（去重，保持首次出现顺序）。

    处理关键词嵌套：若短关键词的匹配位置完全落在某个更长关键词的匹配区间内，
    且两者主题不同，则只保留更长（更具体）的关键词，避免"新关卡"同时被
    content 和 level_design 的"关卡"重复认领。
    """
    lower = clause.lower()
    matched: list[tuple[int, int, str]] = []  # (start, end, aspect)
    for kw, aid in _KW2ASPECT:
        kl = kw.lower()
        start = 0
        while True:
            idx = lower.find(kl, start)
            if idx < 0:
                break
            matched.append((idx, idx + len(kl), aid))
            start = idx + len(kl)

    # 长词优先：按跨度降序，被更长区间覆盖的匹配丢弃
    matched.sort(key=lambda x: -(x[1] - x[0]))
    kept: list[tuple[int, int, str]] = []
    for span in matched:
        covered = any(
            other is not span and other[0] <= span[0] and other[1] >= span[1]
            for other in kept
        )
        if not covered:
            kept.append(span)

    hits: list[str] = []
    for _s, _e, aid in kept:
        if aid not in hits:
            hits.append(aid)
    return hits


def _scan_sentiment(clause: str) -> tuple[int, float, str]:
    """返回 (极性 -1/0/1, 置信度, 命中情感词)。"""
    found: list[tuple[int, int, str, bool]] = []  # (pos, pol, word, negated)
    lower = clause.lower()
    for word, pol in _SENT_WORD2POL.items():
        start = 0
        wl = word.lower()
        while True:
            idx = lower.find(wl, start)
            if idx < 0:
                break
            # 检查情感词前 4 字窗口内的否定词
            window = clause[max(0, idx - 4):idx]
            negated = any(n in window for n in NEGATIONS)
            found.append((idx, pol, word, negated))
            start = idx + len(wl)
    if not found:
        return 0, 0.0, ""

    score = 0.0
    hit_words = []
    for _pos, pol, word, negated in found:
        eff = -pol if negated else pol
        conf = 1.0
        # 程度副词增强
        if any(w in clause for w in INTENSIFIERS):
            conf = 1.3
        if negated:
            conf = max(conf, 1.1)
        score += eff * conf
        hit_words.append(("不" if negated else "") + word)

    if score > 0.25:
        return 1, min(1.0, 0.5 + 0.15 * score), "/".join(hit_words[:3])
    if score < -0.25:
        return -1, min(1.0, 0.5 + 0.15 * abs(score)), "/".join(hit_words[:3])
    # 正负抵消
    return 0, 0.3, "/".join(hit_words[:3])


def label_review(text: str, appid=None) -> dict:
    """对一条评论做两层情感标注：通用主题(11) + 具体更新条目(75)。"""
    clauses = split_clauses(text)
    # aspect -> 最佳证据（置信度最高的子句）
    best: dict[str, dict] = {}
    # clause_idx -> 情感结果（供条目层绑定）
    clause_sent: dict[int, dict] = {}

    for ci, clause in enumerate(clauses):
        pol, conf, sent_word = _scan_sentiment(clause)
        clause_sent[ci] = {
            "sentiment": "positive" if pol == 1 else ("negative" if pol == -1 else "neutral"),
            "confidence": conf, "sentiment_word": sent_word,
        }
        aspects = _scan_aspects(clause)
        if not aspects:
            continue
        # 无情感词：中性，低置信度；有转折词且在段首：该子句权重更高
        if pol == 0:
            conf = 0.35 if not sent_word else conf
            sentiment = "neutral"
        else:
            sentiment = "positive" if pol == 1 else "negative"
        # 位于转折后的子句，评价更"明确"，置信度加成
        if clause and clause[0] in CONTRAST_WORDS:
            conf = min(1.0, conf + 0.15)

        for aid in aspects:
            cand = {
                "aspect": aid,
                "name_zh": ASPECTS[aid]["name_zh"],
                "sentiment": sentiment,
                "confidence": round(conf, 3),
                "evidence": clause[:80],
                "sentiment_word": sent_word,
                "clause_idx": ci,
            }
            if aid not in best or cand["confidence"] > best[aid]["confidence"]:
                best[aid] = cand

    aspects_out = sorted(best.values(), key=lambda x: -x["confidence"])
    pos = sum(1 for a in aspects_out if a["sentiment"] == "positive")
    neg = sum(1 for a in aspects_out if a["sentiment"] == "negative")
    if neg > pos:
        primary = "negative"
    elif pos > 0:
        primary = "positive"
    elif aspects_out:
        primary = "neutral"
    else:
        primary = "none"

    # ---- 具体更新条目层（按 appid 约束候选集）----
    items_out: list[dict] = []
    if appid is not None:
        raw_items = match_items(clauses, appid, fallback_sentiment=primary,
                                fallback_conf=0.35)
        items_out = attach_sentiment(raw_items, clause_sent)

    return {
        "aspects": aspects_out,
        "n_aspects": len(aspects_out),
        "pos_aspects": pos,
        "neg_aspects": neg,
        "primary_sentiment": primary,
        "items": items_out,
        "n_items": len(items_out),
    }


if __name__ == "__main__":
    tests = [
        ("新关卡好玩，但联机老掉线", None),
        ("姜岛探索很好玩，但是八人联机老是掉线", 413150),
        ("海浪效果太惊艳了，拖船也很有意思", 252490),
        ("军备竞赛模式好玩，武器挂件也挺有意思", 730),
        ("盾牌重做之后平衡性一坨", 359550),
        ("姜岛探索很好玩", 730),  # 跨游戏：不应命中条目
        ("画面很棒，剧情也不错，就是优化太差老是闪退", None),
    ]
    for t, aid in tests:
        r = label_review(t, appid=aid)
        print("\n原文:", t, "| appid:", aid)
        print("  主情感:", r["primary_sentiment"], "| 主题数:", r["n_aspects"],
              "| 条目数:", r["n_items"])
        for a in r["aspects"]:
            print(f"    [主题] {a['name_zh']}({a['aspect']}): {a['sentiment']} "
                  f"conf={a['confidence']} 证据=<{a['evidence']}>")
        for it in r["items"]:
            print(f"    [条目] {it['title']}({it['item_id']}): {it['sentiment']} "
                  f"conf={it['confidence']} 词=<{it['matched_term']}> 证据=<{it['evidence']}>")
