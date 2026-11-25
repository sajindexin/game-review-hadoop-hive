"""检索层：评测检索（BM25 + 向量混合）、公告检索、候选改动条目。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .bm25 import BM25Index
from .catalog import Catalog
from .dense import load_vectors
from .tokenize import tokenize

PHASE_LABEL = {"before": "更新前 14 天", "release": "发布当天", "after": "更新后 14 天"}
LANG_LABEL = {"all": "全部语言", "chinese": "中文", "en": "英文"}
CJK = re.compile(r"[\u4e00-\u9fff]")


def snippet(text: str, terms: list[str], width: int = 200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= width:
        return text
    pos = -1
    low = text.lower()
    for t in sorted([t for t in terms if t], key=len, reverse=True):
        pos = low.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:width] + "…"
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start : start + width] + "…"


class KnowledgeBase:
    def __init__(self, data_dir: Path) -> None:
        self.dir = Path(data_dir)
        self.catalog = Catalog(self.dir / "catalog.json")
        self.docs = pd.read_parquet(self.dir / "corpus.parquet")
        self.bm25 = BM25Index.load(self.dir / "bm25.pkl")
        self.vectors, self.encoder_meta = load_vectors(self.dir / "dense.npz")
        self.meta = json.loads((self.dir / "meta.json").read_text("utf-8"))
        self._patch_cache: dict[str, np.ndarray] = {}
        self._patch_items = {p["patch_id"]: p.get("items", []) for p in self.catalog.patches}
        self._texts = self.docs["text"].fillna("").astype(str).to_numpy()
        self._load_verified()
        self._build_item_index()

    def _load_verified(self) -> None:
        path = self.dir / "verified.json"
        self.verified: list[dict] = json.loads(path.read_text("utf-8")) if path.exists() else []
        self._verified_by_patch: dict[str, list[int]] = {}
        for i, v in enumerate(self.verified):
            self._verified_by_patch.setdefault(v["patch_id"], []).append(i)
        index_path = self.dir / "verified_bm25.pkl"
        self._verified_bm25 = BM25Index.load(index_path) if index_path.exists() else None
        self._verified_texts = [f"{v['evidence']} {v['item_title']}" for v in self.verified]

    def has_verified(self, patch_id: str) -> bool:
        return bool(self._verified_by_patch.get(patch_id))

    def verified_stats(self) -> dict:
        return {
            "count": len(self.verified),
            "patches": len(self._verified_by_patch),
            "items": len({(v["patch_id"], v["item_id"]) for v in self.verified}),
        }

    # ---- 基础掩码 -----------------------------------------------------
    def patch_indices(self, patch_id: str) -> np.ndarray:
        if patch_id not in self._patch_cache:
            self._patch_cache[patch_id] = np.flatnonzero(self.docs["patch_id"].to_numpy() == patch_id)
        return self._patch_cache[patch_id]

    def mask(self, patch_id: str, language: str | None = None, phase: str | None = None) -> np.ndarray:
        idx = self.patch_indices(patch_id)
        m = np.zeros(len(self.docs), dtype=np.float32)
        m[idx] = 1.0
        if language and language not in ("", "all"):
            lang = self.docs["language_std"].to_numpy()
            if language == "chinese":
                m *= np.array([1.0 if str(x).startswith("zh") else 0.0 for x in lang], dtype=np.float32)
            elif language == "en":
                m *= (lang == "en").astype(np.float32)
        if phase and phase not in ("", "all"):
            m *= (self.docs["phase"].to_numpy() == phase).astype(np.float32)
        return m

    # ---- 关键实体词 ---------------------------------------------------
    def _key_terms(self, query: str, item: dict | None, limit: int = 8) -> list[str]:
        from .tokenize import GENERIC_CJK, STOP_BIGRAMS

        toks = [t for t in dict.fromkeys(tokenize(query)) if len(t) >= 2]
        toks = [t for t in toks if t not in STOP_BIGRAMS and t not in GENERIC_CJK]
        keys: list[str] = []
        if item:
            # 条目明确时，只认“该改动的别名/标题词”，不让游戏名把泛化评测带进来
            keys.extend(item.get("aliases_zh") or [])
            keys.extend(item.get("aliases_en") or [])
            title_blob = item.get("title", "") + " " + " ".join(
                (item.get("aliases_zh") or []) + (item.get("aliases_en") or []))
            keys.extend(t for t in toks if t in title_blob)
        else:
            scored = [(t, self.bm25.idf.get(t, 0.0)) for t in toks]
            scored = [(t, v) for t, v in scored if v > 0]
            if scored:
                top = max(v for _, v in scored)
                keys.extend(t for t, v in scored if v >= 0.8 * top)
            if not keys:
                keys = toks[:2]
        return list(dict.fromkeys(k for k in keys if len(k) >= 2))[:limit]

    @staticmethod
    def _contains_any(texts: np.ndarray, terms: list[str]) -> np.ndarray:
        if not terms:
            return np.zeros(len(texts), dtype=bool)
        rx = re.compile("|".join(
            rf"\b{re.escape(t)}\b" if not CJK.search(t) else re.escape(t) for t in terms), re.IGNORECASE)
        return np.array([bool(rx.search(t)) for t in texts], dtype=bool)

    # ---- 评测检索 -----------------------------------------------------
    def search_reviews(self, query: str, patch_id: str, item_id: str | None = None,
                       language: str | None = None, phase: str | None = None,
                       sentiment: str | None = None, top_k: int = 5, min_chars: int = 20,
                       prefer_zh: bool = False) -> dict:
        if patch_id not in {p["patch_id"] for p in self.catalog.patches}:
            return {"status": "unknown_patch", "quotes": []}
        item = None
        if item_id:
            item = next((it for it in self._patch_items.get(patch_id, []) if it["item_id"] == item_id), None)
            if item is None:
                return {"status": "unknown_item", "quotes": []}
        query = (query or "").strip()
        if not query and not item:
            return {"status": "query_required", "quotes": []}

        mask = self.mask(patch_id, language, phase)
        if sentiment in ("negative", "positive"):
            up = self.docs["voted_up"].to_numpy()
            mask = mask * (1.0 - up if sentiment == "negative" else up).astype(np.float32)
        if mask.sum() == 0:
            return {"status": "no_candidates", "quotes": [], "candidates": 0}

        q_tokens = [t for t in tokenize(query) if len(t) >= 2] or tokenize(query)
        if item:
            q_tokens += [t for t in tokenize(item["title"]) if len(t) >= 2]
        bm = self.bm25.scores(q_tokens, mask=mask)
        qv = self._encode_query(query or (item["title"] if item else ""))
        dn = np.maximum(self.vectors @ qv, 0.0) * mask

        def norm(a: np.ndarray) -> np.ndarray:
            mx = float(a.max())
            return a / mx if mx > 0 else a

        score = 0.7 * norm(bm) + 0.3 * norm(dn)
        if prefer_zh:
            lang = self.docs["language_std"].to_numpy()
            zh = np.array([1.0 if str(x).startswith("zh") else 0.0 for x in lang], dtype=np.float32)
            score = score * (1.0 + 0.15 * zh)
        score = score * mask

        matched = self.docs["matched_items"].fillna("").to_numpy()
        if item:
            hit = np.array([1.0 if item["item_id"] in str(x).split(",") else 0.0 for x in matched],
                           dtype=np.float32)
            score = score * (1.0 + 0.25 * hit)

        long_enough = self.docs["text"].fillna("").str.len().to_numpy() >= min_chars
        score = np.where(long_enough, score, 0.0)

        k = max(1, min(60, int(mask.sum())))
        cand = np.argpartition(-score, min(k, len(score) - 1))[:k]
        cand = cand[np.argsort(-score[cand])]
        cand = [int(i) for i in cand if score[i] > 0]

        # 硬门槛：必须命中问题里的关键实体词（或条目别名），否则宁缺毋滥
        keys = self._key_terms(query, item)
        kept = []
        for i in cand:
            if keys and not self._contains_any(self._texts[[i]], keys)[0]:
                continue
            kept.append(i)
            if len(kept) >= top_k:
                break

        quotes = []
        for i in kept:
            row = self.docs.iloc[i]
            quotes.append({
                "doc_id": int(row.doc_id),
                "review_id": str(row.review_id),
                "appid": str(row.appid),
                "patch_id": str(row.patch_id),
                "phase": str(row.phase),
                "phase_label": PHASE_LABEL.get(str(row.phase), str(row.phase)),
                "language_std": str(row.language_std),
                "voted_up": bool(row.voted_up),
                "created_at": str(row.created_at),
                "votes_up": int(row.votes_up),
                "playtime_hours": round(float(row.playtime_hours), 1),
                "text": str(row.text),
                "snippet": snippet(str(row.text), keys + [query]),
                "url": str(row.url),
                "matched_items": [x for x in str(row.matched_items).split(",") if x],
                "score": round(float(score[i]), 4),
            })
        status = "quotes" if quotes else "no_quotes"
        return {
            "status": status,
            "candidates": int(mask.sum()),
            "kept": len(quotes),
            "key_terms": keys[:6],
            "item_filter": item["item_id"] if item else None,
            "quotes": quotes,
            "item": {"item_id": item["item_id"], "title": item["title"]} if item else None,
        }

    # ---- 已复核引文 ---------------------------------------------------
    def search_verified(self, query: str, patch_id: str, item_id: str | None = None,
                        phase: str | None = None, language: str | None = None,
                        sentiment: str | None = None, top_k: int = 3,
                        prefer_zh: bool = False) -> dict:
        pool = self._verified_by_patch.get(patch_id, [])
        if not pool:
            return {"status": "no_verified_quotes", "available": 0, "quotes": []}

        def keep(v: dict) -> bool:
            if item_id and v["item_id"] != item_id:
                return False
            if phase and phase not in ("all", "") and v["phase"] != phase:
                return False
            if language and language not in ("all", ""):
                if language == "chinese" and not str(v["language_std"]).startswith("zh"):
                    return False
                if language == "en" and v["language_std"] != "en":
                    return False
            if sentiment == "negative" and v["voted_up"]:
                return False
            if sentiment == "positive" and not v["voted_up"]:
                return False
            return True

        idx = [i for i in pool if keep(self.verified[i])]
        if not idx:
            return {"status": "no_verified_quotes", "available": 0, "quotes": []}

        tokens = [t for t in tokenize(query) if len(t) >= 2]
        if tokens and self._verified_bm25 is not None:
            mask = np.zeros(len(self.verified), dtype=np.float32)
            mask[idx] = 1.0
            scores = self._verified_bm25.scores(tokens, mask=mask)
        else:
            scores = np.zeros(len(self.verified), dtype=np.float32)
            for i in idx:
                scores[i] = float(self.verified[i].get("score") or 0.0)

        def rank_key(i: int) -> tuple[float, int]:
            s = float(scores[i])
            if prefer_zh and str(self.verified[i]["language_std"]).startswith("zh"):
                s *= 1.2
            return (-s, -(self.verified[i]["created_ts"] or 0))

        order = sorted(idx, key=rank_key)
        quotes = []
        for i in order[:top_k]:
            v = self.verified[i]
            quotes.append({
                "review_id": v["review_id"], "appid": v["appid"], "patch_id": v["patch_id"],
                "phase": v["phase"], "phase_label": PHASE_LABEL.get(v["phase"], v["phase"]),
                "language_std": v["language_std"], "voted_up": v["voted_up"],
                "created_at": v["created_at"], "text": v["evidence"], "snippet": v["evidence"][:240],
                "url": v["url"], "item_id": v["item_id"], "item_title": v["item_title"],
                "verification": v["verification"], "verified": True, "score": float(scores[i]),
                "matched_items": [v["item_id"]],
            })
        return {"status": "verified_quotes", "available": len(idx), "quotes": quotes}

    # ---- 代表性评测 ---------------------------------------------------
    def top_reviews(self, patch_id: str, phase: str | None = None, sentiment: str | None = None,
                    language: str | None = None, limit: int = 5) -> dict:
        mask = self.mask(patch_id, language, phase)
        if sentiment in ("negative", "positive"):
            up = self.docs["voted_up"].to_numpy()
            mask = mask * (1.0 - up if sentiment == "negative" else up).astype(np.float32)
        idx = np.flatnonzero(mask > 0)
        if len(idx) == 0:
            return {"status": "no_candidates", "quotes": []}
        long_enough = self.docs["text"].fillna("").str.len().to_numpy() >= 30
        idx = np.array([i for i in idx if long_enough[i]])
        if len(idx) == 0:
            return {"status": "no_candidates", "quotes": []}
        votes = self.docs["votes_up"].to_numpy()[idx]
        order = idx[np.argsort(-votes)][:limit]
        quotes = []
        for i in order:
            row = self.docs.iloc[int(i)]
            quotes.append({
                "doc_id": int(row.doc_id), "review_id": str(row.review_id), "appid": str(row.appid),
                "patch_id": str(row.patch_id), "phase": str(row.phase),
                "phase_label": PHASE_LABEL.get(str(row.phase), str(row.phase)),
                "language_std": str(row.language_std), "voted_up": bool(row.voted_up),
                "created_at": str(row.created_at), "votes_up": int(row.votes_up),
                "playtime_hours": round(float(row.playtime_hours), 1), "text": str(row.text),
                "snippet": snippet(str(row.text), []), "url": str(row.url),
                "matched_items": [x for x in str(row.matched_items).split(",") if x],
                "score": float(row.votes_up),
            })
        return {"status": "quotes", "quotes": quotes}

    # ---- 公告检索 -----------------------------------------------------
    def _build_item_index(self) -> None:
        self._item_docs: list[tuple[dict, list[str]]] = []
        for it in self.catalog.items:
            text = " ".join([it["title"], it.get("title_en", ""), it.get("description", ""),
                             " ".join(it.get("aliases_zh") or []), " ".join(it.get("aliases_en") or [])])
            self._item_docs.append((it, tokenize(text)))
        self._item_bm25 = BM25Index().build([t for _, t in self._item_docs])

    def search_announcements(self, query: str, appid: str | None = None,
                             patch_id: str | None = None, top_k: int = 5) -> dict:
        mask = np.ones(len(self._item_docs), dtype=np.float32)
        for i, (it, _) in enumerate(self._item_docs):
            if patch_id and it["patch_id"] != patch_id:
                mask[i] = 0.0
            if appid and str(it["appid"]) != str(appid):
                mask[i] = 0.0
        hits = self._item_bm25.top_k(tokenize(query), k=top_k, mask=mask) if query.strip() else []
        if not hits:
            hits = [(i, 0.0) for i, (it, _) in enumerate(self._item_docs)
                    if (not patch_id or it["patch_id"] == patch_id)
                    and (not appid or str(it["appid"]) == str(appid))][:top_k]
        out = []
        for i, s in hits:
            it = self._item_docs[i][0]
            out.append({
                "item_id": it["item_id"], "patch_id": it["patch_id"], "appid": str(it["appid"]),
                "title": it["title"], "title_en": it.get("title_en", ""),
                "description": it.get("description", ""), "section": it.get("section", ""),
                "aliases_zh": it.get("aliases_zh") or [], "aliases_en": it.get("aliases_en") or [],
                "source_url": it.get("source_url", ""), "score": round(float(s), 4),
            })
        return {"status": "ok" if out else "none", "items": out}

    def item_candidates(self, text: str, appid: str, patch_id: str, top_k: int = 3) -> dict:
        mask = np.zeros(len(self._item_docs), dtype=np.float32)
        for i, (it, _) in enumerate(self._item_docs):
            if it["patch_id"] == patch_id and str(it["appid"]) == str(appid):
                mask[i] = 1.0
        if mask.sum() == 0:
            return {"status": "unknown_patch", "candidates": []}
        scores = self._item_bm25.scores(tokenize(text), mask=mask)
        low = text.lower()
        for i, (it, _) in enumerate(self._item_docs):
            if mask[i] == 0:
                continue
            for a in (it.get("aliases_zh") or []) + (it.get("aliases_en") or []):
                if a and len(a) >= 2 and a.lower() in low:
                    scores[i] += 3.0
        order = np.argsort(-scores)[: max(1, min(top_k, int(mask.sum())))]
        cands = []
        for i in order:
            if scores[i] <= 0:
                continue
            it = self._item_docs[int(i)][0]
            matched = [a for a in (it.get("aliases_zh") or []) + (it.get("aliases_en") or [])
                       if a and a.lower() in low]
            cands.append({"item_id": it["item_id"], "title": it["title"],
                          "score": round(float(scores[i]), 3), "matched_aliases": matched})
        return {"status": "ok" if cands else "none", "candidates": cands}

    # ---- 向量 ---------------------------------------------------------
    def _encode_query(self, text: str) -> np.ndarray:
        from .dense import HashEncoder, load_encoder

        if not hasattr(self, "_encoder"):
            self._encoder = (load_encoder() if self.encoder_meta.get("backend") == "bge"
                             else HashEncoder(self.encoder_meta["dim"]))
        return self._encoder.encode([text], is_query=True)[0].astype(np.float32)
