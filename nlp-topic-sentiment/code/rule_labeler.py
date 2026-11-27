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
from lexicon_en import (
    ASPECT_KEYWORDS_EN, POSITIVE_WORDS_EN, NEGATIVE_WORDS_EN, NEGATIONS_EN,
    INTENSIFIERS_EN, CONTRAST_WORDS_EN, CONTRAST_SPLIT_RE_EN,
)
from item_matcher import match_items, attach_sentiment

# 预编译：主题关键词 -> aspect（长词优先，避免"新地图"被"地图"抢先）
_KW2ASPECT: list[tuple[str, str]] = []
for _aid, _meta in ASPECTS.items():
    for _kw in _meta["keywords"]:
        _KW2ASPECT.append((_kw, _aid))
_KW2ASPECT.sort(key=lambda x: -len(x[0]))

# 英文：每个主题一条 alternation 正则（长词在前），词边界匹配
_EN_ASPECT_RES: list[tuple[re.Pattern, str]] = []
for _aid, _kws in ASPECT_KEYWORDS_EN.items():
    _alts = "|".join(re.escape(k) for k in sorted(set(_kws), key=lambda x: -len(x)))
    _EN_ASPECT_RES.append((re.compile(r"\b(?:" + _alts + r")\b", re.I), _aid))

# 情感词 -> 极性
_SENT_WORD2POL: dict[str, int] = {}
for w in POSITIVE_WORDS:
    _SENT_WORD2POL[w] = 1
for w in NEGATIVE_WORDS:
    _SENT_WORD2POL[w] = -1

# 英文情感：合并 alternation（长词在前，保证 "waste of time" 优先于 "waste"）
def _en_alt(words: list[str]) -> re.Pattern:
    alts = "|".join(re.escape(w) for w in sorted(set(words), key=lambda x: -len(x)))
    return re.compile(r"\b(?:" + alts + r")\b", re.I)

_EN_POS_RE = _en_alt(POSITIVE_WORDS_EN)
_EN_NEG_RE = _en_alt(NEGATIVE_WORDS_EN)
# 英文否定检测正则（用于情感词前窗口翻转）
_EN_NEGATION_RE = _en_alt(NEGATIONS_EN)
_EN_INTENS_RE = _en_alt(INTENSIFIERS_EN)

# 否定/程度词检测窗口：中文按字符（4 字），英文按字符（25 字符，覆盖
# "not very good" / "isn't really all that good" 这类间隔）
_NEG_WINDOW = {"zh": 4, "en": 25}

_SPLIT_RE = re.compile("[" + re.escape(CLAUSE_SPLIT_CHARS) + "]")
# 英文句尾句号：后跟空白或串尾的 "." 视为分句符（替换为换行再走统一分句），
# 不直接把 "." 加进 CLAUSE_SPLIT_CHARS，避免劈开 "9.5" / 版本号这类小数点。
_EN_PERIOD_RE = re.compile(r"\.(?=\s|$)")
# 转折切分：中文关键词 + 英文词边界；必须 re.I，否则 "BUT"/"However"
# 这类大写开头的转折词不会命中，整句情感词正负抵消成中性。
_CONTRAST_SPLIT_RE = re.compile(
    r"(但|但是|不过|然而|只是|就是|可惜|美中不足|唯一的问题是|" + CONTRAST_SPLIT_RE_EN + r")",
    re.I)
_CONTRAST_SET = set(CONTRAST_WORDS) | {w.lower() for w in CONTRAST_WORDS_EN}
# 子句起始是否为转折词：英文用词边界（避免 "button..." 误配 "but"），中文直接匹配
_CONTRAST_START_RE = re.compile(
    r"^(?:" + CONTRAST_SPLIT_RE_EN + r"|" +
    "|".join(re.escape(w) for w in sorted(CONTRAST_WORDS, key=lambda x: -len(x))) +
    r")", re.I)


def detect_language(text: str) -> str:
    """简单语言判定：ASCII 字母占比高 -> 英文，否则中文。"""
    text = (text or "").strip()
    if not text:
        return "zh"
    ascii_alpha = sum(1 for c in text if c.isascii() and c.isalpha())
    if ascii_alpha >= 8 and ascii_alpha >= 0.5 * len(text.replace(" ", "")):
        return "en"
    return "zh"


def split_clauses(text: str) -> list[str]:
    """按标点和转折词切分子句；转折词保留在子句开头作为信号。"""
    text = (text or "").strip()
    if not text:
        return []
    # 英文句尾句号先归一成分句符（句号后必须紧跟空白/串尾，小数点不受影响）
    text = _EN_PERIOD_RE.sub("\n", text)
    raw = [c.strip() for c in _SPLIT_RE.split(text) if c.strip()]
    clauses: list[str] = []
    for seg in raw:
        # 在段内按转折词再切（中英通用，英文按词边界），转折词并回后半句
        parts = _CONTRAST_SPLIT_RE.split(seg)
        buf = ""
        for p in parts:
            if not p:
                continue
            if p.lower() in _CONTRAST_SET:
                if buf.strip():
                    clauses.append(buf.strip())
                buf = p
            else:
                buf += p
        if buf.strip():
            clauses.append(buf.strip())
    return clauses or [text]


