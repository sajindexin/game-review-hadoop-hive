# -*- coding: utf-8 -*-
"""
构建两层标签体系：通用主题(11类) + 具体更新条目(75项)

输入：优先从仓库相对路径查找黄静雯版本建模分支的 items.jsonl / patches.jsonl；
      找不到时用已提交的 data/taxonomy 重建（保证 PR 快照中可运行）。
      可用 --source-dir / --items / --patches / --parquet 覆盖。
输出：
  data/taxonomy/items_enriched.json   75项条目（含通用主题归属、匹配词）
  data/taxonomy/patches.json          26个版本窗口
  data/taxonomy/appid_game_map.json   appid -> 中文游戏名
  data/taxonomy/item_dict.csv         Hive 更新条目字典表（带引号的标准 CSV）
  code/item_lexicon.py                规则引擎/RBT3 共用的条目词典（内嵌，无运行时外部依赖）

设计要点：
  1. 条目是"游戏特定"的：每项只属于某一款游戏，匹配时按 appid 约束候选集，
     避免"姜岛"被误匹配到别的游戏评论。
  2. 每个条目归属一个通用主题（泛化层），形成 具体条目 -> 通用主题 的映射。
  3. 匹配词 = 中文标题 + 中文别名 + 英文标题 + 英文别名（别名已含繁体）。
"""
from __future__ import annotations
import os
import json
import csv
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
REPO_ROOT = os.path.normpath(os.path.join(ROOT, ".."))
TAX = os.path.join(ROOT, "data", "taxonomy")

# 仅在找不到仓库内版本源时使用的本机兜底路径（不作为必填前提）
LOCAL_FALLBACK_DIR = r"C:\Users\hgj\Desktop\大数据大作业\版本建模分支\game-update-feedback-analysis\黄静雯_全组版本与改动"
LOCAL_FALLBACK_PARQUET = r"D:\QQFiles\team_package_v1\team_package_v1\游戏评论交付\releases\v1\data\cleaned_reviews.parquet"


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", default="",
                    help="版本源目录（含 items/items.jsonl 与 patches/patches.jsonl）；相对路径按模块仓库根解析")
    ap.add_argument("--items", default="", help="直接指定 items.jsonl，覆盖 --source-dir")
    ap.add_argument("--patches", default="", help="直接指定 patches.jsonl，覆盖 --source-dir")
    ap.add_argument("--parquet", default="", help="清洗评论 parquet（仅用于补游戏中文名，可缺失）")
    return ap.parse_args()


def _abs_from_root(p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(ROOT, p))


def resolve_source_dir(explicit=""):
    candidates = []
    if explicit:
        candidates.append(_abs_from_root(explicit))
    candidates.extend([
        os.path.join(ROOT, "data", "version_source"),
        os.path.join(ROOT, "version_source"),
        os.path.join(REPO_ROOT, "version_source"),
        os.path.join(REPO_ROOT, "版本建模分支", "game-update-feedback-analysis", "黄静雯_全组版本与改动"),
        os.path.join(REPO_ROOT, "..", "版本建模分支", "game-update-feedback-analysis", "黄静雯_全组版本与改动"),
        os.path.join(REPO_ROOT, "game-update-feedback-analysis", "黄静雯_全组版本与改动"),
        os.path.join(REPO_ROOT, "..", "game-update-feedback-analysis", "黄静雯_全组版本与改动"),
        REPO_ROOT,
        ROOT,
        LOCAL_FALLBACK_DIR,
    ])
    for d in candidates:
        if (os.path.exists(os.path.join(d, "items", "items.jsonl")) and
                os.path.exists(os.path.join(d, "patches", "patches.jsonl"))):
            return d
    return ""


def resolve_parquet(explicit=""):
    candidates = []
    if explicit:
        candidates.append(_abs_from_root(explicit))
    candidates.extend([
        os.path.join(ROOT, "data", "cleaned_reviews.parquet"),
        os.path.join(REPO_ROOT, "data", "cleaned_reviews.parquet"),
        LOCAL_FALLBACK_PARQUET,
    ])
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return ""

# appid -> 中文游戏名（本地标注工具展示用）
GAME_ZH = {
    "252490": "Rust(腐蚀)", "413150": "星露谷物语", "105600": "泰拉瑞亚",
    "1245620": "艾尔登法环", "2358720": "黑神话:悟空", "271590": "GTA5",
    "359550": "彩虹六号:围攻", "4000": "Garry's Mod", "440": "军团要塞2",
    "550": "求生之路2", "570": "DOTA2", "730": "CS2",
}

