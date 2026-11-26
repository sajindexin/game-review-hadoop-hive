"""版本目录与指标表：26 个版本、75 项官方改动、228 行窗口指标。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

LANG_GROUPS = {"all": "全部语言", "chinese": "中文", "en": "英文"}


class Catalog:
    def __init__(self, path: Path) -> None:
        raw = json.loads(Path(path).read_text("utf-8"))
        self.data_version: str = raw.get("data_version", "v1")
        self.generated_at: str = raw.get("generated_at", "")
        self.games: list[dict[str, Any]] = raw["games"]
        self.patches: list[dict[str, Any]] = raw["patches"]
        self.items: list[dict[str, Any]] = raw["items"]
        self.metrics: list[dict[str, Any]] = raw["metrics"]
        self.source: dict[str, Any] = raw.get("source", {})

        self._game_by_id = {g["appid"]: g for g in self.games}
        self._patch_by_id = {p["patch_id"]: p for p in self.patches}
        self._items_by_patch: dict[str, list[dict]] = {}
        for it in self.items:
            self._items_by_patch.setdefault(it["patch_id"], []).append(it)
        self._metrics_by_patch = {(m["patch_id"], m["language_group"]): m for m in self.metrics}
        self._by_game: dict[str, list[dict]] = {}
        for p in self.patches:
            self._by_game.setdefault(str(p["appid"]), []).append(p)

    # ---- 基础查询 -----------------------------------------------------
    def game(self, appid: str | int) -> dict | None:
        return self._game_by_id.get(str(appid))

    def games_sorted(self) -> list[dict]:
        return sorted(self.games, key=lambda g: g["game_name"])

    def patches_of(self, appid: str | int) -> list[dict]:
        return sorted(self._by_game.get(str(appid), []), key=lambda p: p["release_date"])

    def patch(self, patch_id: str) -> dict | None:
        return self._patch_by_id.get(patch_id)

    def items_of(self, patch_id: str) -> list[dict]:
        return self._items_by_patch.get(patch_id, [])

    def item(self, appid: str | int, item_id: str) -> dict | None:
        for it in self.items:
            if it["item_id"] == item_id and str(it["appid"]) == str(appid):
                return it
        return None

    def metrics_of(self, patch_id: str, language_group: str = "all") -> dict | None:
        return self._metrics_by_patch.get((patch_id, language_group))

    def metric_groups(self, patch_id: str) -> list[str]:
        return [g for g in LANG_GROUPS if (patch_id, g) in self._metrics_by_patch]

    def all_metrics(self, language_group: str = "all") -> list[dict]:
        return [m for m in self.metrics if m["language_group"] == language_group]

    def ranking(self, language_group: str = "all") -> list[dict]:
        rows = self.all_metrics(language_group)
        return sorted(rows, key=lambda m: m["change_percentage_points"])

    # ---- 文本解析 -----------------------------------------------------
    def resolve_patch(self, text: str, appid: str | int | None = None) -> dict | None:
        """从问题里识别版本：patch_id、版本号（1.12 / 7.33）、版本名或日期。"""
        text = text or ""
        for p in self.patches:
            if p["patch_id"] in text:
                return p
        pool = self.patches_of(appid) if appid else self.patches
        # 版本号：1.12 / 7.33 / 1.0.13.16669
        versions: list[tuple[str, dict]] = []
        for p in pool:
            for cand in {p.get("patch_name", ""), p.get("title_zh", "")}:
                m = re.search(r"\d+(?:\.\d+){1,3}", cand or "")
                if m:
                    versions.append((m.group(), p))
        for m in re.finditer(r"\d+(?:\.\d+){1,3}", text):
            v = m.group()
            for pv, p in versions:
                if v == pv:
                    return p
        for p in pool:
            for key in ("patch_name", "title_zh", "patch_id"):
                k = p.get(key) or ""
                if len(k) >= 3 and k.lower() in text.lower():
                    return p
        # 日期 2024-06-20
        for m in re.finditer(r"20\d{2}-\d{2}-\d{2}", text):
            for p in pool:
                if p["release_date"] == m.group():
                    return p
        return None
