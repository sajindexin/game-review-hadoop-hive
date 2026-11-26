"""意图路由：优先让 LLM 输出结构化 JSON，失败或未配置时退回确定性规则。

LLM 只做“路由”（意图 / 阶段 / 条目 / 排行口径），不生成任何数字或原文；
数字与引文仍然只能来自指标表和已复核引文库。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import httpx

INTENTS = {"metric", "announcement", "review", "why", "mixed", "ranking", "outside", "need_context"}
PHASES = {"before", "release", "after"}

OUTSIDE_RE = re.compile(
    r"天气|下雨|带伞|气温|台风|公交|地铁|机票|航班|打车|退款|退货|支付|付款|打折|折扣|"
    r"密码|账号|登录|注册|客服|封号申诉|显卡驱动|装系统|重装|C盘|磁盘|内存条|硬件故障|"
    r"医院|生病|健康|体检|作业|考试|论文|选课|股票|基金|彩票|汇率"
)
METRIC_RE = re.compile(r"推荐率|好评率|差评率|百分之|百分点|样本|多少条|条数|占比|比例|变化|降了|升了|下降|上升|跌|涨")
REVIEW_RE = re.compile(r"玩家|评价|评论|反馈|吐槽|抱怨|差评|好评|怎么说|原话|骂|夸|觉得")
ANNOUNCE_RE = re.compile(r"公告|改了什么|改了啥|改动|更新内容|版本内容|更新日志|补丁说明|加了什么|加了啥|有什么新|新内容|什么新|哪些内容")
WHY_RE = re.compile(r"为什么|为啥|原因|怎么回事|导致")
RANK_SCOPE_RE = re.compile(r"哪个版本|哪些版本|所有版本|全组|排名|排行|对比一下版本|哪次更新|哪个更新")
RANK_EXTREME_RE = re.compile(r"最高|最低|最好|最差|最大|最小|最多|最少")
PAGE_VERSION_RE = re.compile(r"这个版本|该版本|当前版本|本版本")
COMPARE_RE = re.compile(r"更新前后|发布前后|上线前后|前后对比|前后变化|前后差异")
RELEASE_RE = re.compile(r"发布当天|上线当天|发布日(?:当天)?|更新当天")
BEFORE_RE = re.compile(r"更新前|发布前|上线前|版本前|之前")
AFTER_RE = re.compile(r"更新后|发布后|上线后|版本后|之后")


@dataclass
class Route:
    intent: str = "review"
    phases: list[str] = field(default_factory=list)
    item_ids: list[str] = field(default_factory=list)
    language: str | None = None
    ranking_phase: str | None = None
    ranking_direction: str | None = None
    confidence: float = 0.0
    source: str = "rules"
    reason: str = ""


class Router:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.base_url = (os.environ.get("GRA_LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = os.environ.get("GRA_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
        self.model = os.environ.get("GRA_LLM_MODEL") or "deepseek-chat"
        self._client = client

    @property
    def available(self) -> bool:
        return bool(self.api_key) or self._client is not None

    # ---- 对外入口 -----------------------------------------------------
    def route(self, question: str, patch: dict | None, items: list[dict] | None = None,
              language: str | None = None) -> Route:
        if self.available:
            try:
                return self._route_llm(question, patch, items or [], language)
            except Exception as exc:  # noqa: BLE001 - 路由失败必须降级，不能影响问答
                fallback = self._route_rules(question, patch, items or [], language)
                fallback.source = "rules_fallback"
                fallback.reason = f"LLM 路由失败：{type(exc).__name__}: {exc}"[:200]
                return fallback
        return self._route_rules(question, patch, items or [], language)

    # ---- LLM ---------------------------------------------------------
    def _route_llm(self, question: str, patch: dict | None, items: list[dict],
                   language: str | None) -> Route:
        catalog = "\n".join(
            f"- {it['item_id']} | {it['title']} | {' / '.join((it.get('aliases_zh') or [])[:3])}"
            for it in items
        ) or "（当前没有版本目录，条目留空）"
        patch_line = (f"当前版本：{patch['patch_name']}（{patch.get('release_date', '')}，patch_id={patch['patch_id']}）"
                      if patch else "当前没有预选版本")
        system = (
            "你是游戏版本评测问答系统的路由器。只输出 JSON，不要输出解释文字。\n"
            "任务：把用户问题解析成结构化路由，供下游查询指标、公告或已复核引文。\n"
            "字段：\n"
            '  intent: "metric" | "announcement" | "review" | "why" | "mixed" | "ranking" | "outside" | "need_context"\n'
            '  phases: 数组，元素只能是 "before" / "release" / "after"；不限制就给 []；\n'
            '          "更新前后" 必须给 ["before","after"]，不能只给 before；\n'
            '  item_ids: 数组，只能从下面条目目录里选；没有明确改动就给 []；\n'
            '  language: "all" | "chinese" | "en" | null；\n'
            '  ranking: 仅 intent=ranking 时给 {"metric_phase":"before|release|after","direction":"top|low|rise|drop"}，否则 null；\n'
            "  confidence: 0 到 1 的小数。\n"
            "判断规则：\n"
            "1. 只有明确跨版本比较（哪个版本/全组/排名/所有版本，或“最高/最低 + 版本”）才是 ranking；\n"
            "   “整体推荐率”“全部语言”是语言分组，不是排行。\n"
            "2. “更新前后”是两段证据，phases 必须同时包含 before 和 after。\n"
            "3. 指标问题用 metric；官方改动用 announcement；玩家原话/评价用 review；\n"
            "   “怎么看/怎么用/好玩吗/值得吗/体验如何”问的是玩家反馈，用 review，不是 announcement；\n"
            "   问“为什么差评多”用 why；指标+原话混合用 mixed。\n"
            "4. 天气、退款、客服、硬件、健康、考试等超出数据范围，用 outside；\n"
            "   游戏内作弊/外挂/封号讨论仍属于玩家评价，用 review，不要判 outside。\n"
            "5. 不要编造 item_id；不确定就留空数组。\n"
            "输出示例：{\"intent\":\"review\",\"phases\":[\"before\",\"after\"],"
            "\"item_ids\":[\"S15_01\"],\"language\":\"chinese\",\"ranking\":null,\"confidence\":0.9}"
        )
        user = f"{patch_line}\n可选条目：\n{catalog}\n\n用户问题：{question}"
        if language:
            user += f"\n调用方已指定 language={language}"
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self._client is not None:
            resp = self._client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        else:
            with httpx.Client(timeout=8.0, trust_env=False) as client:
                resp = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(self._strip_fences(content))
        return self._validate(data, items, language, source="llm", question=question)

    @staticmethod
    def _strip_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return text

    def _validate(self, data: dict, items: list[dict], language: str | None,
                  source: str, question: str = "") -> Route:
        intent = str(data.get("intent", "")).strip()
        if intent not in INTENTS:
            raise ValueError(f"invalid intent: {intent}")
        phases = [p for p in (data.get("phases") or []) if p in PHASES]
        q = question or ""
        has_before = bool(BEFORE_RE.search(q))
        has_after = bool(AFTER_RE.search(q))
        has_release = bool(RELEASE_RE.search(q))
        # 问题里同时出现“更新前”和“更新后”时，模型只给一个阶段也要纠正
        if has_before and has_after:
            phases = ["before", "after"]
        # “更新差评”这类问法模型偶尔只给 release；没有明确“发布当天/发布日”就丢掉
        if phases == ["release"] and not has_release:
            phases = []
        allowed_items = {it["item_id"] for it in items}
        item_ids = [i for i in (data.get("item_ids") or []) if i in allowed_items]
        lang = data.get("language")
        if lang not in ("all", "chinese", "en"):
            lang = language if language in ("all", "chinese", "en") else None
        ranking = data.get("ranking") or {}
        rank_phase = ranking.get("metric_phase") if ranking.get("metric_phase") in PHASES else None
        rank_dir = ranking.get("direction") if ranking.get("direction") in ("top", "low", "rise", "drop") else None
        if intent == "ranking":
            if has_release:
                rank_phase = "release"
            elif has_before and not has_after:
                rank_phase = "before"
            elif has_after and not has_before:
                rank_phase = "after"
            rank_phase = rank_phase or (phases[0] if len(phases) == 1 else "after")
            rank_dir = rank_dir or "drop"
        else:
            rank_phase = rank_dir = None
        return Route(intent=intent, phases=list(dict.fromkeys(phases)), item_ids=item_ids,
                     language=lang, ranking_phase=rank_phase, ranking_direction=rank_dir,
                     confidence=float(data.get("confidence") or 0.0), source=source,
                     reason=str(data.get("reason", ""))[:200])

    # ---- 规则兜底 -----------------------------------------------------
    def _route_rules(self, question: str, patch: dict | None, items: list[dict],
                     language: str | None) -> Route:
        q = question
        if OUTSIDE_RE.search(q):
            return Route(intent="outside", source="rules", confidence=0.9)

        phases: list[str] = []
        has_before = bool(BEFORE_RE.search(q))
        has_after = bool(AFTER_RE.search(q))
        if COMPARE_RE.search(q) or (has_before and has_after):
            phases = ["before", "after"]
        elif RELEASE_RE.search(q):
            phases = ["release"]
        elif has_before:
            phases = ["before"]
        elif has_after:
            phases = ["after"]

        metric = bool(METRIC_RE.search(q)) or "推荐率" in q or "好评率" in q
        review = bool(REVIEW_RE.search(q))
        announce = bool(ANNOUNCE_RE.search(q))
        ask_why = bool(WHY_RE.search(q))
        page_context = bool(PAGE_VERSION_RE.search(q)) and not re.search(r"哪个|哪些|所有|全组|排名|排行", q)
        wants_rank = bool(RANK_SCOPE_RE.search(q)) or (bool(RANK_EXTREME_RE.search(q)) and "版本" in q)

        if wants_rank and metric and not page_context:
            if phases:
                rank_phase = phases[0] if len(phases) == 1 else "after"
            else:
                rank_phase = "after"
            if re.search(r"最高|最好|最强", q):
                direction = "top"
            elif re.search(r"最低|最差|最弱", q):
                direction = "low"
            elif re.search(r"上升|涨|升得|提高|增长", q):
                direction = "rise"
            else:
                direction = "drop"
            return Route(intent="ranking", phases=phases, ranking_phase=rank_phase,
                         ranking_direction=direction, source="rules", confidence=0.6)

        if ask_why:
            intent = "why"
        elif metric and (review or announce):
            intent = "mixed"
        elif metric:
            intent = "metric"
        elif announce:
            intent = "announcement"
        elif review:
            intent = "review"
        else:
            intent = "review"

        lang = language if language in ("all", "chinese", "en") else None
        if lang is None:
            if re.search(r"中文|汉语|简中|繁中", q):
                lang = "chinese"
            elif re.search(r"英文|英语", q, re.I):
                lang = "en"
        return Route(intent=intent, phases=phases, language=lang, source="rules", confidence=0.4)