# item_id -> 通用主题（人工按条目语义映射，依据 description/section）
ITEM2ASPECT = {
    # Rust 深海/世界/丛林更新 (19)
    "D01": "graphics", "D02": "content", "D03": "level_design", "D04": "monetization",
    "W01": "level_design", "W02": "level_design", "W03": "level_design",
    "W04": "level_design", "W05": "level_design",
    "J01": "level_design", "J02": "content", "J03": "controls", "J04": "level_design",
    "J05": "level_design", "J06": "content", "J07": "monetization", "J08": "content",
    "J09": "content", "J10": "performance",
    # 星露谷物语 (16)
    "S14_01": "level_design", "S14_02": "content", "S14_03": "content",
    "S14_04": "content", "S14_05": "story",
    "S15_01": "content", "S15_02": "content", "S15_03": "level_design",
    "S15_04": "level_design", "S15_05": "multiplayer",
    "S16_01": "level_design", "S16_02": "content", "S16_03": "content",
    "S16_04": "multiplayer", "S16_05": "content", "S16_06": "content",
    # 泰拉瑞亚 (4)
    "105600_20200516_01": "content", "105600_20200516_02": "controls",
    "105600_20220928_01": "content", "105600_20220928_02": "content",
    # 艾尔登法环 (4)
    "1245620_20221207_01": "multiplayer", "1245620_20221207_02": "graphics",
    "1245620_20240620_01": "content", "1245620_20240620_02": "controls",
    # 黑神话:悟空 (4)
    "2358720_20240923_01": "controls", "2358720_20240923_02": "controls",
    "2358720_20241214_01": "content", "2358720_20241214_02": "level_design",
    # GTA5 (4)
    "271590_20230613_01": "content", "271590_20230613_02": "content",
    "271590_20240625_01": "content", "271590_20240625_02": "content",
    # 彩虹六号 (4)
    "359550_20231206_01": "content", "359550_20231206_02": "level_design",
    "359550_20240312_01": "content", "359550_20240312_02": "controls",
    # Garry's Mod (4)
    "4000_20240313_01": "content", "4000_20240313_02": "content",
    "4000_20250326_01": "content", "4000_20250326_02": "graphics",
    # 军团要塞2 (4)
    "440_20171020_01": "level_design", "440_20171020_02": "content",
    "440_20230712_01": "level_design", "440_20230712_02": "monetization",
    # 求生之路2 (4)
    "550_20200924_01": "content", "550_20200924_02": "content",
    "550_20210708_01": "performance", "550_20210708_02": "controls",
    # Dota2 (4)
    "570_20230420_01": "level_design", "570_20230420_02": "content",
    "570_20240523_01": "content", "570_20240523_02": "content",
    # CS2 (4)
    "730_20240207_01": "content", "730_20240207_02": "content",
    "730_20241002_01": "monetization", "730_20241002_02": "monetization",
}


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# 歧义别名黑名单：这些词在中文评论里是高频普通词，作为官方条目的匹配词会大量误命中，
# 故剔除，仅保留条目标题本身作为匹配依据。
AMBIGUOUS_TERMS = {
    "连战", "復戰", "連戰", "复战", "挑战", "地图", "关卡", "更新", "模式",
    "大师", "旅途", "宠物", "书商", "电影", "农场", "节", "活动",
}


def build_match_terms(r):
    """匹配词：标题 + 别名，去重；剔除歧义短词，去掉过短(<2)的词。"""
    terms = []
    for k in ("title", "title_en"):
        v = (r.get(k) or "").strip()
        if v:
            terms.append(v)
    for k in ("aliases_zh", "aliases_en"):
        terms.extend([x.strip() for x in (r.get(k) or []) if x.strip()])
    seen, out = set(), []
    for t in terms:
        tl = t.lower()
        if tl in AMBIGUOUS_TERMS:
            continue
        if tl not in seen and len(t) >= 2:
            seen.add(tl)
            out.append(t)
    # 长词优先
    out.sort(key=lambda x: -len(x))
    return out


def get_appid_game_map(patches):
    m = {}
    for p in patches:
        m[str(p["appid"])] = p.get("game", "")
    return m


def get_game_name_zh(parquet_path=""):
    """从 parquet 读 appid -> game_name（只读一个行组）；文件缺失时返回空字典。"""
    if not parquet_path or not os.path.exists(parquet_path):
        return {}
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(parquet_path)
        t = pf.read_row_group(0, columns=["appid", "game_name"]).to_pydict()
        m = {}
        for a, g in zip(t["appid"], t["game_name"]):
            if a and g:
                m[str(a)] = g
        return m
    except Exception as e:
        print("  [warn] 无法从 parquet 读游戏中文名:", e)
        return {}


