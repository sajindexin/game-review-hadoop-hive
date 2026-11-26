"""纯 numpy 实现的 BM25（Okapi），带倒排索引与可选的候选集掩码。

索引规模按“版本窗口内样本评测”设计（数万条），单次查询只需对查询词
累加分数向量，无需遍历全部文档。
"""
from __future__ import annotations

import math
import pickle
from collections import defaultdict
from pathlib import Path

import numpy as np

K1 = 1.2
B = 0.75


class BM25Index:
    def __init__(self, k1: float = K1, b: float = B) -> None:
        self.k1 = k1
        self.b = b
        self.n_docs = 0
        self.avgdl = 0.0
        self.doc_len: np.ndarray = np.zeros(0, dtype=np.float32)
        self.postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.idf: dict[str, float] = {}

    def build(self, docs: list[list[str]]) -> "BM25Index":
        self.n_docs = len(docs)
        lengths = np.fromiter((len(d) for d in docs), dtype=np.float32, count=self.n_docs)
        self.doc_len = lengths
        self.avgdl = float(lengths.mean()) if self.n_docs else 0.0

        raw: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for doc_id, tokens in enumerate(docs):
            counts: dict[str, int] = {}
            for t in tokens:
                counts[t] = counts.get(t, 0) + 1
            for term, tf in counts.items():
                raw[term].append((doc_id, tf))

        postings: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        idf: dict[str, float] = {}
        for term, pairs in raw.items():
            ids = np.fromiter((p[0] for p in pairs), dtype=np.int32, count=len(pairs))
            tfs = np.fromiter((p[1] for p in pairs), dtype=np.float32, count=len(pairs))
            postings[term] = (ids, tfs)
            df = len(pairs)
            idf[term] = math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))
        self.postings = postings
        self.idf = idf
        return self

    def scores(self, query_tokens: list[str], mask: np.ndarray | None = None) -> np.ndarray:
        scores = np.zeros(self.n_docs, dtype=np.float32)
        if self.n_docs == 0:
            return scores
        norm = self.k1 * (1.0 - self.b + self.b * self.doc_len / (self.avgdl or 1.0))
        for term in query_tokens:
            posting = self.postings.get(term)
            if posting is None:
                continue
            ids, tfs = posting
            denom = tfs + norm[ids]
            contrib = self.idf[term] * tfs * (self.k1 + 1.0) / denom
            scores[ids] += contrib
        if mask is not None:
            scores = scores * mask
        return scores

    def top_k(self, query_tokens: list[str], k: int = 5, mask: np.ndarray | None = None,
              min_score: float = 0.0) -> list[tuple[int, float]]:
        scores = self.scores(query_tokens, mask=mask)
        if self.n_docs == 0:
            return []
        k = max(1, min(k, self.n_docs))
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [(int(i), float(scores[i])) for i in idx if scores[i] > min_score]

    # ---- 持久化 -------------------------------------------------------
    def save(self, path: Path) -> None:
        payload = {
            "k1": self.k1, "b": self.b, "n_docs": self.n_docs, "avgdl": self.avgdl,
            "doc_len": self.doc_len, "postings": self.postings, "idf": self.idf,
        }
        with Path(path).open("wb") as f:
            pickle.dump(payload, f, protocol=5)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with Path(path).open("rb") as f:
            payload = pickle.load(f)
        idx = cls(k1=payload["k1"], b=payload["b"])
        idx.n_docs = payload["n_docs"]
        idx.avgdl = payload["avgdl"]
        idx.doc_len = payload["doc_len"]
        idx.postings = payload["postings"]
        idx.idf = payload["idf"]
        return idx
