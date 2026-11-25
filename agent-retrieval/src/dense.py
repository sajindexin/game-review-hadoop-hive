"""向量编码器：默认离线哈希向量，可选接 BGE（sentence-transformers）。

交付口径是 BM25 + BGE 混合检索。BGE 需要额外安装 sentence-transformers 并
下载模型（约 100MB）；本机离线时自动降级为“哈希词袋向量”，接口保持一致，
只是语义泛化能力弱一些。切换方式见 README。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .tokenize import tokenize

DEFAULT_DIM = 512


class HashEncoder:
    """把 token 哈希到固定维度并做 L2 归一化，零依赖、可复现。"""

    name = "hash-512"

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in tokenize(text):
            h = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "little")
            sign = 1.0 if (h >> 63) & 1 else -1.0
            v[h % self.dim] += sign
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def encode(self, texts: list[str], batch_size: int = 256, is_query: bool = False) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            out[i] = self._vec(t)
        return out

    def meta(self) -> dict:
        return {"name": self.name, "dim": self.dim, "backend": "hash"}


class BGEEncoder:
    """BGE（默认 bge-small-zh-v1.5），需要 sentence-transformers。"""

    def __init__(self, model_path: str | Path) -> None:
        from sentence_transformers import SentenceTransformer  # 延迟导入

        self.model = SentenceTransformer(str(model_path))
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.name = f"bge:{Path(model_path).name}"

    def encode(self, texts: list[str], batch_size: int = 64, is_query: bool = False) -> np.ndarray:
        prefix = "为这个句子生成表示以用于检索相关文章：" if is_query else ""
        arr = self.model.encode(
            [prefix + t for t in texts] if prefix else texts,
            batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False,
        )
        return np.asarray(arr, dtype=np.float32)

    def meta(self) -> dict:
        return {"name": self.name, "dim": self.dim, "backend": "bge"}


def load_encoder(prefer_bge: bool = True) -> HashEncoder | BGEEncoder:
    model = os.environ.get("GRA_BGE_MODEL", "")
    if prefer_bge and model and Path(model).exists():
        try:
            return BGEEncoder(model)
        except Exception as exc:  # pragma: no cover - 取决于本机依赖
            print(f"[dense] BGE 不可用（{exc}），降级为哈希向量")
    return HashEncoder()


def save_vectors(path: Path, vectors: np.ndarray, meta: dict) -> None:
    np.savez_compressed(path, vectors=vectors.astype(np.float16), meta=json.dumps(meta, ensure_ascii=False))


def load_vectors(path: Path) -> tuple[np.ndarray, dict]:
    with np.load(path, allow_pickle=False) as z:
        vectors = z["vectors"].astype(np.float32)
        meta = json.loads(str(z["meta"]))
    return vectors, meta
