"""构建条目同义词表：人工重点词 + LLM 批量扩词 + 语料校验。

用法：
    set GRA_LLM_API_KEY=...
    python -m tools.build_lexicon --items data_sources/catalog/items.jsonl \
        --corpus data/corpus.parquet --out data_sources/lexicon
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import httpx
import pandas as pd

GENERIC = set("""
更新 新增 玩家 游戏 支持 加入 调整 内容 系统 模式 功能 版本 修复 优化 增加 可以 使用 以及 现在
已经 通过 进行 问题 之后 之前 同时 部分 包括 相关 官方 本次 地图 武器 任务 装备 角色 事件
建筑 物品 界面 显示 时间 数据 主题 这个 那个 我们 你们 他们 自己 就是 还是 但是 因为 所以
如果 虽然 而且 然后 什么 怎么 为什么 如何 多少 哪些 哪个 一个 一下 没有 不是 也是 可能
""".split())

MANUAL: dict[str, dict] = {
    "D01": {"synonyms": ["海浪", "水面", "浪花", "水花", "海面", "water", "waves", "ocean"], "negative": ["出海", "航海"]},
    "D02": {"synonyms": ["拖船", "拖轮", "tugboat", "tug boat", "tug"], "negative": []},
    "D03": {"synonyms": ["渡轮码头", "码头", "渡轮", "ferry terminal", "ferry"], "negative": []},
    "D04": {"synonyms": ["深渊皮肤", "潜水服", "深渊包", "外观包", "skin pack"], "negative": []},
    "S15_01": {"synonyms": ["姜岛", "薑島", "姜岛探险", "ginger island", "island farm"], "negative": ["出海"]},
    "S15_02": {"synonyms": ["金核桃", "金色核桃", "golden walnut", "walnut"], "negative": []},
    "S15_03": {"synonyms": ["火山地牢", "火山", "火山洞", "volcano dungeon", "volcano"], "negative": []},
    "S15_04": {"synonyms": ["海滩农场", "沙滩农场", "beach farm", "beach"], "negative": []},
    "S15_05": {"synonyms": ["分屏", "本地合作", "本地联机", "split screen", "local co-op"], "negative": []},
    "S16_04": {"synonyms": ["八人联机", "8人联机", "八人", "多人联机", "multiplayer", "co-op"], "negative": []},
    "730_20241002_01": {"synonyms": ["军械库", "通行证", "武库通行证", "军械库通行证", "armory pass", "battle pass"], "negative": []},
    "730_20241002_02": {"synonyms": ["挂件", "吊饰", "挂饰", "武器挂件", "charm", "charms"], "negative": ["外挂", "开挂"]},
    "359550_20231206_02": {"synonyms": ["巢穴", "巢穴地图", "新地图", "lair", "lair map"], "negative": []},
    "359550_20240312_02": {"synonyms": ["盾牌", "盾", "持盾", "盾牌重做", "shield"], "negative": []},
    "2358720_20241214_02": {"synonyms": ["行旅图", "旅行图", "地图", "travel map", "map"], "negative": []},
    "2358720_20241214_01": {"synonyms": ["土地庙", "连战", "挑战", "boss 连战", "shrine", "gauntlet"], "negative": []},
    "2358720_20240923_01": {"synonyms": ["百眼魔君", "百眼", "魔君", "boss", "hundred-eyed"], "negative": []},
    "2358720_20240923_02": {"synonyms": ["狼牙棒", "法力上限", "蓝量", "mana", "wolf fang"], "negative": []},
    "570_20230420_01": {"synonyms": ["地图变大", "地图扩展", "新地图", "map", "map size"], "negative": []},
    "570_20230420_02": {"synonyms": ["全才", "全才英雄", "universal", "hero attribute"], "negative": []},
    "570_20240523_01": {"synonyms": ["先天技能", "先天", "innate", "innate ability"], "negative": []},
    "570_20240523_02": {"synonyms": ["命石", "英雄命石", "facet", "facets"], "negative": []},
    "4000_20250326_01": {"synonyms": ["treason", "叛徒", "资源挂载"], "negative": []},
    "4000_20250326_02": {"synonyms": ["着色器", "shader", "shaders"], "negative": []},
    "steam_105600_20200516_01": {"synonyms": ["旅途模式", "旅行模式", "journey mode", "journey"], "negative": []},
    "steam_105600_20200516_02": {"synonyms": ["大师模式", "master mode", "master"], "negative": []},
    "steam_105600_20220928_01": {"synonyms": ["装备配置", "配装", "loadout", "loadouts"], "negative": []},
    "steam_105600_20220928_02": {"synonyms": ["碎石机", "装饰工具", "rubblemaker", "decor"], "negative": []},
    "steam_1245620_20221207_01": {"synonyms": ["竞技场", "斗技场", "arena", "colosseum"], "negative": []},
    "steam_1245620_20221207_02": {"synonyms": ["发型", "新发型", "hairstyle", "hair"], "negative": []},
    "steam_1245620_20240620_01": {"synonyms": ["最近物品", "最近获得", "物品栏", "recent items"], "negative": []},
    "steam_1245620_20240620_02": {"synonyms": ["灵马", "骑马", "艾尔登之兽", "mount", "horse"], "negative": []},
    "steam_271590_20230613_01": {"synonyms": ["颠覆计划", "查理里德", "mercenaries", "missions"], "negative": []},
    "steam_271590_20230613_02": {"synonyms": ["洛圣都天使", "天使行动", "san andreas"], "negative": []},
    "steam_271590_20240625_01": {"synonyms": ["保金", "执法事务所", "赏金", "bounty", "bail office"], "negative": []},
    "steam_271590_20240625_02": {"synonyms": ["警车", "警务派遣", "police", "dispatch"], "negative": []},
}

GLOBAL_GROUPS = [
    {"canonical": "crash", "terms": ["闪退", "崩溃", "报错", "闪退报错", "crash", "crashes", "crashing"]},
    {"canonical": "performance", "terms": ["卡顿", "掉帧", "帧数", "优化", "性能", "lag", "stutter", "fps"]},
    {"canonical": "multiplayer", "terms": ["联机", "多人", "组队", "合作", "co-op", "multiplayer"]},
    {"canonical": "map", "terms": ["地图", "新地图", "map", "maps"]},
    {"canonical": "story", "terms": ["剧情", "故事", "叙事", "story", "narrative"]},
    {"canonical": "difficulty", "terms": ["难度", "太难", "简单", "难度调整", "difficulty", "hard"]},
    {"canonical": "price", "terms": ["价格", "售价", "贵", "便宜", "price", "pricing"]},
    {"canonical": "content", "terms": ["内容", "更新内容", "新内容", "content", "update"]},
    {"canonical": "bug", "terms": ["bug", "漏洞", "故障", "问题", "bugs"]},
    {"canonical": "balance", "terms": ["平衡", "数值", "强度", "nerf", "buff", "balance"]},
]


def clean_terms(terms, aliases: set[str], corpus: str, require_corpus: bool = True) -> list[str]:
    out = []
    for t in terms or []:
        if not isinstance(t, str):
            continue
        t = t.strip().lower()
        if len(t) < 2 or len(t) > 20 or t.isdigit():
            continue
        if t in GENERIC or t in aliases:
            continue
        if require_corpus and t not in corpus:
            continue
        if t not in out:
            out.append(t)
    return out[:12]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", default="data_sources/catalog/items.jsonl")
    ap.add_argument("--corpus", default="data/corpus.parquet")
    ap.add_argument("--out", default="data_sources/lexicon")
    args = ap.parse_args()

    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=True)
    items = [json.loads(l) for l in Path(args.items).read_text("utf-8").splitlines() if l.strip()]
    docs = pd.read_parquet(args.corpus, columns=["text"])
    corpus = " ".join(docs["text"].fillna("").astype(str)).lower()

    raw_path = root / "llm_raw.json"
    generated: dict[str, dict] = {}
    if raw_path.exists():
        generated = json.loads(raw_path.read_text("utf-8"))
        print(f"reuse LLM cache: {len(generated)} items", flush=True)
    else:
        api_key = os.environ.get("GRA_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if api_key:
            base = (os.environ.get("GRA_LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
            model = os.environ.get("GRA_LLM_MODEL") or "deepseek-chat"
            system = (
                "你在为游戏版本问答系统补检索词。对每个改动条目，输出玩家可能使用的检索词。"
                "只输出 JSON 对象：{\"item_id\": {\"synonyms\": [...], \"negative\": [...]}}。"
                "synonyms 8-12 个，中文为主，可含英文、缩写、玩家俗称；不要泛词（如 更新/游戏/内容）。"
                "negative 3-5 个容易混淆但不属于该改动的词。不要解释。"
            )
            with httpx.Client(timeout=90.0, trust_env=False) as client:
                for i in range(0, len(items), 10):
                    batch = items[i:i + 10]
                    payload_items = [{
                        "item_id": it["item_id"], "title": it["title"], "title_en": it.get("title_en", ""),
                        "description": it.get("description", ""),
                        "aliases": it.get("aliases_zh", []) + it.get("aliases_en", []),
                        "note": it.get("annotation_note", ""),
                    } for it in batch]
                    resp = client.post(f"{base}/chat/completions", headers={
                        "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                    }, json={
                        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(payload_items, ensure_ascii=False)},
                        ],
                    })
                    resp.raise_for_status()
                    content = resp.json()["choices"][0]["message"]["content"]
                    data = json.loads(re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content.strip()))
                    for k, v in data.items():
                        if isinstance(v, dict):
                            generated[k] = v
                    print(f"LLM batch {i // 10 + 1}: {len(batch)} items", flush=True)
            raw_path.write_text(json.dumps(generated, ensure_ascii=False), encoding="utf-8")
        else:
            print("WARN no API key, only manual lexicon")

    rows = []
    total = 0
    for it in items:
        iid = it["item_id"]
        base = MANUAL.get(iid, {})
        gen = generated.get(iid, {})
        aliases = {a.lower() for a in (it.get("aliases_zh") or []) + (it.get("aliases_en") or [])}
        manual_syn = clean_terms(base.get("synonyms") or [], aliases, corpus, require_corpus=False)
        llm_syn = clean_terms(gen.get("synonyms") or [], aliases, corpus, require_corpus=False)
        synonyms = list(dict.fromkeys([*manual_syn, *llm_syn]))[:12]
        manual_neg = clean_terms(base.get("negative") or [], aliases, corpus, require_corpus=False)
        llm_neg = clean_terms(gen.get("negative") or [], aliases, corpus, require_corpus=False)
        negative = list(dict.fromkeys([*manual_neg, *llm_neg]))[:6]
        rows.append({"item_id": iid, "patch_id": it["patch_id"], "synonyms": synonyms,
                     "negative": negative, "source": "manual+llm" if gen else "manual"})
        total += len(synonyms)

    with (root / "items_extra.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (root / "global_synonyms.json").write_text(
        json.dumps({"groups": GLOBAL_GROUPS}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"items={len(rows)} synonyms={total} -> {root}")


if __name__ == "__main__":
    main()
