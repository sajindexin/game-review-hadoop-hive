"""评测条目解析：同义/变体/错字/多语言问法能不能命中正确改动。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.retrieval import KnowledgeBase  # noqa: E402


def main() -> int:
    kb = KnowledgeBase(ROOT / "data")
    agent = Agent(kb)
    cases = [json.loads(l) for l in (ROOT / "eval" / "paraphrase.jsonl").read_text("utf-8").splitlines() if l.strip()]
    hit1 = hit3 = 0
    failures = []
    for c in cases:
        patch = kb.catalog.patch(c["patch_id"])
        items = agent._resolve_items(c["question"], patch)
        ids = [it["item_id"] for it in items]
        exp = c["expected"]
        if exp is None:
            ok1 = "730_20241002_02" not in ids[:1]
            ok3 = "730_20241002_02" not in ids[:3]
        else:
            ok1 = bool(ids) and ids[0] == exp
            ok3 = exp in ids[:3]
        hit1 += ok1
        hit3 += ok3
        if not ok3:
            failures.append((c["question"], exp, ids))
    n = len(cases)
    print(f"cases={n} Hit@1={hit1}/{n} ({hit1/n:.1%}) Hit@3={hit3}/{n} ({hit3/n:.1%})")
    for q, exp, ids in failures:
        print("MISS", q, "expected", exp, "got", ids[:3])
    return 0 if hit3 / n >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
