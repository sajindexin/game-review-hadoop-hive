"""NLP 查询接口，由何国境接入业务查询。"""

from .common import pending


def get_topics() -> dict:
    """返回与 data/nlp/topics.js 中 SRW_NLP_TOPICS 相同结构的 JSON。"""
    pending("NLP 主题")


def get_items() -> dict:
    """返回与 data/nlp/items.js 中 SRW_NLP_ITEMS 相同结构的 JSON。"""
    pending("NLP 条目")
