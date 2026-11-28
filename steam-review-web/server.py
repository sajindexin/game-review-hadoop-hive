# -*- coding: utf-8 -*-
"""
联调用本地服务：静态站 + POST /api/ask。

两种模式：

1) 本地规则引擎（默认）
       python server.py --port 8021
   只读 data/ 下已导出的离线数据包，不依赖任何外部服务。

2) 接通检索 Agent（唐宇杰模块）
       python server.py --port 8021 --agent-base http://127.0.0.1:8000
   把 /api/ask 转发到对方的 POST /api/agent/ask，并把返回字段映射成前端契约。
   后端不可用时自动回退到本地规则引擎，页面不白屏。

前端契约：
    POST /api/ask  {"patch_id": "...", "question": "..."}
    → {"answer": "<p>…</p>", "citations": [...], "stats": {...}, "intent": "..."}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from backend import dataset
from backend.routes import api_error, dispatch_get

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# 由 --agent-base 指定；为 None 时走本地规则引擎
AGENT_BASE: str | None = None


# ---------------------------------------------------------------- Agent 适配层
#
# 唐宇杰模块（feat/agent-retrieval）的接口是 POST /api/agent/ask，返回
#   {status, answer(纯文本), numbers[], citations[{type:metric|announcement|review,...}],
#    links, evidence_status, suggestions, route}
# 前端页面的契约是
#   {intent, answer(HTML), stats{before_rate,...}, citations[{text,language,voted_up,url,...}]}
# 下面的 adapt_agent_response() 负责逐字段翻译，两个模块都不需要改。

# numbers[].label → 前端 stats 字段
_NUMBER_KEYS = {
    "更新前": "before_rate",
    "发布当天": "release_rate",
    "更新日": "release_rate",
    "更新后": "after_rate",
    "变化": "delta_pp",
    "相比": "delta_pp",
    "差": "delta_pp",
}


def _split_detail(detail: str) -> dict:
    """从 '已复核 · 更新后 14 天 · zh-Hans · 推荐' 里抠出结构化字段。"""
    out = {"verified_note": "", "phase": "", "language": "", "voted_up": None}
    if not detail:
        return out
    parts = [p.strip() for p in detail.split("·") if p.strip()]
    for p in parts:
        if "复核" in p:
            out["verified_note"] = p
        elif "更新前" in p or "发布前" in p:
            out["phase"] = "before"
        elif "发布当天" in p or "更新日" in p:
            out["phase"] = "release"
        elif "更新后" in p:
            out["phase"] = "after"
        elif p == "推荐":
            out["voted_up"] = True
        elif "不推荐" in p:
            out["voted_up"] = False
        elif re.match(r"^(zh-Hans|zh-Hant|en|ru|ja|ko|de|es|fr|pt-BR)$", p):
            out["language"] = p
    return out


def _text_to_html(text: str) -> str:
    """后端 answer 是纯文本（带 [1] 标记与换行），转成前端能渲染的段落。"""
    text = (text or "").strip()
    if not text:
        return ""
    if "<p>" in text or "<br" in text:  # 已经是 HTML
        return text

    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    html = []
    for b in blocks:
        lines = [ln.strip() for ln in b.split("\n") if ln.strip()]
        # 形如 "1. xxx  2. xxx" 的编号列表
        if len(lines) > 1 and all(re.match(r"^\d+[.、]\s*", ln) for ln in lines):
            lis = "".join(f"<li>{esc(re.sub(r'^\\d+[.、]\\s*', '', ln))}</li>" for ln in lines)
            html.append(f"<ul>{lis}</ul>")
        elif len(lines) > 1:
            html.append("<p>" + "<br>".join(esc(ln) for ln in lines) + "</p>")
        else:
            html.append(f"<p>{esc(lines[0])}</p>")
    return "".join(html)


def adapt_agent_response(agent: dict, question: str) -> dict:
    """唐宇杰模块的响应 → 前端契约。"""
    citations_raw = agent.get("citations") or []
    route = agent.get("route") or {}
    is_topic = route.get("intent") in ("topic", "topic_change")

    # ---- stats：从 numbers[] 里挑出三段推荐率与净变化 ----
    # 后端的量纲约定：unit="%" 时 value 是 0~1 比例；unit="pp" 时 value 已是百分点数值
    stats: dict = {
        "before_rate": None,
        "release_rate": None,
        "after_rate": None,
        "delta_pp": None,
        "language_group": None,
    }
    for n in ([] if is_topic else agent.get("numbers") or []):
        label = str(n.get("label") or "")
        unit = str(n.get("unit") or "")
        group = n.get("language_group")
        val = n.get("value")
        if isinstance(val, (int, float)):
            if unit == "%" and abs(val) <= 1.0001:
                val = round(val * 100, 2)
            elif unit == "pp":
                val = round(float(val), 2)
        for kw, key in _NUMBER_KEYS.items():
            if kw in label and stats.get(key) is None:
                if group and stats["language_group"] not in (None, group):
                    continue
                stats[key] = val
                if group:
                    stats["language_group"] = group
                break

    # 后端 delta 与本地页面各自四舍五入会差 0.01，统一按 before/after 差值重算，避免报告数字打架
    if stats.get("before_rate") is not None and stats.get("after_rate") is not None:
        stats["delta_pp"] = round(stats["after_rate"] - stats["before_rate"], 2)

    # 后端没给推荐率时，按问答的语种范围使用页面已有的版本统计。
    if not is_topic and all(stats[k] is None for k in ("before_rate", "release_rate", "after_rate")):
        try:
            local = load_patch(str(agent.get("patch", {}).get("patch_id") or ""))
            group = (agent.get("route") or {}).get("language") or "all"
            if group not in local["stats"]:
                group = "all"
            s = local["stats"][group]
            stats["language_group"] = group
            stats["before_rate"] = s.get("before", {}).get("rate")
            stats["release_rate"] = s.get("release", {}).get("rate")
            stats["after_rate"] = s.get("after", {}).get("rate")
            before, after = stats["before_rate"], stats["after_rate"]
            stats["delta_pp"] = round(after - before, 2) if before is not None and after is not None else None
            if group == "all":
                stats["delta_pp"] = local.get("delta_pp")
        except (FileNotFoundError, KeyError):
            pass

    # ---- citations：保留公告出处，评测引用仍使用原有前端契约 ----
    announcements = [
        {
            "id": c.get("id"),
            "title": c.get("title") or "官方公告",
            "url": c.get("url") or (agent.get("links") or {}).get("announcement") or "",
        }
        for c in citations_raw if c.get("type") == "announcement"
    ]
    out_cites = []
    for c in citations_raw:
        if c.get("type") != "review":
            continue
        parsed = _split_detail(c.get("detail") or "")
        out_cites.append(
            {
                "review_id": c.get("review_id") or "",
                "steamid": "",  # 后端给的是现成 url，不需要再拼
                "language": c.get("language_std") or parsed["language"],
                "voted_up": parsed["voted_up"] if parsed["voted_up"] is not None else True,
                "phase": c.get("phase") or parsed["phase"],
                "created_at": c.get("created_at") or "",
                "votes_up": c.get("votes_up"),
                "playtime_hours": 0,
                "text": c.get("snippet") or "",
                "url": c.get("url") or "",
                "verified": bool(c.get("verified")),
                "verification": c.get("verification") or "",
                "item_id": c.get("item_id") or "",
                "patch_id": (agent.get("patch") or {}).get("patch_id") or "",
                "detail": c.get("detail") or "",
            }
        )

    # ---- intent：按引用类型推断，仅用于页面上的小标签 ----
    types = {c.get("type") for c in citations_raw}
    if is_topic:
        intent = route["intent"]
    elif out_cites:
        intent = "negative" if all(not x["voted_up"] for x in out_cites) else "evidence"
    elif "announcement" in types:
        intent = "items"
    elif "metric" in types:
        intent = "overall"
    else:
        intent = "overall"

    answer_html = _text_to_html(agent.get("answer") or "")

    # 来源与证据级别由前端 ask.js 统一渲染（避免和服务端的提示重复），这里只传结构化字段
    return {
        "intent": intent,
        "answer": answer_html,
        "citations": out_cites,
        "announcement_citations": announcements,
        "stats": {} if is_topic else stats,
        "topic_stats": agent.get("topic_stats"),
        "topic_citations": [c for c in citations_raw if c.get("type") == "topic"],
        "source": "agent-retrieval",
        "evidence_status": agent.get("evidence_status"),
        "suggestions": agent.get("suggestions") or [],
        "route": agent.get("route"),
        "raw_status": agent.get("status"),
    }


def call_agent(patch_id: str, question: str) -> dict:
    """转发到唐宇杰模块的 /api/agent/ask，超时 30 秒。"""
    payload = {"question": question, "patch_id": patch_id}
    try:
        local = load_patch(patch_id)
        payload["appid"] = str(local["patch"]["appid"])
    except (FileNotFoundError, KeyError):
        pass

    req = urllib.request.Request(
        (AGENT_BASE or "").rstrip("/") + "/api/agent/ask",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))



def load_patch(patch_id: str) -> dict:
    path = DATA / "patches" / f"{patch_id}.js"
    if not path.exists():
        raise FileNotFoundError(patch_id)
    raw = path.read_text(encoding="utf-8")
    return json.loads(raw.split("=", 1)[1].strip().rstrip(";"))


def load_index() -> dict:
    raw = (DATA / "index.js").read_text(encoding="utf-8")
    return json.loads(raw.split("=", 1)[1].strip().rstrip(";"))


LANG_CN = {
    "chinese": "简中 + 繁中",
    "en": "英语",
    "ru": "俄语",
    "pt-BR": "葡萄牙语（巴西）",
    "ja": "日语",
    "ko": "韩语",
    "de": "德语",
    "es": "西班牙语",
    "fr": "法语",
}


def pct(v) -> str:
    return "—" if v is None else f"{v:.1f}%"


def pp(v) -> str:
    return "—" if v is None else f"{v:+.2f} pp"


def review_url(c: dict, appid: str) -> str:
    """Steam 评测页永久链接：/profiles/<steamid>/recommended/<appid>/"""
    if not c.get("steamid") or not appid:
        return ""
    return f"https://steamcommunity.com/profiles/{c['steamid']}/recommended/{appid}/"


def verified_item_quotes(patch_id: str, item_id: str) -> list[dict]:
    path = DATA / "verified.js"
    if not path.exists():
        return []
    rows = json.loads(path.read_text("utf-8").split("=", 1)[1].strip().rstrip(";"))
    return [
        {**row, "language": row["language_std"], "text": row["evidence"], "verified": True}
        for row in rows if row["patch_id"] == patch_id and row["item_id"] == item_id
    ]


def cites(rows: list[dict], appid: str, limit: int = 3) -> list[dict]:
    out = []
    for c in rows[:limit]:
        out.append(
            {
                "review_id": c["review_id"],
                "steamid": c.get("steamid", ""),
                "language": c["language"],
                "voted_up": c["voted_up"],
                "created_at": c["created_at"],
                "votes_up": c.get("votes_up"),
                "playtime_hours": c.get("playtime_hours", 0),
                "text": c["text"],
                "url": c.get("url") or review_url(c, appid),
                "verified": bool(c.get("verified")),
                "verification": c.get("verification", ""),
                "phase": c.get("phase", ""),
                "item_id": c.get("item_id", ""),
                "patch_id": c.get("patch_id", ""),
            }
        )
    return out


def status_text(delta) -> str:
    if delta is None:
        return "数据不足"
    if delta <= -5:
        return "口碑明显下滑"
    if delta < -1:
        return "略有回落"
    if delta <= 1:
        return "基本持平"
    return "口碑回升"


def is_topic_question(question: str) -> bool:
    if re.search(r"改动|条目", question) and re.search(r"提及|提到|讨论", question) and not re.search(r"主题|话题|负面|负向", question):
        return False
    return bool(re.search(r"主题|话题|主要.{0,8}(讨论|聊|关注)|讨论.{0,5}(什么|哪些|最多)|哪些方面.{0,8}(反馈|评价|吐槽)", question)
                or (re.search(r"方面", question) and re.search(r"负面|负向|差评|抱怨|吐槽", question)))


def local_statistics_intent(question: str) -> str | None:
    """识别本地数据包支持、检索 Agent 尚未覆盖的版本内统计问题。"""
    if is_topic_question(question):
        return None
    if re.search(r"哪个版本|哪些版本|所有版本|全组|各个版本|不同版本", question):
        return None
    compare = re.search(r"对比|比较|差异|差多少|相差|区别", question)
    chinese = re.search(r"中文|简中|繁中|汉语", question)
    english = re.search(r"英文|英语", question)
    if compare and ((chinese and english) or re.search(r"语种|语言", question)):
        return "language"
    if re.search(r"提到|提及|讨论", question) and re.search(r"最多|最少|次数|排序|排行|排名", question):
        if re.search(r"改动|条目|哪一?项", question):
            return "items"
    if re.search(r"评论量|评测量|评论数量|评测数量", question) and re.search(r"峰值|趋势|最多|哪一天|哪天|波动", question):
        return "trend"
    return None


def answer(patch_id: str, question: str, intent: str | None = None) -> dict:
    d = load_patch(patch_id)
    p = d["patch"]
    allst = d["stats"].get("all", {})
    b, r, a = allst.get("before", {}), allst.get("release", {}), allst.get("after", {})
    delta = d["delta_pp"]
    q = question.lower()

    def has(*ws):
        return any(w in q for w in ws)

    head = f"<p><b>{p['game']} · {p['title_zh'] or p['patch_name']}</b>（{p['release_date']} 上线）</p>"
    stats = {
        "before_rate": b.get("rate"),
        "release_rate": r.get("rate"),
        "after_rate": a.get("rate"),
        "delta_pp": delta,
        "language_group": "all",
        "window_reviews": d.get("window_reviews_total"),
        "pool_reviews": d.get("pool_reviews"),
    }

    if is_topic_question(question):
        return {"intent": "topic", "answer": head + "<p>主题情感分析需要连接并构建好 NLP 知识库的检索 Agent。</p>",
                "citations": [], "stats": {}, "raw_status": "unsupported"}

    # 1) 负面 / 差评
    if intent is None and has("不推荐", "差评", "负面", "问题", "抱怨", "吐槽"):
        downs = [c for c in d["comments"] if not c["voted_up"]]
        downs.sort(key=lambda c: -c["votes_up"])
        body = (
            head
            + f"<p>窗口内推荐率 {pct(b.get('rate'))} → {pct(a.get('rate'))}（{pp(delta)}），"
            f"下面是不推荐评价中赞数最高的几条：</p>"
        )
        return {
            "intent": "negative",
            "answer": body,
            "citations": cites(downs, p["appid"]),
            "stats": stats,
        }

    # 2) 语种差异
    if intent == "language" or (intent is None and has("语种", "中文", "英文", "语言", "简中", "繁中")):
        rows = [x for x in d["language_compare"] if x["lang"] != "all"]
        cn = next((x for x in rows if x["lang"] == "chinese"), None)
        en = next((x for x in rows if x["lang"] == "en"), None)
        changes = [x for x in rows if x.get("delta") is not None]
        largest = max(changes, key=lambda x: abs(x["delta"])) if changes else None
        body = head + f"<p>中文与英文口径对比（更新前 → 更新后）：</p><ul>"
        if cn:
            body += f"<li>简中 + 繁中：{pct(cn['before_rate'])} → {pct(cn['after_rate'])}（{pp(cn['delta'])}，样本 {cn['before_reviews']}+{cn['after_reviews']} 条）</li>"
        if en:
            body += f"<li>英语：{pct(en['before_rate'])} → {pct(en['after_rate'])}（{pp(en['delta'])}，样本 {en['before_reviews']}+{en['after_reviews']} 条）</li>"
        body += "</ul>"
        if cn and en and cn.get("after_rate") is not None and en.get("after_rate") is not None:
            gap = round(en["after_rate"] - cn["after_rate"], 2)
            body += (
                f"<p>更新后英语与中文推荐率相差 <b>{abs(gap):.2f} pp</b>"
                f"（英语 {pct(en['after_rate'])}，中文 {pct(cn['after_rate'])}）。</p>"
            )
        if largest:
            body += f"<p>推荐率变化绝对值最大的是 <b>{LANG_CN.get(largest['lang'], largest['lang'])}</b>（{pp(largest['delta'])}）。</p>"
        zh = [c for c in d["comments"] if c["language"] in ("zh-Hans", "zh-Hant")]
        zh.sort(key=lambda c: -c["votes_up"])
        return {"intent": "language", "answer": body, "citations": cites(zh, p["appid"]), "stats": stats}

    # 3) 改动条目
    if intent == "items" or (intent is None and has("改动", "条目", "更新内容", "哪一项", "改了什么", "提及", "新增")):
        items = sorted(p["items"], key=lambda x: -x["mentions"])
        body = head + f"<p>本版本登记 <b>{len(items)}</b> 项改动，按窗口内评测提及次数排序：</p><ul>"
        for it in items:
            body += f"<li>{it['title']}（{it.get('section') or '—'}）· 提及 {it['mentions']} 次 — {it.get('description') or ''}</li>"
        body += "</ul><p>提及数来自评测正文与官方条目别名的近似匹配。</p>"
        top = items[0] if items else None
        ev = verified_item_quotes(patch_id, top["item_id"]) if top else []
        body += "<p>提及最多的改动的已复核引文。</p>" if ev else "<p>提及最多的改动暂无已复核引文。</p>"
        return {"intent": "items", "answer": body, "citations": cites(ev, p["appid"]), "stats": stats}

    # 4) 趋势 / 峰值
    if intent == "trend" or (intent is None and has("趋势", "峰值", "哪天", "时间", "评论量", "波动")):
        trend = [x for x in d["trend"] if x["rate"] is not None]
        if not trend:
            return {"intent": "trend", "answer": head + "<p>窗口内没有可用趋势数据。</p>", "citations": [], "stats": stats}
        peak = max(trend, key=lambda x: x["reviews"])
        low = min(trend, key=lambda x: x["rate"])
        body = (
            head
            + f"<p>评论量峰值在 <b>{peak['date']}</b>（{peak['reviews']} 条，推荐率 {pct(peak['rate'])}）；"
            f"推荐率低点在 <b>{low['date']}</b>（{pct(low['rate'])}）。</p>"
            f"<p>更新前 14 天均值 {pct(b.get('rate'))}，更新当日 {pct(r.get('rate'))}，"
            f"更新后 14 天均值 {pct(a.get('rate'))}。</p>"
        )
        hot = sorted(d["comments"], key=lambda c: -c["votes_up"])[:3]
        return {"intent": "trend", "answer": body, "citations": cites(hot, p["appid"]), "stats": stats}

    # 5) 默认：整体口碑
    sample = [c for c in d["comments"] if c.get("items")] or d["comments"]
    body = (
        head
        + f"<p>结论：<b>{status_text(delta)}</b>。更新前 {pct(b.get('rate'))}（{b.get('reviews')} 条）、"
        f"更新当日 {pct(r.get('rate'))}（{r.get('reviews')} 条）、更新后 {pct(a.get('rate'))}"
        f"（{a.get('reviews')} 条），净变化 <b>{pp(delta)}</b>。</p>"
        f"<p>登记改动 {len(p['items'])} 项，抽样评测 {len(d['comments'])} 条。以下为命中改动条目的评测原文：</p>"
    )
    return {"intent": "overall", "answer": body, "citations": cites(sample, p["appid"]), "stats": stats}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):  # 安静一点
        print("[srv] " + fmt % args)

    def _send_json(self, status, result, etag=None):
        try:
            body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            error = api_error(exc)
            status = error.status
            body = json.dumps(error.payload(), ensure_ascii=False).encode("utf-8")
            etag = None
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if etag:
            # 静态数据集内容随构建变化，用内容摘要做弱校验，配合 If-None-Match 走 304
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        request = urlparse(self.path)
        if request.path != "/api" and not request.path.startswith("/api/"):
            return super().do_GET()

        try:
            payload, cacheable = dispatch_get(request.path, request.query)
        except Exception as exc:
            error = api_error(exc)
            self._send_json(error.status, error.payload())
            return

        etag = None
        if cacheable:
            digest = hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()[:32]
            etag = f'W/"{digest}"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return

        self._send_json(200, payload, etag=etag)

    def do_POST(self):
        if urlparse(self.path).path != "/api/ask":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return

        patch_id = str(payload.get("patch_id") or "")
        question = str(payload.get("question") or "").strip()
        if not patch_id or not question:
            self.send_error(400, "patch_id and question required")
            return

        try:
            local_intent = local_statistics_intent(question)
            if AGENT_BASE and local_intent is None:
                try:
                    agent = call_agent(patch_id, question)
                    result = adapt_agent_response(agent, question)
                    result["fallback"] = False
                except (urllib.error.URLError, TimeoutError, OSError):
                    result = answer(patch_id, question)
                    result["source"] = "local-rules"
                    result["fallback"] = True
                    result["fallback_reason"] = "检索 Agent 不可用"
            else:
                result = answer(patch_id, question, local_intent)
                result["source"] = "local-rules"
                result["fallback"] = False
        except FileNotFoundError:
            self.send_error(404, f"unknown patch_id: {patch_id}")
            return

        self._send_json(200, result)

    def end_headers(self):
        # 允许 file:// 页面直接调接口时跨域
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


def main():
    global AGENT_BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument(
        "--agent-base",
        default="",
        help="检索 Agent 地址，如 http://127.0.0.1:8000；留空则用本地规则引擎",
    )
    args = ap.parse_args()
    AGENT_BASE = (args.agent_base or "").strip() or None
    dataset.configure(DATA)

    mimetypes.add_type("application/javascript", ".js")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    idx = load_index()
    print(f"服务已启动： http://127.0.0.1:{args.port}/index.html")
    print(f"接口：       POST http://127.0.0.1:{args.port}/api/ask")
    print(f"Hive 检查：  GET http://127.0.0.1:{args.port}/api/health/hive")
    print(f"数据：       {idx['source']['games']} 款游戏 / {idx['source']['patches']} 个版本")
    if AGENT_BASE:
        print(f"问答后端：   检索 Agent {AGENT_BASE}（不可用时自动回退本地规则引擎）")
    else:
        print("问答后端：   本地规则引擎（如需接通检索 Agent，加 --agent-base http://127.0.0.1:8000）")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
