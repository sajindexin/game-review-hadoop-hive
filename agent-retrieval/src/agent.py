"""有依据的问答 Agent：意图识别 -> 调工具（指标/公告/评测）-> 模板化作答。

设计原则（对应交付要求）：
1. 每个数字都必须来自指标表，并给出 [n] 出处；
2. 每条玩家原话都带 review_id 与 Steam 链接，找不到就不编；
3. 没有证据时明确返回“暂无已复核引文”，而不是用模型常识补全。
"""
from __future__ import annotations

import re
from typing import Any

from .catalog import LANG_GROUPS, Catalog
from .retrieval import KnowledgeBase
from .router import Router
from .tokenize import GENERIC_CJK, STOP_BIGRAMS, tokenize

OUTSIDE_PATTERNS = [
    r"天气|下雨|带伞|气温|台风", r"公交|地铁|机票|航班|打车|路线", r"退款|退货|支付|付款|打折|折扣|优惠",
    r"密码|账号|登录|注册|客服|封号申诉", r"显卡驱动|装系统|重装|C盘|磁盘|内存条|装机|硬件故障",
    r"医院|生病|健康|体检|作业|考试|论文|选课", r"股票|基金|彩票|汇率",
]
METRIC_RE = re.compile(r"推荐率|好评率|差评率|百分之|百分点|样本|多少条|条数|占比|比例|变化|降了|升了|下降|上升|跌|涨")
REVIEW_RE = re.compile(r"玩家|评价|评论|反馈|吐槽|抱怨|差评|好评|怎么说|原话|骂|夸|觉得")
ANNOUNCE_RE = re.compile(r"公告|改了什么|改了啥|改动|更新内容|版本内容|更新日志|补丁说明|加了什么|加了啥|有什么新|新内容|什么新|哪些内容")
WHY_RE = re.compile(r"为什么|为啥|原因|怎么回事|导致")
RANK_RE = re.compile(r"哪个版本|哪些版本|所有版本|全组|排名|排行|对比一下版本|哪次更新|哪个更新")
RANK_EXTREME_RE = re.compile(r"最高|最低|最好|最差|最大|最小|最多|最少")
PAGE_VERSION_RE = re.compile(r"这个版本|该版本|当前版本|本版本")
PHASE_COMPARE_RE = re.compile(r"更新前后|发布前后|上线前后|前后对比|前后变化|前后差异")
PHASE_RELEASE_RE = re.compile(r"发布当天|上线当天|发布日当天|更新当天")
PHASE_BEFORE_RE = re.compile(r"更新前|发布前|上线前|版本前|之前")
PHASE_AFTER_RE = re.compile(r"更新后|发布后|上线后|版本后|之后")
GAME_HINTS = {
    "星露谷": "413150", "stardew": "413150", "艾尔登": "1245620", "elden": "1245620",
    "黑神话": "2358720", "悟空": "2358720", "wukong": "2358720", "泰拉瑞亚": "105600",
    "terraria": "105600", "求生之路": "550", "l4d": "550", "dota": "570", "刀塔": "570",
    "反恐精英": "730", "cs2": "730", "csgo": "730", "彩虹六号": "359550", "彩六": "359550",
    "r6": "359550", "siege": "359550", "gmod": "4000", "盖瑞": "4000", "tf2": "440",
    "军团要塞": "440", "gta": "271590", "侠盗": "271590", "rust": "252490", "腐蚀": "252490",
}


def _fmt_int(n: int | None) -> str:
    return f"{int(n):,}" if n is not None else "-"


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "-"


