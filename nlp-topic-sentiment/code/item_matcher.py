# -*- coding: utf-8 -*-
"""
具体更新条目匹配器（两层标签体系的"具体层"）

设计要点：
  1. 条目按 appid 约束：一条评论只在其所属游戏的候选条目里匹配，
     避免"姜岛"被误匹配到其它游戏。
  2. 匹配词 = 官方标题 + 中英文别名（含繁体），长词优先。
  3. 情感就近绑定：条目命中的子句若带情感词，绑定到该子句情感；
     若整个评论有通用主题情感，用其作为回退，避免"姜岛不错"被判中性。
  4. 置信度 = 匹配词具体度 + 情感强度，只保留有明确证据的条目命中。

输出 item_json = [{"item_id","title","title_en","aspect","sentiment",
                    "confidence","evidence","matched_term","clause_idx"}, ...]
"""
from __future__ import annotations

from item_lexicon import ITEMS, APPID_ITEMS, ITEM2ASPECT, candidate_items

# appid -> 预编译的匹配词表 [(term_lower, item_id, term_len), ...]，长词优先
_APPID_TERMS: dict[str, list[tuple[str, str, int]]] = {}
for _appid, _iids in APPID_ITEMS.items():
    terms = []
    for _iid in _iids:
        meta = ITEMS.get(_iid, {})
        for _t in meta.get("match_terms", []):
            tl = _t.lower()
            if len(tl) >= 2:
                terms.append((tl, _iid, len(_t)))
    terms.sort(key=lambda x: -x[2])
    _APPID_TERMS[_appid] = terms


def match_items(clauses: list[str], appid, fallback_sentiment: str = "neutral",
                fallback_conf: float = 0.35) -> list[dict]:
    """在给定游戏的候选条目中匹配评论子句。

    Args:
        clauses: 切分后的子句列表
        appid: 游戏 appid（字符串或整数）
        fallback_sentiment: 子句无情感词时，用评论级通用情感回退
        fallback_conf: 回退情感的基础置信度

    Returns:
        条目命中列表，每个条目取置信度最高的一次命中。
    """
    terms = _APPID_TERMS.get(str(appid), [])
    if not terms or not clauses:
        return []

    # 评论级情感可能是 "none"（无情感证据），条目层统一回退为 neutral
    if fallback_sentiment not in ("positive", "negative", "neutral"):
        fallback_sentiment = "neutral"

    # item_id -> 最佳命中
    best: dict[str, dict] = {}
    for ci, clause in enumerate(clauses):
        lower = clause.lower()
        # 该子句中被更长匹配词覆盖的位置，避免子串重复认领
        matched: list[tuple[int, int, str, str]] = []  # (start, end, item_id, term)
        for tl, iid, tlen in terms:
            start = 0
            while True:
                idx = lower.find(tl, start)
                if idx < 0:
                    break
                matched.append((idx, idx + tlen, iid, tl))
                start = idx + tlen

        if not matched:
            continue
        matched.sort(key=lambda x: -(x[1] - x[0]))
        kept: list[tuple[int, int, str, str]] = []
        for span in matched:
            covered = any(
                o is not span and o[0] <= span[0] and o[1] >= span[1] and o[2] != span[2]
                for o in kept
            )
            if not covered:
                kept.append(span)

        for _s, _e, iid, term in kept:
            meta = ITEMS.get(iid, {})
            # 匹配词越具体（越长），置信度越高；短别名（2字）降权防误匹配
            tconf = 0.55 if len(term) >= 4 else (0.45 if len(term) == 3 else 0.35)
            cand = {
                "item_id": iid,
                "title": meta.get("title", ""),
                "title_en": meta.get("title_en", ""),
                "aspect": ITEM2ASPECT.get(iid, ""),
                "sentiment": fallback_sentiment,
                "confidence": round(tconf, 3),
                "evidence": clause[:80],
                "matched_term": term,
                "clause_idx": ci,
            }
            if iid not in best or cand["confidence"] > best[iid]["confidence"]:
                best[iid] = cand

    return sorted(best.values(), key=lambda x: -x["confidence"])


def attach_sentiment(items: list[dict], clause_sentiments: dict[int, dict]) -> list[dict]:
    """把每个子句的情感结果绑定到命中该子句的条目上。

    clause_sentiments: clause_idx -> {"sentiment": str, "confidence": float,
                                       "sentiment_word": str}
    """
    out = []
    for it in items:
        cs = clause_sentiments.get(it["clause_idx"])
        if cs and cs.get("sentiment") != "neutral":
            it = dict(it)
            it["sentiment"] = cs["sentiment"]
            it["confidence"] = round(min(1.0, it["confidence"] + cs["confidence"] * 0.4), 3)
            it["sentiment_word"] = cs.get("sentiment_word", "")
        out.append(it)
    return out


if __name__ == "__main__":
    tests = [
        ("姜岛探索很好玩，但是八人联机老是掉线", "413150"),
        ("新出的海浪效果太惊艳了，水面过渡很自然", "252490"),
        ("军备竞赛模式好玩，武器挂件也挺有意思", "730"),
        ("姜岛探索很好玩", "730"),  # 跨游戏：不应命中
        ("盾牌重做之后平衡性一坨", "359550"),
    ]
    for text, appid in tests:
        from rule_labeler import split_clauses
        clauses = split_clauses(text)
        print("\n原文:", text, "| appid:", appid)
        print("  子句:", clauses)
        for it in match_items(clauses, appid):
            print(f"    -> {it['title']}({it['item_id']}) "
                  f"aspect={it['aspect']} conf={it['confidence']} "
                  f"term=<{it['matched_term']}> 证据=<{it['evidence']}>")
