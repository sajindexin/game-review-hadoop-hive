"""中文/英文混合分词：不依赖 jieba，CJK 采用 unigram + bigram。

Steam 评测里中英文混排很常见，检索侧只需要稳定的词项：
- ASCII 字母/数字按连续串切词；
- CJK 连续串切成单字与相邻双字（双字基本等价于常用词，且无需词典）。
"""
from __future__ import annotations

import re
import unicodedata

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9_']*")

# 单字停用词只影响 unigram，双字仍保留
STOP_CHARS = set("的了是在我你他她它和与也就都很不没有这那个一上下来去说要会着过还但而及或为以于对从被把让给")
STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "this", "that",
    "for", "on", "with", "as", "at", "be", "are", "was", "were", "but", "not",
    "game", "games",
}

# 疑问/功能类双字词：不承担“实体”作用，不能作为证据门槛
STOP_BIGRAMS = {
    "为什", "什么", "怎么", "么样", "怎样", "如何", "多少", "哪些", "哪个", "可以", "是否",
    "有没", "没有", "玩家", "评价", "评论", "游戏", "更新", "版本", "内容", "现在", "已经",
    "知道", "觉得", "值得", "不值", "值不", "好用", "不好", "推荐", "下降", "上升", "变化",
    "情况", "问题", "方面", "表现", "反馈", "体验", "感觉", "认为", "说说", "介绍", "一下",
    "这个", "那个", "这些", "那些", "我们", "他们", "你们", "自己", "就是", "还是", "但是",
    "因为", "所以", "如果", "虽然", "而且", "然后", "之后", "之前", "时候", "东西", "地方",
}

# 过于宽泛、不能单独定位改动的词
GENERIC_CJK = {
    "地图", "武器", "任务", "装备", "角色", "事件", "建筑", "物品", "界面", "显示", "时间",
    "数据", "主题", "系统", "模式", "功能", "支持", "加入", "调整", "修复", "优化", "增加",
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str) -> list[str]:
    text = normalize(text)
    tokens: list[str] = []
    for m in ASCII_WORD.finditer(text):
        w = m.group()
        if len(w) > 1 and w not in STOP_WORDS:
            tokens.append(w)
    for m in CJK_RUN.finditer(text):
        run = m.group()
        for ch in run:
            if ch not in STOP_CHARS:
                tokens.append(ch)
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
    return tokens


def normalize_for_match(text: str) -> str:
    """用于别名词面匹配：去掉空白、统一小写。"""
    return re.sub(r"\s+", "", normalize(text))