def _fmt_pp(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{abs(x):.1f} 个百分点"


class Agent:
    def __init__(self, kb: KnowledgeBase, router: Router | None = None) -> None:
        self.kb = kb
        self.catalog: Catalog = kb.catalog
        self.router = router or Router()

    # ---- 入口 ---------------------------------------------------------
    def ask(self, question: str, appid: str | None = None, patch_id: str | None = None,
            language: str | None = None, phase: str | None = None) -> dict[str, Any]:
        question = (question or "").strip()
        result: dict[str, Any] = {
            "question": question,
            "status": "answered",
            "answer": "",
            "numbers": [],
            "citations": [],
            "links": {},
            "tools": [],
            "evidence_status": "none",
            "empty_state_text": "暂无已复核引文",
        }
        if not question:
            return self._fail(result, "need_context", "请先输入问题，例如“这个版本推荐率降了多少？”")
        if phase not in ("before", "release", "after", "all", "compare", "release_day"):
            phase = None
        if phase == "release_day":
            phase = "release"

        # 1) 定位游戏与版本（版本解析仍用确定性规则，避免模型改错版本）
        if not appid:
            appid = self._appid_from_question(question)
        patch = self.catalog.patch(patch_id) if patch_id else None
        if patch and appid and str(patch["appid"]) != str(appid):
            patch = None
        if patch is None:
            patch = self.catalog.resolve_patch(question, appid)
        if patch:
            appid = str(patch["appid"])
        game = self.catalog.game(appid) if appid else None

        # 2) 意图/阶段/条目：LLM 优先，失败退回规则
        items = self.catalog.items_of(patch["patch_id"]) if patch else []
        route = self.router.route(question, patch, items, language)
        result["route"] = {
            "intent": route.intent, "phases": route.phases, "item_ids": route.item_ids,
            "language": route.language, "ranking_phase": route.ranking_phase,
            "ranking_direction": route.ranking_direction, "source": route.source,
            "confidence": round(route.confidence, 3), "reason": route.reason,
        }

        if route.intent == "outside":
            return self._fail(result, "outside_catalog",
                              "这个问题超出了游戏评测数据范围。我只能回答版本指标、官方改动和玩家评测相关的问题。")

        if route.intent == "ranking":
            return self._answer_ranking(result, question, language or route.language,
                                        route.ranking_phase, route.ranking_direction)

        if not patch:
            if not appid:
                return self._fail(result, "need_context",
                                  "我还不知道你问的是哪款游戏、哪次更新。请先选择游戏和版本，或在问题里带上版本号（例如“1.12”）。")
            return self._need_patch(result, appid, game)

        if not patch:
            if not appid:
                return self._fail(result, "need_context",
                                  "我还不知道你问的是哪款游戏、哪次更新。请先选择游戏和版本，或在问题里带上版本号（例如“1.12”）。")
            return self._need_patch(result, appid, game)

        # 3) 组装版本身份
        result["appid"] = appid
        result["game"] = game["game_name"] if game else patch["game"]
        result["patch"] = {
            "patch_id": patch["patch_id"], "patch_name": patch["patch_name"],
            "title_zh": patch.get("title_zh", ""), "release_date": patch["release_date"],
            "window_start": patch["window_start"], "window_end": patch["window_end"],
            "source_url": patch.get("source_url", ""),
        }
        result["links"] = {
            "stats": f"/versions/{patch['patch_id']}",
            "evidence": f"/versions/{patch['patch_id']}?tab=feedback",
            "announcement": patch.get("source_url", ""),
        }

        # 4) 调工具
        numbers: list[dict] = []
        citations: list[dict] = []
        tool_log: list[dict] = []
        segments: list[str] = []

        intent = route.intent
        explicit_phase = phase is not None
        if phase in ("before", "release", "after"):
            phases = [phase]
        elif phase == "compare":
            phases = ["before", "after"]
        elif phase == "all":
            phases = []
        else:
            phases = list(route.phases)
        # 只有调用方没显式指定阶段时，才把差评归因默认成更新后
        if intent == "why" and not phases and not explicit_phase:
            phases = ["after"]
        if language in ("all", "chinese", "en"):
            lang = language
        else:
            lang = route.language
        lang_filter = lang if lang in ("chinese", "en") else None
        if lang in ("all", "chinese", "en"):
            group = lang
        else:
            group = lang_filter or self._language_group(question, None)

        metric = None
        want_metric = intent in ("metric", "mixed", "why")
        if want_metric:
            metric = self.catalog.metrics_of(patch["patch_id"], group)
            tool_log.append({"name": "lookup_metrics", "args": {"patch_id": patch["patch_id"], "language_group": group},
                             "status": "ok" if metric else "missing"})
            if metric:
                segments.append(self._metric_sentence(patch, metric, group, len(citations) + 1))
                numbers.extend(self._metric_numbers(metric, patch, group))
                citations.append({
                    "id": len(citations) + 1, "type": "metric", "title": "更新前后推荐率",
                    "detail": f"{LANG_GROUPS[group]} · 更新前 14 天 / 发布当天 / 更新后 14 天",
                    "source": "上游版本分析模块 Hive 窗口统计（12 款游戏 26 个版本，数据版本 v1）",
                    "url": "", "snippet": "",
                })
                result["links"]["stats"] += f"?lang={group}"

        if intent in ("announcement", "mixed") or (intent == "why" and ANNOUNCE_RE.search(question)):
            # “改了什么/有什么新内容”是列清单，不能因为问句里的虚词只返回两三条
            listing = intent == "announcement"
            ann = self.kb.search_announcements("" if listing else question, patch_id=patch["patch_id"],
                                               top_k=20 if listing else 6)
            items = ann.get("items") or []
            tool_log.append({"name": "search_announcements",
                             "args": {"patch_id": patch["patch_id"], "query": "" if listing else question},
                             "status": ann.get("status", "none")})
            if items:
                text, cits = self._announcement_block(patch, items, len(citations) + 1)
                segments.append(text)
                citations.extend(cits)

        want_review = intent in ("review", "mixed", "why")
        if want_review:
            sentiment = "negative" if (intent == "why" or "差评" in question or "吐槽" in question) else None
            prefer_zh = bool(re.search(r"[\u4e00-\u9fff]", question))
            candidates = (self._items_from_route(route.item_ids, patch)
                          or self._resolve_items(question, patch, allow_bm25=(intent != "why")))
            explicit_ids = self._explicit_item_ids(question, self.catalog.items_of(patch["patch_id"]))
            if intent == "why":
                # 差评归因只在“标题/别名明确点名”时限定条目；同义词推断不缩范围
                named_ids = self._named_item_ids(question, self.catalog.items_of(patch["patch_id"]))
                candidates = [c for c in candidates if c["item_id"] in named_ids]
            blocks, logs = self._collect_review_blocks(question, patch, candidates, phases,
                                                       lang_filter, sentiment, prefer_zh,
                                                       explicit_ids)
            tool_log.extend(logs)
            any_quotes = any(b[2] for b in blocks)
            any_verified = any(b[2] and b[3] for b in blocks)
            has_unverified = any(b[2] and not b[3] for b in blocks)
            for item, ph, quotes, verified, sort_label in blocks:
                if quotes:
                    text, cits = self._review_block(quotes, len(citations) + 1, item, sort_label,
                                                    verified=verified)
                    segments.append(text)
                    citations.extend(cits)
                elif len(blocks) > 1:
                    title = f"《{item['title']}》" if item else "该版本"
                    segments.append(f"{title}{self._phase_label(ph)}：暂无已复核引文。")
            if any_quotes:
                if any_verified and has_unverified:
                    result["evidence_status"] = "mixed_evidence"
                elif any_verified:
                    result["evidence_status"] = "verified_quotes"
                else:
                    result["evidence_status"] = "dataset_quotes"
            else:
                scope = []
                if lang_filter:
                    scope.append({"chinese": "中文", "en": "英文"}[lang_filter])
                if phases:
                    scope.append("、".join(self._phase_label(p) for p in phases))
                where = f"（已按{'、'.join(scope)}筛选）" if scope else ""
                segments.append(f"玩家原话：暂无已复核引文{where}。")

        # 原文链接必须带实际关联条目；多条目时每个条目各给一个入口
        review_item_ids = [c.get("item_id") for c in citations
                           if c.get("type") == "review" and c.get("item_id")]
        if review_item_ids:
            item_map = {it["item_id"]: it for it in self.catalog.items_of(patch["patch_id"])}
            unique_ids = list(dict.fromkeys(review_item_ids))
            result["links"]["evidence"] = (f"/versions/{patch['patch_id']}"
                                           f"?tab=feedback&item={unique_ids[0]}")
            result["links"]["evidence_items"] = [{
                "item_id": iid,
                "title": item_map.get(iid, {}).get("title", ""),
                "url": f"/versions/{patch['patch_id']}?tab=feedback&item={iid}",
            } for iid in unique_ids]

        result["tools"] = tool_log
        result["numbers"] = numbers
        result["citations"] = citations
        result["answer"] = "\n\n".join(s for s in segments if s)
        result["suggestions"] = self._suggestions(patch)
        if not result["answer"]:
            return self._fail(result, "unsupported", "这个问题我暂时没有可用的数据工具来回答。可以问推荐率、官方改了什么，或玩家怎么评价某个改动。")
        return result

    # ---- 意图 ---------------------------------------------------------
    @staticmethod
    def _intent(q: str) -> str:
        wants_rank = bool(RANK_RE.search(q)) or (bool(RANK_EXTREME_RE.search(q)) and "版本" in q)
        has_metric = bool(METRIC_RE.search(q)) or "推荐率" in q or "好评率" in q
        page_context = bool(PAGE_VERSION_RE.search(q)) and not re.search(r"哪个|哪些|所有|全组|排名|排行", q)
        if wants_rank and has_metric and not page_context:
            return "ranking"
        m, r, a = bool(METRIC_RE.search(q)), bool(REVIEW_RE.search(q)), bool(ANNOUNCE_RE.search(q))
        if WHY_RE.search(q):
            return "why"
        if m and (r or a):
            return "mixed"
        if m:
            return "metric"
        if a:
            return "announcement"
        if r:
            return "review"
        return "review"

    def _appid_from_question(self, q: str) -> str | None:
        low = q.lower()
        for hint, appid in sorted(GAME_HINTS.items(), key=lambda x: -len(x[0])):
            if hint in low:
                return appid
        for g in self.catalog.games:
            if g["game_name"].lower() in low:
                return str(g["appid"])
        return None

    @staticmethod
    def _language_group(question: str, language: str | None) -> str:
        if language in LANG_GROUPS:
            return language
        if re.search(r"中文|汉语|简中|繁中", question):
            return "chinese"
        if re.search(r"英文|英语", question, re.I):
            return "en"
        return "all"

    @staticmethod
    def _phase_from_question(question: str) -> str | None:
        if PHASE_RELEASE_RE.search(question):
            return "release"
        if PHASE_BEFORE_RE.search(question):
            return "before"
        if PHASE_AFTER_RE.search(question):
            return "after"
        return None

    def _resolve_items(self, question: str, patch: dict, allow_bm25: bool = True) -> list[dict]:
        """按提及顺序返回候选条目；一个问题提到两个改动时，两个都留作候选。"""
        items = self.catalog.items_of(patch["patch_id"])
        by_id = {it["item_id"]: it for it in items}
        low = question.lower()
        exact: list[tuple[int, int, dict]] = []
        for order, it in enumerate(items):
            hit = max((len(t) for t in [it["title"], *(it.get("aliases_zh") or []),
                                        *(it.get("aliases_en") or []), *(it.get("synonyms") or [])]
                       if t and t.lower() in low), default=0)
            if hit:
                exact.append((hit, order, it))
        exact.sort(key=lambda x: (-x[0], x[1]))
        out: list[dict] = []
        for _, _, it in exact:
            if it["item_id"] not in {x["item_id"] for x in out}:
                out.append(it)
        if out:
            return out[:3]

        # 别名词典没直接命中时，用条目 BM25（已带同义词/负向词）排序
        if allow_bm25:
            cand = self.kb.item_candidates(question, patch["appid"], patch["patch_id"], top_k=3)
            if cand.get("status") == "ok":
                return [by_id[c["item_id"]] for c in cand["candidates"] if c["item_id"] in by_id]

        # 最后兜底：问题里的实体词出现在条目标题/别名里
        game_blob = (patch.get("game", "") + " " + str(self.catalog.game(patch["appid"]) or "")).lower()
        hint_blob = " ".join(GAME_HINTS.keys())
        toks = [t for t in tokenize(question) if len(t) >= 2
                and t not in STOP_BIGRAMS and t not in GENERIC_CJK]
        scored: list[tuple[int, int, dict]] = []
        for order, it in enumerate(items):
            blob = " ".join([it["title"], it.get("title_en", ""),
                             *(it.get("aliases_zh") or []), *(it.get("aliases_en") or []),
                             *(it.get("synonyms") or [])]).lower()
            s = sum(len(t) for t in toks if t in blob and t not in game_blob and t not in hint_blob)
            if s > 0:
                scored.append((s, order, it))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [it for _, _, it in scored[:3]]

    def _resolve_item(self, question: str, patch: dict) -> dict | None:
        items = self._resolve_items(question, patch)
        return items[0] if items else None

    def _explicit_item_ids(self, question: str, items: list[dict]) -> set[str]:
        """问题里直接出现标题/别名/同义词，都算“词面已点名”，可跳过相关度门槛。"""
        low = question.lower()
        ids: set[str] = set()
        for it in items:
            terms = [it["title"], *(it.get("aliases_zh") or []),
                     *(it.get("aliases_en") or []), *(it.get("synonyms") or [])]
            if any(t and len(t) >= 2 and t.lower() in low for t in terms):
                ids.add(it["item_id"])
        return ids

    def _named_item_ids(self, question: str, items: list[dict]) -> set[str]:
        """只有标题/别名才算“用户明确点名”，用于差评归因时限定条目。"""
        low = question.lower()
        ids: set[str] = set()
        for it in items:
            terms = [it["title"], *(it.get("aliases_zh") or []), *(it.get("aliases_en") or [])]
            if any(t and len(t) >= 2 and t.lower() in low for t in terms):
                ids.add(it["item_id"])
        return ids

    def _items_from_route(self, item_ids: list[str], patch: dict) -> list[dict]:
        if not item_ids:
            return []
        by_id = {it["item_id"]: it for it in self.catalog.items_of(patch["patch_id"])}
        return [by_id[i] for i in item_ids if i in by_id]

    @staticmethod
    def _phase_label(phase: str | None) -> str:
        return {"before": "更新前", "release": "发布当天", "after": "更新后"}.get(phase or "", "")

    def _collect_review_blocks(self, question: str, patch: dict, candidates: list[dict],
                               phases: list[str], lang_filter: str | None,
                               sentiment: str | None, prefer_zh: bool,
                               explicit_ids: set[str]):
        """按条目 × 阶段收集证据；compare 时 before/after 各一块，多条目每项各一块。"""
        patch_id = patch["patch_id"]
        patch_has_verified = self.kb.has_verified(patch_id)
        pools: list[dict | None] = candidates or [None]
        phase_list: list[str | None] = phases or [None]
        per = 3 if (len(pools) == 1 and len(phase_list) == 1) else 2
        blocks: list[tuple[dict | None, str | None, list[dict], bool, str]] = []
        logs: list[dict] = []

        for item in pools:
            cid = item["item_id"] if item else None
            for ph in phase_list:
                quotes: list[dict] = []
                verified = False
                sort_label = "按相关度排序"
                if patch_has_verified:
                    vres = self.kb.search_verified(question, patch_id, item_id=cid, phase=ph,
                                                   language=lang_filter, sentiment=sentiment,
                                                   top_k=per, prefer_zh=prefer_zh,
                                                   require_relevance=bool(cid and cid not in explicit_ids))
                    logs.append({"name": "search_verified_quotes",
                                 "args": {"patch_id": patch_id, "item_id": cid,
                                          "phase": ph, "language": lang_filter},
                                 "status": vres["status"], "available": vres.get("available", 0)})
                    if vres["status"] == "verified_quotes":
                        quotes = vres["quotes"]
                        verified = True

                if not quotes and sentiment:
                    res = self.kb.top_reviews(patch_id, phase=ph or "after", sentiment="negative",
                                              language=lang_filter, item_id=cid,
                                              limit=per, prefer_zh=prefer_zh)
                    logs.append({"name": "top_negative_reviews",
                                 "args": {"patch_id": patch_id, "phase": ph, "item_id": cid,
                                          "language": lang_filter},
                                 "status": res["status"]})
                    quotes = res.get("quotes") or []
                    sort_label = f"{self._phase_label(ph or 'after')}差评按点赞排序"

                if not quotes and not patch_has_verified:
                    q = self._strip_entity(question, item) if item else question
                    res = self.kb.search_reviews(q or question, patch_id, item_id=cid,
                                                 language=lang_filter, phase=ph,
                                                 sentiment=sentiment, top_k=per,
                                                 prefer_zh=prefer_zh)
                    logs.append({"name": "search_reviews",
                                 "args": {"patch_id": patch_id, "item_id": cid,
                                          "query": q, "phase": ph, "language": lang_filter},
                                 "status": res["status"], "candidates": res.get("candidates")})
                    quotes = res.get("quotes") or []

                blocks.append((item, ph, quotes, verified, sort_label))
        return blocks, logs

    @staticmethod
    def _strip_entity(question: str, item: dict) -> str:
        q = question
        for term in [item["title"], *(item.get("aliases_zh") or []), *(item.get("aliases_en") or [])]:
            if term:
                q = re.sub(re.escape(term), " ", q, flags=re.I)
        q = re.sub(r"玩家|怎么|如何|评价|评测|评论|说说|一下|这个|版本|更新|的|吗|呢|？|\?", " ", q)
        return re.sub(r"\s+", " ", q).strip()

    # ---- 组句 ---------------------------------------------------------
    def _metric_sentence(self, patch: dict, m: dict, group: str, cite: int) -> str:
        before_rate, after_rate = m.get("before_rate"), m.get("after_rate")
        delta = m.get("change_percentage_points")
        direction = "上升" if (delta or 0) >= 0 else "下降"
        return (
            f"《{patch['game']}》{patch['patch_name']}（{patch['release_date']}）的推荐率："
            f"更新前 14 天 {_fmt_pct(before_rate)}（{_fmt_int(m['before_recommended'])}/{_fmt_int(m['before_reviews'])}），"
            f"发布当天 {_fmt_pct(m.get('release_rate'))}（{_fmt_int(m['release_recommended'])}/{_fmt_int(m['release_reviews'])}），"
            f"更新后 14 天 {_fmt_pct(after_rate)}（{_fmt_int(m['after_recommended'])}/{_fmt_int(m['after_reviews'])}），"
            f"前后相比{direction} {_fmt_pp(delta)}（{LANG_GROUPS[group]}）[{cite}]"
        )

    def _metric_numbers(self, m: dict, patch: dict, group: str) -> list[dict]:
        return [
            {"label": "更新前推荐率", "value": m.get("before_rate"), "display": _fmt_pct(m.get("before_rate")),
             "unit": "%", "sample": m["before_reviews"], "language_group": group},
            {"label": "发布当天推荐率", "value": m.get("release_rate"), "display": _fmt_pct(m.get("release_rate")),
             "unit": "%", "sample": m["release_reviews"], "language_group": group},
            {"label": "更新后推荐率", "value": m.get("after_rate"), "display": _fmt_pct(m.get("after_rate")),
             "unit": "%", "sample": m["after_reviews"], "language_group": group},
            {"label": "变化", "value": m.get("change_percentage_points"),
             "display": f"{m['change_percentage_points']:+.1f} pp" if m.get("change_percentage_points") is not None else "-",
             "unit": "pp", "language_group": group},
        ]

    def _announcement_block(self, patch: dict, items: list[dict], start: int) -> tuple[str, list[dict]]:
        items = sorted(items, key=lambda x: x.get("item_id", ""))
        total = patch.get("item_count") or len(items)
        head = (f"这次更新官方公告共 {total} 项改动（官方公告链接见文末引用）："
                if len(items) >= total
                else f"与该问题相关的 {len(items)} 项改动（该版本公告共 {total} 项，官方链接见文末引用）：")
        parts = [head]
        cits = []
        for i, it in enumerate(items):
            cite = start + i
            parts.append(f"{i + 1}. {it['title']}：{it['description']} [{cite}]")
            cits.append({"id": cite, "type": "announcement", "title": it["title"],
                         "detail": it.get("title_en", ""), "snippet": it["description"],
                         "url": it.get("source_url", ""), "item_id": it["item_id"]})
        return "\n".join(parts), cits

    def _review_block(self, quotes: list[dict], start: int, item: dict | None,
                      sort_label: str = "按相关度排序", verified: bool = False) -> tuple[str, list[dict]]:
        phases = {q.get("phase") for q in quotes[:3]}
        phase_txt = ""
        if len(phases) == 1:
            phase_txt = {"before": "更新前", "release": "发布当天", "after": "更新后"}.get(next(iter(phases)), "")
        scope = f"{'《' + item['title'] + '》相关，' if item else ''}{phase_txt + '，' if phase_txt else ''}{sort_label}"
        head = (f"已复核玩家原话（{scope}）：" if verified else f"数据集原文（未复核，{scope}）：")
        lines = [head]
        cits = []
        for i, q in enumerate(quotes[:3]):
            cite = start + i
            tag = "推荐" if q["voted_up"] else "不推荐"
            lines.append(f"{i + 1}. “{q['snippet']}”—— {q['language_std']} · {q['created_at'][:10]} · {tag} [{cite}]")
            cits.append({"id": cite, "type": "review",
                         "title": f"玩家评测 {q['review_id']}",
                         "detail": f"{'已复核' if verified else '未复核'} · {q['phase_label']} · {q['language_std']} · {tag}",
                         "snippet": q["snippet"], "url": q["url"], "review_id": q["review_id"],
                         "votes_up": q.get("votes_up", 0),
                         "language_std": q.get("language_std", ""),
                         "phase": q.get("phase", ""),
                         "item_id": (q.get("item_id") or (q.get("matched_items") or [""])[0]
                                     or (item or {}).get("item_id", "")),
                         "verified": bool(verified), "verification": q.get("verification", "")})
        return "\n".join(lines), cits

    # ---- 其他回答 -----------------------------------------------------
    def _answer_ranking(self, result: dict, question: str, language: str | None,
                        rank_phase: str | None = None, direction: str | None = None) -> dict:
        group = self._language_group(question, language)
        rows = self.catalog.ranking(group)
        if not rows:
            return self._fail(result, "unsupported", "指标表里没有可用于排名的版本数据。")

        if rank_phase not in ("before", "release", "after"):
            rank_phase = self._phase_from_question(question)
        if rank_phase not in ("before", "release", "after"):
            rank_phase = "after"
        if direction not in ("top", "low", "rise", "drop"):
            if re.search(r"最高|最好|最强", question):
                direction = "top"
            elif re.search(r"最低|最差|最弱", question):
                direction = "low"
            elif re.search(r"上升|涨|升得|提高|增长", question):
                direction = "rise"
            else:
                direction = "drop"
        phase_label = {"before": "更新前", "release": "发布当天", "after": "更新后"}[rank_phase]
        rate_key = {"before": "before_rate", "release": "release_rate", "after": "after_rate"}[rank_phase]
        rec_key = {"before": "before_recommended", "release": "release_recommended",
                   "after": "after_recommended"}[rank_phase]
        reviews_key = {"before": "before_reviews", "release": "release_reviews",
                       "after": "after_reviews"}[rank_phase]

        extreme = direction in ("top", "low")
        citations: list[dict] = []
        if extreme:
            want_top = direction == "top"
            pool = [m for m in rows if m.get(rate_key) is not None]
            pick = (max(pool, key=lambda m: m[rate_key]) if want_top
                    else min(pool, key=lambda m: m[rate_key]))
            p = self.catalog.patch(pick["patch_id"])
            headline = (f"{phase_label}推荐率{'最高' if want_top else '最低'}的是《{p['game']}》{p['patch_name']}（{p['release_date']}）："
                        f"{_fmt_pct(pick[rate_key])}（{_fmt_int(pick[rec_key])}/{_fmt_int(pick[reviews_key])}）")
            citations.append({"id": 1, "type": "metric", "title": f"{p['game']} · {p['patch_name']}",
                              "detail": f"{p['release_date']} · {phase_label} {_fmt_pct(pick[rate_key])}",
                              "source": "上游版本分析模块 Hive 窗口统计", "url": "", "snippet": ""})
            other = (min(pool, key=lambda m: m[rate_key]) if want_top
                     else max(pool, key=lambda m: m[rate_key]))
            mode = f"{rank_phase}_{'top' if want_top else 'low'}"
        else:
            pool = [m for m in rows if m.get("change_percentage_points") is not None]
            want_rise = direction == "rise"
            pick = (max(pool, key=lambda m: m["change_percentage_points"]) if want_rise
                    else min(pool, key=lambda m: m["change_percentage_points"]))
            p = self.catalog.patch(pick["patch_id"])
            delta = pick["change_percentage_points"]
            verb = "上升" if delta >= 0 else "下降"
            headline = (f"更新前后推荐率{'上升' if want_rise else '下降'}最多的是《{p['game']}》{p['patch_name']}（{p['release_date']}）："
                        f"{_fmt_pct(pick['before_rate'])} → {_fmt_pct(pick['after_rate'])}，{verb} {_fmt_pp(delta)}"
                        f"（样本 {_fmt_int(pick['before_reviews'])} / {_fmt_int(pick['after_reviews'])}）")
            citations.append({"id": 1, "type": "metric", "title": f"{p['game']} · {p['patch_name']}",
                              "detail": f"{p['release_date']} · 更新前 {_fmt_pct(pick['before_rate'])} → 更新后 {_fmt_pct(pick['after_rate'])}",
                              "source": "上游版本分析模块 Hive 窗口统计", "url": "", "snippet": ""})
            other = (min(pool, key=lambda m: m["change_percentage_points"]) if want_rise
                     else max(pool, key=lambda m: m["change_percentage_points"]))
            mode = "rise" if want_rise else "drop"

        numbers = [
            {"label": "更新前推荐率", "value": pick["before_rate"], "display": _fmt_pct(pick["before_rate"]),
             "unit": "%", "sample": pick["before_reviews"], "language_group": group},
            {"label": "更新后推荐率", "value": pick["after_rate"], "display": _fmt_pct(pick["after_rate"]),
             "unit": "%", "sample": pick["after_reviews"], "language_group": group},
            {"label": "变化",
             "value": pick.get("change_percentage_points"),
             "display": (f"{pick['change_percentage_points']:+.1f} pp"
                         if pick.get("change_percentage_points") is not None else "-"),
             "unit": "pp", "language_group": group},
        ]
        if rank_phase == "release":
            numbers.insert(2, {"label": "发布当天推荐率", "value": pick.get("release_rate"),
                               "display": _fmt_pct(pick.get("release_rate")), "unit": "%",
                               "sample": pick.get("release_reviews"), "language_group": group})

        contrast = ""
        if other["patch_id"] != pick["patch_id"]:
            op = self.catalog.patch(other["patch_id"])
            if extreme:
                detail = f"{op['release_date']} · {phase_label} {_fmt_pct(other[rate_key])}"
                lead = f"对照：{phase_label}推荐率另一端是《{op['game']}》{op['patch_name']}，{_fmt_pct(other[rate_key])}"
            else:
                od = other["change_percentage_points"]
                detail = f"{op['release_date']} · 变化 {od:+.1f} pp"
                lead = (f"对照：变化幅度另一端是《{op['game']}》{op['patch_name']}，"
                        f"{_fmt_pct(other['before_rate'])} → {_fmt_pct(other['after_rate'])}（{od:+.1f} pp）")
            citations.append({"id": 2, "type": "metric", "title": f"{op['game']} · {op['patch_name']}",
                              "detail": detail, "source": "上游版本分析模块 Hive 窗口统计", "url": "", "snippet": ""})
            contrast = f"\n\n{lead} [2]"

        header = (f"按{phase_label}推荐率统计（{LANG_GROUPS[group]}）：" if extreme
                  else f"按更新前后各 14 天的推荐率变化统计（{LANG_GROUPS[group]}）：")
        answer = f"{header}\n\n{headline} [1]" + contrast
        result.update({"status": "answered", "answer": answer, "numbers": numbers,
                       "citations": citations,
                       "tools": [{"name": "ranking_metrics",
                                  "args": {"language_group": group, "mode": mode, "phase": rank_phase},
                                  "status": "ok"}],
                       "links": {}, "suggestions": []})
        return result

    def _need_patch(self, result: dict, appid: str, game: dict | None) -> dict:
        patches = self.catalog.patches_of(appid)
        opts = "、".join(f"{p['patch_name']}（{p['release_date']}）" for p in patches[-6:])
        result.update({
            "status": "need_context", "appid": appid,
            "game": game["game_name"] if game else "",
            "answer": f"你要问《{game['game_name'] if game else appid}》的哪次更新？可选：{opts}。",
            "suggestions": [f"{p['patch_name']} 推荐率变化" for p in patches[-3:]],
        })
        return result

    def _suggestions(self, patch: dict) -> list[str]:
        name = patch["patch_name"]
        items = self.catalog.items_of(patch["patch_id"])
        first = items[0]["title"] if items else "这次更新"
        return [f"{name} 更新后推荐率降了多少？", f"{name} 改了什么？", f"玩家怎么评价{first}？"]

    @staticmethod
    def _fail(result: dict, status: str, message: str) -> dict:
        result["status"] = status
        result["answer"] = message
        return result
