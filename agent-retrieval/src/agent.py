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

OUTSIDE_PATTERNS = [
    r"天气|下雨|带伞|气温|台风", r"公交|地铁|机票|航班|打车|路线", r"退款|退货|支付|付款|打折|折扣|优惠",
    r"密码|账号|登录|注册|客服|封号申诉", r"显卡驱动|装系统|重装|C盘|磁盘|内存条|装机|硬件故障",
    r"医院|生病|健康|体检|作业|考试|论文|选课", r"股票|基金|彩票|汇率",
]
METRIC_RE = re.compile(r"推荐率|好评率|差评率|百分之|百分点|样本|多少条|条数|占比|比例|变化|降了|升了|下降|上升|跌|涨")
REVIEW_RE = re.compile(r"玩家|评价|评论|反馈|吐槽|抱怨|差评|好评|怎么说|原话|骂|夸|觉得")
ANNOUNCE_RE = re.compile(r"公告|改了什么|改了啥|改动|更新内容|版本内容|更新日志|补丁说明|加了什么|加了啥|有什么新|新内容|什么新|哪些内容")
WHY_RE = re.compile(r"为什么|为啥|原因|怎么回事|导致")
RANK_RE = re.compile(r"哪个版本|哪些版本|最多|最少|排名|排行|全组|所有版本|整体|对比一下版本")
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
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.catalog: Catalog = kb.catalog

    # ---- 入口 ---------------------------------------------------------
    def ask(self, question: str, appid: str | None = None, patch_id: str | None = None,
            language: str | None = None) -> dict[str, Any]:
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

        if any(re.search(p, question, re.I) for p in OUTSIDE_PATTERNS):
            return self._fail(result, "outside_catalog",
                              "这个问题超出了游戏评测数据范围。我只能回答版本指标、官方改动和玩家评测相关的问题。")

        # 1) 定位游戏与版本
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

        intent = self._intent(question)

        # 2) 聚合问题（哪个版本跌得最多）
        if intent == "ranking" and not patch:
            return self._answer_ranking(result, question, language)

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

        metric = None
        want_metric = intent in ("metric", "mixed", "why")
        if want_metric:
            group = self._language_group(question, language)
            metric = self.catalog.metrics_of(patch["patch_id"], group)
            tool_log.append({"name": "lookup_metrics", "args": {"patch_id": patch["patch_id"], "language_group": group},
                             "status": "ok" if metric else "missing"})
            if metric:
                segments.append(self._metric_sentence(patch, metric, group, len(citations) + 1))
                numbers.extend(self._metric_numbers(metric, patch, group))
                citations.append({
                    "id": len(citations) + 1, "type": "metric", "title": "更新前后推荐率",
                    "detail": f"{LANG_GROUPS[group]} · 更新前 14 天 / 发布当天 / 更新后 14 天",
                    "source": "黄静雯 Hive 窗口统计（12 款游戏 26 个版本，数据版本 v1）",
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

        quotes: list[dict] = []
        review_sort = "按相关度排序"
        verified_mode = False
        want_review = intent in ("review", "mixed", "why")
        prefer_zh = bool(re.search(r"[\u4e00-\u9fff]", question))
        if want_review:
            item = self._resolve_item(question, patch)
            sentiment = "negative" if (WHY_RE.search(question) or "差评" in question or "吐槽" in question) else None
            patch_has_verified = self.kb.has_verified(patch["patch_id"])

            if patch_has_verified:
                vres = self.kb.search_verified(question, patch["patch_id"],
                                               item_id=item["item_id"] if item else None,
                                               phase="after" if sentiment else None,
                                               sentiment=sentiment, top_k=3, prefer_zh=prefer_zh)
                tool_log.append({"name": "search_verified_quotes",
                                 "args": {"patch_id": patch["patch_id"],
                                          "item_id": item["item_id"] if item else None},
                                 "status": vres["status"], "available": vres.get("available", 0)})
                if vres["status"] == "verified_quotes" and (not sentiment or len(vres["quotes"]) >= 2):
                    quotes = vres["quotes"]
                    verified_mode = True
                    review_sort = "按相关度排序"

            if not quotes and sentiment:
                # 归因问题用更新后被顶得最多的差评；已复核库不够 2 条时明确标注“未复核”
                res = self.kb.top_reviews(patch["patch_id"], phase="after", sentiment="negative", limit=3)
                tool_log.append({"name": "top_negative_reviews",
                                 "args": {"patch_id": patch["patch_id"], "phase": "after"},
                                 "status": res["status"]})
                quotes = res.get("quotes") or []
                review_sort = "更新后差评按点赞排序"

            if not quotes and not patch_has_verified:
                if item:
                    q = self._strip_entity(question, item)
                    res = self.kb.search_reviews(q or question, patch["patch_id"], item_id=item["item_id"],
                                                 top_k=3, prefer_zh=prefer_zh)
                    tool_log.append({"name": "search_reviews",
                                     "args": {"patch_id": patch["patch_id"], "item_id": item["item_id"], "query": q},
                                     "status": res["status"], "candidates": res.get("candidates")})
                    quotes = res.get("quotes") or []
                if not quotes and not sentiment:
                    # 只有“没锁定具体改动”时才退到整版本检索；条目明确但无证据，不能拿别的评论凑数
                    res = self.kb.search_reviews(question, patch["patch_id"], top_k=3, prefer_zh=prefer_zh)
                    tool_log.append({"name": "search_reviews",
                                     "args": {"patch_id": patch["patch_id"], "query": question},
                                     "status": res["status"], "candidates": res.get("candidates")})
                    quotes = res.get("quotes") or []

            if quotes:
                text, cits = self._review_block(quotes, len(citations) + 1, item, review_sort,
                                                verified=verified_mode)
                segments.append(text)
                citations.extend(cits)
                result["evidence_status"] = "verified_quotes" if verified_mode else "dataset_quotes"
            elif patch_has_verified and not sentiment:
                segments.append("玩家原话：暂无已复核引文（该版本有已复核引文库，但这条改动没有可引用原话）。")
            else:
                segments.append("玩家原话：暂无已复核引文（该版本窗口的样本里没有检索到可靠出处）。")

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
        if RANK_RE.search(q) and METRIC_RE.search(q):
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

    def _resolve_item(self, question: str, patch: dict) -> dict | None:
        best, best_len = None, 0
        for it in self.catalog.items_of(patch["patch_id"]):
            for term in [it["title"], *(it.get("aliases_zh") or []), *(it.get("aliases_en") or [])]:
                if term and term.lower() in question.lower() and len(term) > best_len:
                    best, best_len = it, len(term)
        return best

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
        for i, it in enumerate(items[:6]):
            cite = start + i
            parts.append(f"{i + 1}. {it['title']}：{it['description']} [{cite}]")
            cits.append({"id": cite, "type": "announcement", "title": it["title"],
                         "detail": it.get("title_en", ""), "snippet": it["description"],
                         "url": it.get("source_url", ""), "item_id": it["item_id"]})
        return "\n".join(parts), cits

    def _review_block(self, quotes: list[dict], start: int, item: dict | None,
                      sort_label: str = "按相关度排序", verified: bool = False) -> tuple[str, list[dict]]:
        if verified:
            head = f"已复核玩家原话（{'《' + item['title'] + '》相关，' if item else ''}{sort_label}）："
        else:
            head = f"数据集原文（未复核，{'《' + item['title'] + '》相关，' if item else ''}{sort_label}）："
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
                         "votes_up": q.get("votes_up", 0), "item_id": (item or {}).get("item_id", ""),
                         "verified": bool(verified), "verification": q.get("verification", "")})
        return "\n".join(lines), cits

    # ---- 其他回答 -----------------------------------------------------
    def _answer_ranking(self, result: dict, question: str, language: str | None) -> dict:
        group = self._language_group(question, language)
        rows = [m for m in self.catalog.ranking(group) if m.get("change_percentage_points") is not None]
        if not rows:
            return self._fail(result, "unsupported", "指标表里没有可用于排名的版本数据。")

        if re.search(r"最高|最好|最强", question):
            mode = "after_top"
        elif re.search(r"最低|最差", question):
            mode = "after_low"
        elif re.search(r"上升|涨|升得", question):
            mode = "rise"
        else:
            mode = "drop"

        if mode in ("after_top", "after_low"):
            pool = [m for m in rows if m.get("after_rate") is not None]
            pick = max(pool, key=lambda m: m["after_rate"]) if mode == "after_top" else min(pool, key=lambda m: m["after_rate"])
        else:
            pick = max(rows, key=lambda m: m["change_percentage_points"]) if mode == "rise" else min(rows, key=lambda m: m["change_percentage_points"])

        p = self.catalog.patch(pick["patch_id"])
        delta = pick["change_percentage_points"]
        if mode == "after_top":
            headline = (f"更新后推荐率最高的是《{p['game']}》{p['patch_name']}（{p['release_date']}）："
                        f"{_fmt_pct(pick['after_rate'])}（{_fmt_int(pick['after_recommended'])}/{_fmt_int(pick['after_reviews'])}），"
                        f"更新前为 {_fmt_pct(pick['before_rate'])}")
        elif mode == "after_low":
            headline = (f"更新后推荐率最低的是《{p['game']}》{p['patch_name']}（{p['release_date']}）："
                        f"{_fmt_pct(pick['after_rate'])}（{_fmt_int(pick['after_recommended'])}/{_fmt_int(pick['after_reviews'])}），"
                        f"更新前为 {_fmt_pct(pick['before_rate'])}")
        else:
            verb = "上升" if delta >= 0 else "下降"
            headline = (f"{'更新后推荐率上升' if mode == 'rise' else '更新后推荐率下降'}最多的是《{p['game']}》{p['patch_name']}（{p['release_date']}）："
                        f"{_fmt_pct(pick['before_rate'])} → {_fmt_pct(pick['after_rate'])}，{verb} {_fmt_pp(delta)}"
                        f"（样本 {_fmt_int(pick['before_reviews'])} / {_fmt_int(pick['after_reviews'])}）")

        citations = [{"id": 1, "type": "metric", "title": f"{p['game']} · {p['patch_name']}",
                      "detail": f"{p['release_date']} · 更新前 {_fmt_pct(pick['before_rate'])} → 更新后 {_fmt_pct(pick['after_rate'])}",
                      "source": "黄静雯 Hive 窗口统计", "url": "", "snippet": ""}]
        numbers = [
            {"label": "更新前推荐率", "value": pick["before_rate"], "display": _fmt_pct(pick["before_rate"]),
             "unit": "%", "sample": pick["before_reviews"], "language_group": group},
            {"label": "更新后推荐率", "value": pick["after_rate"], "display": _fmt_pct(pick["after_rate"]),
             "unit": "%", "sample": pick["after_reviews"], "language_group": group},
            {"label": "变化", "value": delta, "display": f"{delta:+.1f} pp", "unit": "pp", "language_group": group},
        ]

        # 再补一句另一端的版本，避免“只有结论没有对照”
        other = (min(rows, key=lambda m: m["change_percentage_points"]) if mode in ("rise", "after_top")
                 else max(rows, key=lambda m: m["change_percentage_points"]))
        if other["patch_id"] != pick["patch_id"]:
            op = self.catalog.patch(other["patch_id"])
            od = other["change_percentage_points"]
            citations.append({"id": 2, "type": "metric", "title": f"{op['game']} · {op['patch_name']}",
                              "detail": f"{op['release_date']} · 变化 {od:+.1f} pp",
                              "source": "黄静雯 Hive 窗口统计", "url": "", "snippet": ""})
            contrast = (f"\n\n对照：变化幅度另一端是《{op['game']}》{op['patch_name']}，"
                        f"{_fmt_pct(other['before_rate'])} → {_fmt_pct(other['after_rate'])}（{od:+.1f} pp）[2]")
        else:
            contrast = ""

        answer = f"按更新前后各 14 天的推荐率统计（{LANG_GROUPS[group]}）：\n\n{headline} [1]" + contrast
        result.update({"status": "answered", "answer": answer, "numbers": numbers,
                       "citations": citations, "tools": [{"name": "ranking_metrics", "args": {"language_group": group, "mode": mode}, "status": "ok"}],
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
