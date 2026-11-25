"""命令行入口：ask / serve / meta。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent
from .retrieval import KnowledgeBase


def _kb(path: str) -> KnowledgeBase:
    return KnowledgeBase(Path(path))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="game-review-agent")
    ap.add_argument("--data", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ask = sub.add_parser("ask", help="问一个问题")
    p_ask.add_argument("question")
    p_ask.add_argument("--appid")
    p_ask.add_argument("--patch")
    p_ask.add_argument("--language", choices=["all", "chinese", "en"])
    p_ask.add_argument("--json", action="store_true")

    p_serve = sub.add_parser("serve", help="启动 HTTP 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    sub.add_parser("meta", help="查看知识库概况")

    args = ap.parse_args(argv)
    if args.cmd == "meta":
        kb = _kb(args.data)
        print(json.dumps(kb.meta, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "ask":
        kb = _kb(args.data)
        agent = Agent(kb)
        res = agent.ask(args.question, appid=args.appid, patch_id=args.patch, language=args.language)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(res["answer"])
            if res.get("numbers"):
                print("\n数字：")
                for n in res["numbers"]:
                    print(f"  - {n['label']}: {n.get('display', n.get('value'))}")
            if res.get("citations"):
                print("\n出处：")
                for c in res["citations"]:
                    print(f"  [{c['id']}] {c['type']} | {c['title']} | {c.get('url', '')}")
            print(f"\nstatus={res['status']}  evidence={res.get('evidence_status')}")
        return 0

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        app = create_app(args.data)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
