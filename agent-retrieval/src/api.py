"""HTTP 接口：给 Java Web（石曜铭）和任何前端调用。

启动：python -m src.cli serve --port 8000
文档：http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .catalog import LANG_GROUPS
from .retrieval import KnowledgeBase

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    appid: str | None = None
    patch_id: str | None = None
    language: str | None = Field(default=None, description="all / chinese / en")


class ReviewQuery(BaseModel):
    query: str = ""
    appid: str | None = None
    patch_id: str
    item_id: str | None = None
    language: str | None = None
    phase: str | None = Field(default=None, description="before / release / after / all")
    sentiment: str | None = Field(default=None, description="negative / positive")
    top_k: int = Field(default=5, ge=1, le=20)


class AnnouncementQuery(BaseModel):
    query: str = ""
    appid: str | None = None
    patch_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class VerifiedQuery(BaseModel):
    query: str = ""
    patch_id: str
    item_id: str | None = None
    phase: str | None = Field(default=None, description="before / release / after / all")
    language: str | None = None
    sentiment: str | None = Field(default=None, description="negative / positive")
    top_k: int = Field(default=5, ge=1, le=20)


class CandidateQuery(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    appid: str
    patch_id: str
    top_k: int = Field(default=3, ge=1, le=10)


def create_app(data_dir: str | Path = "data") -> FastAPI:
    kb = KnowledgeBase(Path(data_dir))
    agent = Agent(kb)

    app = FastAPI(title="游戏更新反馈 · 检索与问答 Agent", version="1.0.0",
                  description="唐宇杰负责模块：BM25+BGE 检索接口、候选改动条目、可调用的 Agent 服务。")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "data_version": kb.catalog.data_version,
                "docs": int(len(kb.docs)), "encoder": kb.encoder_meta.get("name")}

    @app.get("/api/meta")
    def meta() -> dict:
        return {**kb.meta, "games": len(kb.catalog.games), "patches": len(kb.catalog.patches),
                "items": len(kb.catalog.items)}

    @app.get("/api/games")
    def games() -> dict:
        out = []
        for g in kb.catalog.games_sorted():
            out.append({**g, "patch_count": len(kb.catalog.patches_of(g["appid"]))})
        return {"games": out}

    @app.get("/api/games/{appid}/patches")
    def patches(appid: str) -> dict:
        if not kb.catalog.game(appid):
            raise HTTPException(404, "unknown appid")
        rows = []
        for p in kb.catalog.patches_of(appid):
            m = kb.catalog.metrics_of(p["patch_id"], "all") or {}
            rows.append({
                "patch_id": p["patch_id"], "patch_name": p["patch_name"],
                "title_zh": p.get("title_zh", ""), "release_date": p["release_date"],
                "window_start": p["window_start"], "window_end": p["window_end"],
                "item_count": p["item_count"],
                "before_rate": m.get("before_rate"), "after_rate": m.get("after_rate"),
                "change_percentage_points": m.get("change_percentage_points"),
            })
        return {"appid": appid, "game": kb.catalog.game(appid)["game_name"], "patches": rows}

    @app.get("/api/patches/{patch_id}")
    def patch_detail(patch_id: str) -> dict:
        p = kb.catalog.patch(patch_id)
        if not p:
            raise HTTPException(404, "unknown patch")
        return {
            "patch": {k: p.get(k) for k in ("patch_id", "patch_name", "title_zh", "release_date",
                                            "window_start", "window_end", "source_url", "changelog_url")},
            "game": {"appid": str(p["appid"]), "game_name": p["game"]},
            "items": kb.catalog.items_of(patch_id),
            "metrics": {g: kb.catalog.metrics_of(patch_id, g) for g in LANG_GROUPS
                        if kb.catalog.metrics_of(patch_id, g)},
        }

    @app.get("/api/patches/{patch_id}/metrics")
    def patch_metrics(patch_id: str, language: str = "all") -> dict:
        if language not in LANG_GROUPS:
            raise HTTPException(400, "language must be all/chinese/en")
        m = kb.catalog.metrics_of(patch_id, language)
        if not m:
            raise HTTPException(404, "no metrics")
        return m

    @app.post("/api/retrieve/reviews")
    def retrieve_reviews(body: ReviewQuery) -> dict:
        return kb.search_reviews(body.query, body.patch_id, item_id=body.item_id,
                                 language=body.language, phase=body.phase,
                                 sentiment=body.sentiment, top_k=body.top_k)

    @app.post("/api/retrieve/announcements")
    def retrieve_announcements(body: AnnouncementQuery) -> dict:
        return kb.search_announcements(body.query, appid=body.appid, patch_id=body.patch_id,
                                       top_k=body.top_k)

    @app.post("/api/retrieve/verified")
    def retrieve_verified(body: VerifiedQuery) -> dict:
        return kb.search_verified(body.query, body.patch_id, item_id=body.item_id,
                                  phase=body.phase, language=body.language,
                                  sentiment=body.sentiment, top_k=body.top_k)

    @app.post("/api/items/candidates")
    def item_candidates(body: CandidateQuery) -> dict:
        return kb.item_candidates(body.text, body.appid, body.patch_id, top_k=body.top_k)

    @app.post("/api/agent/ask")
    def ask(body: AskBody) -> dict:
        return agent.ask(body.question, appid=body.appid, patch_id=body.patch_id,
                         language=body.language)

    @app.get("/api/qa/examples")
    def examples() -> dict:
        path = Path(__file__).resolve().parents[1] / "eval" / "acceptance.jsonl"
        rows = []
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    rows.append({k: r.get(k) for k in ("id", "question", "appid", "patch_id", "category")})
        return {"examples": rows}

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/versions/{patch_id}")
    def version_page(patch_id: str):
        return FileResponse(WEB_DIR / "version.html")

    @app.get("/version.js")
    def version_js():
        return FileResponse(WEB_DIR / "version.js")

    @app.get("/app.js")
    def app_js():
        return FileResponse(WEB_DIR / "app.js")

    @app.get("/style.css")
    def style_css():
        return FileResponse(WEB_DIR / "style.css")

    return app
