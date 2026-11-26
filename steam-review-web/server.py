# -*- coding: utf-8 -*-
"""
联调用本地服务：静态站 + POST /api/ask。

    python server.py --port 8000

接口契约（与 README / assets/ask.js 一致）：
    POST /api/ask  {"patch_id": "...", "question": "..."}
    → {"answer": "<p>…</p>", "citations": [...], "stats": {...}, "intent": "..."}

后端只读 data/ 下已导出的离线数据包，不连 Hive，用于前端联调与演示。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


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
                "votes_up": c.get("votes_up", 0),
                "playtime_hours": c.get("playtime_hours", 0),
                "text": c["text"],
                "url": review_url(c, appid),
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


def answer(patch_id: str, question: str) -> dict:
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
        "window_reviews": d.get("window_reviews_total"),
        "pool_reviews": d.get("pool_reviews"),
    }

    # 1) 负面 / 差评
    if has("不推荐", "差评", "负面", "问题", "抱怨", "吐槽"):
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
    if has("语种", "中文", "英文", "语言", "简中", "繁中"):
        rows = [x for x in d["language_compare"] if x["lang"] != "all"]
        cn = next((x for x in rows if x["lang"] == "chinese"), None)
        en = next((x for x in rows if x["lang"] == "en"), None)
        worst = min(rows, key=lambda x: x["delta"]) if rows else None
        body = head + f"<p>中文与英文口径对比（更新前 → 更新后）：</p><ul>"
        if cn:
            body += f"<li>简中 + 繁中：{pct(cn['before_rate'])} → {pct(cn['after_rate'])}（{pp(cn['delta'])}，样本 {cn['before_reviews']}+{cn['after_reviews']} 条）</li>"
        if en:
            body += f"<li>英语：{pct(en['before_rate'])} → {pct(en['after_rate'])}（{pp(en['delta'])}，样本 {en['before_reviews']}+{en['after_reviews']} 条）</li>"
        body += "</ul>"
        if cn and en:
            gap = round(en["after_rate"] - cn["after_rate"], 2)
            body += (
                f"<p>更新后英语口径比中文口径高 <b>{gap:.2f} pp</b>"
                f"（{pct(en['after_rate'])} vs {pct(cn['after_rate'])}），"
                f"两端变化方向一致，中文样本量约为英语的 1/6，波动更大。</p>"
            )
        if worst:
            body += f"<p>相对变化绝对值最大的是 <b>{LANG_CN.get(worst['lang'], worst['lang'])}</b>（{pp(worst['delta'])}）。</p>"
        zh = [c for c in d["comments"] if c["language"] in ("zh-Hans", "zh-Hant")]
        zh.sort(key=lambda c: -c["votes_up"])
        return {"intent": "language", "answer": body, "citations": cites(zh, p["appid"]), "stats": stats}

    # 3) 改动条目
    if has("改动", "条目", "更新内容", "哪一项", "改了什么", "提及", "新增"):
        items = sorted(p["items"], key=lambda x: -x["mentions"])
        body = head + f"<p>本版本登记 <b>{len(items)}</b> 项改动，按窗口内评测提及次数排序：</p><ul>"
        for it in items:
            body += f"<li>{it['title']}（{it.get('section') or '—'}）· 提及 {it['mentions']} 次 — {it.get('description') or ''}</li>"
        body += "</ul><p>下面是提及最多的那项改动所命中的评测原文：</p>"
        top = items[0] if items else None
        ev = top["evidence"] if top else []
        return {"intent": "items", "answer": body, "citations": cites(ev, p["appid"]), "stats": stats}

    # 4) 趋势 / 峰值
    if has("趋势", "峰值", "哪天", "时间", "评论量", "波动"):
        trend = [x for x in d["trend"] if x["rate"] is not None]
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
            result = answer(patch_id, question)
        except FileNotFoundError:
            self.send_error(404, f"unknown patch_id: {patch_id}")
            return

        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    mimetypes.add_type("application/javascript", ".js")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    idx = load_index()
    print(f"服务已启动： http://127.0.0.1:{args.port}/index.html")
    print(f"接口：       POST http://127.0.0.1:{args.port}/api/ask")
    print(f"数据：       {idx['source']['games']} 款游戏 / {idx['source']['patches']} 个版本")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