def _scan_aspects(clause: str, lang: str = "zh") -> list[str]:
    """返回该子句命中的主题（去重，保持首次出现顺序）。

    中文：关键词子串匹配（长词优先，被更长区间覆盖的短词丢弃，
    避免"新关卡"同时被 content 和 level_design 的"关卡"重复认领）。
    英文：每主题一条词边界正则，同样按跨度去重。
    """
    matched: list[tuple[int, int, str]] = []  # (start, end, aspect)
    if lang == "en":
        for pat, aid in _EN_ASPECT_RES:
            for m in pat.finditer(clause):
                matched.append((m.start(), m.end(), aid))
    else:
        lower = clause.lower()
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


def _scan_sentiment(clause: str, lang: str = "zh") -> tuple[int, float, str]:
    """返回 (极性 -1/0/1, 置信度, 命中情感词)。"""
    found: list[tuple[int, int, str, bool]] = []  # (pos, pol, word, negated)
    win = _NEG_WINDOW[lang]
    if lang == "en":
        for pat, pol in ((_EN_POS_RE, 1), (_EN_NEG_RE, -1)):
            for m in pat.finditer(clause):
                window = clause[max(0, m.start() - win):m.start()]
                negated = bool(_EN_NEGATION_RE.search(window))
                found.append((m.start(), pol, m.group(0).lower(), negated))
        if not found:
            return 0, 0.0, ""
        found.sort(key=lambda x: x[0])
    else:
        lower = clause.lower()
        for word, pol in _SENT_WORD2POL.items():
            start = 0
            wl = word.lower()
            while True:
                idx = lower.find(wl, start)
                if idx < 0:
                    break
                # 检查情感词前窗口内的否定词
                window = clause[max(0, idx - win):idx]
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
        if lang == "en":
            if _EN_INTENS_RE.search(clause):
                conf = 1.3
        elif any(w in clause for w in INTENSIFIERS):
            conf = 1.3
        if negated:
            conf = max(conf, 1.1)
        score += eff * conf
        hit_words.append(("not " if negated else "") + word if lang == "en"
                         else ("不" if negated else "") + word)

    if score > 0.25:
        return 1, min(1.0, 0.5 + 0.15 * score), "/".join(hit_words[:3])
    if score < -0.25:
        return -1, min(1.0, 0.5 + 0.15 * abs(score)), "/".join(hit_words[:3])
    # 正负抵消
    return 0, 0.3, "/".join(hit_words[:3])


def label_review(text: str, appid=None, lang: str | None = None) -> dict:
    """对一条评论做两层情感标注：通用主题(11) + 具体更新条目(75)。

    lang: "zh" / "en"；None 时自动检测（ASCII 字母占比高按英文处理）。
    """
    if lang is None:
        lang = detect_language(text)
    clauses = split_clauses(text)
    # aspect -> 最佳证据（置信度最高的子句）
    best: dict[str, dict] = {}
    # clause_idx -> 情感结果（供条目层绑定）
    clause_sent: dict[int, dict] = {}

    for ci, clause in enumerate(clauses):
        pol, conf, sent_word = _scan_sentiment(clause, lang)
        clause_sent[ci] = {
            "sentiment": "positive" if pol == 1 else ("negative" if pol == -1 else "neutral"),
            "confidence": conf, "sentiment_word": sent_word,
        }
        aspects = _scan_aspects(clause, lang)
        if not aspects:
            continue
        # 无情感词：中性，低置信度；有转折词且在段首：该子句权重更高
        if pol == 0:
            conf = 0.35 if not sent_word else conf
            sentiment = "neutral"
        else:
            sentiment = "positive" if pol == 1 else "negative"
        # 位于转折后的子句，评价更"明确"，置信度加成（中英转折词都认）
        if clause and _CONTRAST_START_RE.match(clause):
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
        ("新关卡好玩，但联机老掉线", None, None),
        ("姜岛探索很好玩，但是八人联机老是掉线", 413150, None),
        ("海浪效果太惊艳了，拖船也很有意思", 252490, None),
        ("军备竞赛模式好玩，武器挂件也挺有意思", 730, None),
        ("盾牌重做之后平衡性一坨", 359550, None),
        ("姜岛探索很好玩", 730, None),  # 跨游戏：不应命中条目
        ("画面很棒，剧情也不错，就是优化太差老是闪退", None, None),
        # ---- 英文 ----
        ("Great new maps, but the servers keep disconnecting", None, "en"),
        ("The combat feels amazing, however performance is a stuttering mess on my rig", None, "en"),
        ("Story was boring and the ending ruined it for me", None, "en"),
        ("Not worth the price, cash grab DLC with no real content", None, "en"),
        ("Beautiful art style and incredible soundtrack", None, "en"),
        ("Runs great after the patch, devs actually listen to the community", None, "en"),
        ("The buttons are mushy and input lag is awful", None, "en"),  # "but"不误配 button
    ]
    for t, aid, lang in tests:
        r = label_review(t, appid=aid, lang=lang)
        print("\n原文:", t, "| appid:", aid)
        print("  主情感:", r["primary_sentiment"], "| 主题数:", r["n_aspects"],
              "| 条目数:", r["n_items"])
        for a in r["aspects"]:
            print(f"    [主题] {a['name_zh']}({a['aspect']}): {a['sentiment']} "
                  f"conf={a['confidence']} 证据=<{a['evidence']}>")
        for it in r["items"]:
            print(f"    [条目] {it['title']}({it['item_id']}): {it['sentiment']} "
                  f"conf={it['confidence']} 词=<{it['matched_term']}> 证据=<{it['evidence']}>")