def main():
    args = parse_args()
    os.makedirs(TAX, exist_ok=True)

    src_dir = resolve_source_dir(args.source_dir)
    items_src = _abs_from_root(args.items) if args.items else (
        os.path.join(src_dir, "items", "items.jsonl") if src_dir else "")
    patches_src = _abs_from_root(args.patches) if args.patches else (
        os.path.join(src_dir, "patches", "patches.jsonl") if src_dir else "")

    use_raw = bool(items_src and patches_src and os.path.exists(items_src) and os.path.exists(patches_src))
    if use_raw:
        print(f"版本源: {src_dir or os.path.dirname(items_src)}")
        items = load_jsonl(items_src)
        patches = load_jsonl(patches_src)
        missing = [r["item_id"] for r in items if r["item_id"] not in ITEM2ASPECT]
        if missing:
            print("[ERROR] 以下条目未映射通用主题:", missing)
            return
    else:
        print("[warn] 未找到 items.jsonl / patches.jsonl，改用已提交的 taxonomy 重建词典。")
        items = json.load(open(os.path.join(TAX, "items_enriched.json"), encoding="utf-8"))
        patches = json.load(open(os.path.join(TAX, "patches.json"), encoding="utf-8"))

    game_map = get_appid_game_map(patches)
    game_zh = get_game_name_zh(resolve_parquet(args.parquet))

    enriched = []
    for r in items:
        iid = r["item_id"]
        enriched.append({
            "item_id": iid,
            "patch_id": r.get("patch_id", ""),
            "appid": r.get("appid"),
            "game_en": game_map.get(str(r.get("appid")), ""),
            "game_zh": game_zh.get(str(r.get("appid")), ""),
            "title": r.get("title", ""),
            "title_en": r.get("title_en", ""),
            "description": r.get("description", ""),
            "section": r.get("section", ""),
            "aliases_zh": r.get("aliases_zh", []),
            "aliases_en": r.get("aliases_en", []),
            "annotation_note": r.get("annotation_note", ""),
            "aspect": ITEM2ASPECT[iid],
            "match_terms": build_match_terms(r),
        })

    def dump(obj, name):
        p = os.path.join(TAX, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        print("  写出", p, f"({os.path.getsize(p)//1024} KB)")

    final_game_map = {**game_map, **game_zh, **GAME_ZH}
    dump(enriched, "items_enriched.json")
    dump(patches, "patches.json")
    dump(final_game_map, "appid_game_map.json")

    # Hive 条目字典表：标准 CSV（带引号、含表头，OpenCSVSerde 可直接读入）
    item_dict_path = os.path.join(TAX, "item_dict.csv")
    with open(item_dict_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "title", "appid", "game", "aspect", "description"])
        for r in enriched:
            w.writerow([
                r["item_id"], r.get("title", ""), r.get("appid", ""),
                final_game_map.get(str(r.get("appid")), ""),
                r.get("aspect", ""), r.get("description", ""),
            ])
    print("  写出", item_dict_path, f"({os.path.getsize(item_dict_path)//1024} KB)")

    # 生成内嵌词典 item_lexicon.py
    items_by_id = {r["item_id"]: r for r in enriched}
    appid_items = {}
    for r in enriched:
        appid_items.setdefault(str(r["appid"]), []).append(r["item_id"])

    header = (
        "# -*- coding: utf-8 -*-\n"
        '"""自动生成的条目词典（由 build_item_taxonomy.py 产出，请勿手改）。\n\n'
        "两层标签体系：具体更新条目(75) -> 通用主题(11)。\n"
        "条目按 appid 归属游戏；匹配时务必按 appid 约束候选集，避免跨游戏误匹配。\n"
        '"""\n'
        "from __future__ import annotations\n\n"
    )
    jd = lambda o: json.dumps(o, ensure_ascii=False, indent=2)
    body = (
        "ITEMS = " + jd(items_by_id) + "\n\n"
        "APPID_ITEMS = " + jd(appid_items) + "\n\n"
        "ITEM2ASPECT = " + jd(ITEM2ASPECT) + "\n\n"
        "APPID_GAME = " + jd(final_game_map) + "\n\n\n"
        "def candidate_items(appid):\n"
        '    """返回该游戏的候选条目 id 列表。"""\n'
        "    return APPID_ITEMS.get(str(appid), [])\n"
    )
    lex_path = os.path.join(HERE, "item_lexicon.py")
    with open(lex_path, "w", encoding="utf-8") as f:
        f.write(header + body)
    print("  写出", lex_path, f"({os.path.getsize(lex_path)//1024} KB)")

    print("\n=== 两层标签体系构建完成 ===")
    print(f"条目数: {len(enriched)}, 版本数: {len(patches)}, 游戏数: {len(appid_items)}")
    from collections import Counter
    c = Counter(r["aspect"] for r in enriched)
    print("条目归属通用主题分布:")
    for k, v in sorted(c.items(), key=lambda x: -x[1]):
        print(f"  {k:<15} {v}")


if __name__ == "__main__":
    main()
