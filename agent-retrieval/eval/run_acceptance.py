"""跑 20 道验收题，核对数字、出处和边界行为，输出报告。

用法：python eval/run_acceptance.py [--json eval/last_run.json] [--md eval/REPORT.md]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.retrieval import KnowledgeBase  # noqa: E402


def check(case: dict, res: dict) -> list[str]:
    exp = case.get("expect", {})
    errs: list[str] = []
    if exp.get("status") and res["status"] != exp["status"]:
        errs.append(f"status={res['status']} != {exp['status']}")
    answer = res.get("answer", "")
    for text in exp.get("contains", []):
        if text.lower() not in answer.lower():
            errs.append(f"缺少文字「{text}」")
    for text in exp.get("not_contains", []):
        if text.lower() in answer.lower():
            errs.append(f"不该出现文字「{text}」")
    if exp.get("links_evidence_contains"):
        link = str((res.get("links") or {}).get("evidence", ""))
        if exp["links_evidence_contains"] not in link:
            errs.append(f"原文链接缺少 {exp['links_evidence_contains']}：{link}")
    if exp.get("links_evidence_items_count") is not None:
        items = (res.get("links") or {}).get("evidence_items") or []
        if len(items) != exp["links_evidence_items_count"]:
            errs.append(f"evidence_items 数量 {len(items)} != {exp['links_evidence_items_count']}")
    for want in exp.get("numbers", []):
        hit = next((n for n in res.get("numbers", []) if n["label"] == want["label"]), None)
        if hit is None:
            errs.append(f"缺少数字项「{want['label']}」")
        elif hit.get("value") is None or abs(float(hit["value"]) - float(want["value"])) > float(want["tol"]):
            errs.append(f"{want['label']}={hit.get('value')} 期望 {want['value']}±{want['tol']}")
    if exp.get("min_citations") and len(res.get("citations", [])) < exp["min_citations"]:
        errs.append(f"引用数 {len(res.get('citations', []))} < {exp['min_citations']}")
    counts = Counter(c["type"] for c in res.get("citations", []))
    for kind, need in (exp.get("types") or {}).items():
        if counts.get(kind, 0) < need:
            errs.append(f"{kind} 引用 {counts.get(kind, 0)} < {need}")
    if exp.get("evidence"):
        if res.get("evidence_status") != exp["evidence"]:
            errs.append(f"evidence={res.get('evidence_status')} != {exp['evidence']}")
    if exp.get("verified_reviews"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        if not reviews or any(not c.get("verified") for c in reviews):
            errs.append("引文没有标记为已复核")
    if exp.get("review_negative"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        if not reviews or any("不推荐" not in c.get("detail", "") for c in reviews):
            errs.append("差评归因里出现了非差评引文")
    if exp.get("no_review_citations") and any(c["type"] == "review" for c in res.get("citations", [])):
        errs.append("不该出现玩家引文却出现了")
    if exp.get("review_languages"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        allowed = set(exp["review_languages"])
        if any(c.get("language_std") not in allowed for c in reviews):
            errs.append(f"引文语种不符：{[c.get('language_std') for c in reviews]}")
    if exp.get("review_phases"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        allowed = set(exp["review_phases"])
        if any(c.get("phase") not in allowed for c in reviews):
            errs.append(f"引文阶段不符：{[c.get('phase') for c in reviews]}")
    if exp.get("review_items"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        if any(c.get("item_id") != exp["review_items"] for c in reviews):
            errs.append(f"引文条目不符：{[c.get('item_id') for c in reviews]}")
    if exp.get("review_item_set"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        got = {c.get("item_id") for c in reviews}
        if got != set(exp["review_item_set"]):
            errs.append(f"引文条目集合不符：{sorted(got)}")
    if exp.get("review_ids"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        got = {c.get("review_id") for c in reviews}
        if got != set(exp["review_ids"]):
            errs.append(f"引文 ID 不符：{sorted(got)}")
    if exp.get("review_phases_include"):
        reviews = [c for c in res.get("citations", []) if c["type"] == "review"]
        got = {c.get("phase") for c in reviews}
        missing = set(exp["review_phases_include"]) - got
        if missing:
            errs.append(f"缺少阶段引文：{sorted(missing)}")
    # 全局质量检查
    for c in res.get("citations", []):
        if c["type"] == "review" and not str(c.get("url", "")).startswith("https://steamcommunity.com"):
            errs.append(f"引文 {c.get('review_id')} 缺少 Steam 链接")
    for c in res.get("citations", []):
        if c["type"] == "announcement" and not str(c.get("url", "")).startswith("http"):
            errs.append(f"公告 {c.get('item_id')} 缺少官方链接")
    if res["status"] == "answered" and not answer.strip():
        errs.append("回答为空")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(ROOT / "eval" / "last_run.json"))
    ap.add_argument("--md", default=str(ROOT / "eval" / "REPORT.md"))
    args = ap.parse_args()

    kb = KnowledgeBase(ROOT / "data")
    agent = Agent(kb)
    cases = [json.loads(l) for l in (ROOT / "eval" / "acceptance.jsonl").read_text("utf-8").splitlines() if l.strip()]

    results = []
    t0 = time.perf_counter()
    for case in cases:
        res = agent.ask(case["question"], appid=case.get("appid"), patch_id=case.get("patch_id"),
                        language=case.get("language"))
        errs = check(case, res)
        results.append({"case": case, "result": res, "errors": errs, "passed": not errs})
        mark = "PASS" if not errs else "FAIL"
        print(f"[{mark}] {case['id']:<28} {case['question']}")
        for e in errs:
            print(f"        - {e}")

    passed = sum(r["passed"] for r in results)
    elapsed = time.perf_counter() - t0
    print(f"\n{passed}/{len(results)} 通过，用时 {elapsed:.1f}s")
    if passed != len(results):
        print("失败用例：", [r["case"]["id"] for r in results if not r["passed"]])

    payload = {
        "passed": passed, "total": len(results), "seconds": round(elapsed, 2),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cases": [{"id": r["case"]["id"], "question": r["case"]["question"], "passed": r["passed"],
                   "errors": r["errors"], "status": r["result"]["status"],
                   "answer": r["result"]["answer"]} for r in results],
    }
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = ["# Agent 验收报告", "",
             f"- 用例：{len(results)} 道（指标 / 公告 / 原文 / 归因 / 边界 / 回归）",
             f"- 结果：**{passed}/{len(results)} 通过**",
             f"- 用时：{elapsed:.1f}s · 数据版本 v1",
             "", "| # | 用例 | 问题 | 结果 |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['case']['id']} | {r['case']['question']} | {'✅' if r['passed'] else '❌ ' + '；'.join(r['errors'])} |")
    Path(args.md).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
