# -*- coding: utf-8 -*-
"""
英文词典（主题关键词 + 情感词 + 否定/程度/转折词）
与 lexicon.py（中文）配套，主题 ID 与中文版一一对应（11 类）。

匹配约定：英文关键词一律按「词边界」匹配（\\bkw\\b，忽略大小写），
避免子串误伤（如 art 误中 part/start）；由 rule_labeler 按语言路由。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. 主题关键词（英文，键 = lexicon.ASPECTS 的主题 ID）
# ---------------------------------------------------------------------------
ASPECT_KEYWORDS_EN: dict[str, list[str]] = {
    "content": [
        "content", "contents", "dlc", "expansion", "new content", "new map",
        "new boss", "new mode", "new character", "new weapon", "new event",
        "new season", "season pass", "gameplay", "game play", "playability",
        "replayability", "replay value", "content update", "free update",
        "content update", "endgame", "endgame content", "postgame",
    ],
    "level_design": [
        "map", "maps", "map design", "level design", "level", "levels",
        "chapter", "chapters", "exploration", "explore", "open world",
        "waypoint", "guidance", "guide", "backtracking", "invisible wall",
        "maze", "navigation", "getting lost", "world design", "pacing",
    ],
    "multiplayer": [
        "multiplayer", "co-op", "coop", "co op", "online", "online mode",
        "server", "servers", "dedicated servers", "lag", "laggy", "ping",
        "latency", "matchmaking", "disconnect", "disconnects",
        "disconnected", "disconnecting", "connection",
        "netcode", "team up", "party", "lobby", "ranked", "crossplay",
    ],
    "graphics": [
        "graphics", "art style", "art direction", "art", "visuals", "visual",
        "texture", "textures", "lighting", "rendering", "scenery", "scenic",
        "environment art", "character model", "models", "pixel art",
        "animation", "animations", "cinematic", "cinematics", "ray tracing",
    ],
    "performance": [
        "performance", "optimization", "optimisation", "optimized", "fps",
        "frames", "framerate", "frame rate", "stutter", "stutters",
        "stuttering", "crash", "crashes", "crashing", "crashed", "bug",
        "bugs", "buggy", "glitch", "glitches", "glitchy", "black screen",
        "loading", "load times", "freeze", "freezes", "freezing", "memory",
        "vram", "won't launch", "not launch", "startup", "stability",
        "stable", "runs", "running", "driver", "settings",
    ],
    "story": [
        "story", "storyline", "plot", "narrative", "lore", "writing",
        "dialogue", "dialog", "ending", "endings", "characters", "character",
        "protagonist", "villain", "main quest", "worldbuilding", "script",
        "voice acting", "cutscene", "cutscenes",
    ],
    "controls": [
        "controls", "control scheme", "combat", "combat system", "combat feel",
        "game feel", "dodge", "dodging", "parry", "parrying", "difficulty",
        "hard", "too hard", "challenging", "boss fight", "boss", "bosses",
        "combo", "combos", "keybind", "keybinds", "keybinding", "controller",
        "gamepad", "keyboard", "input", "inputs", "input lag", "hitbox",
        "hitboxes",
    ],
    "monetization": [
        "price", "pricing", "priced", "overpriced", "pay", "paying",
        "pay-to-win", "pay to win", "p2w", "microtransaction",
        "microtransactions", "microtransactions", "monetization", "worth",
        "worth it", "not worth", "refund", "refunded", "refunding", "sale",
        "discount", "cash grab", "cashgrab", "greedy", "greed", "skin",
        "skins", "cosmetics", "battle pass", "full price", "deluxe",
        "expensive",
    ],
    "cheating": [
        "cheat", "cheats", "cheater", "cheaters", "cheating", "hacker",
        "hackers", "hacking", "cheat engine", "anticheat", "anti-cheat",
        "ban", "banned", "bans", "ban wave", "false ban", "unfair",
        "unbalanced", "balance", "balanced", "balancing", "smurf", "smurfs",
        "script", "scripts", "scripting", "report", "reported", "modder",
        "modders",
    ],
    "audio": [
        "music", "soundtrack", "ost", "score", "sound", "sounds", "sfx",
        "audio", "sound design", "bgm", "voice", "voice acting", "vo",
        "vocal", "song", "songs", "theme song",
    ],
    "community": [
        "developer", "developers", "devs", "dev", "studio", "publisher",
        "support", "customer support", "update", "updates", "patch",
        "patches", "patch notes", "community", "forum", "forums",
        "communication", "roadmap", "devs listen", "player base",
        "playerbase", "fanbase", "mod support", "modding",
    ],
}

# ---------------------------------------------------------------------------
# 2. 英文情感词典
# ---------------------------------------------------------------------------
POSITIVE_WORDS_EN = [
    "fun", "funny", "great", "amazing", "awesome", "excellent", "fantastic",
    "wonderful", "brilliant", "masterpiece", "best", "love", "loved",
    "loving", "enjoy", "enjoyed", "enjoyable", "enjoying", "good", "solid",
    "polished", "smooth", "beautiful", "gorgeous", "stunning", "immersive",
    "addictive", "engaging", "recommend", "recommended", "worth it",
    "impressive", "satisfying", "incredible", "perfect", "gem", "hidden gem",
    "refreshing", "banger", "peak", "goat", "gem", "epic", "legendary",
    "flawless", "top notch", "top-tier", "next level", "must play",
    "must-play", "well made", "well done", "nailed it", "nailed",
    "respect", "genius", "clever", "cozy", "chill", "relaxing", "hyped",
    "outdid themselves", "outdid", "step up", "step up from", "improved",
    "fixed", "much better", "way better", "so much better",
]

NEGATIVE_WORDS_EN = [
    "bad", "terrible", "awful", "horrible", "horrid", "worst", "boring",
    "bored", "tedious", "repetitive", "dull", "disappointing",
    "disappointed", "disappointment", "letdown", "let down", "broken",
    "buggy", "crashes", "crashing", "crashed", "laggy", "lagging",
    "stuttering", "stutters", "unplayable", "trash", "garbage", "garbage",
    "shit", "crap", "dogshit", "mid", "mediocre", "bland", "shallow",
    "hollow", "empty", "grindy", "p2w", "pay to win", "cash grab",
    "cashgrab", "scam", "ripoff", "rip-off", "refund", "refunded",
    "frustrating", "frustrated", "annoying", "infuriating", "miserable",
    "painful", "headache", "waste of time", "waste of money", "wasted",
    "clunky", "jank", "janky", "unbalanced", "unfair", "dead game",
    "disconnect", "disconnects", "disconnected", "disconnecting",
    "dead on arrival", "abandoned", "soulless", "lazy", "half-baked",
    "unfinished", "overpriced", "overrated", "overhyped", "downgrade",
    "downgraded", "regression", "regressed", "worse", "worsened", "ruined",
    "ruining", "destroyed", "killed the game", "avoid", "skip", "do not buy",
    "don't buy", "dont buy", "regret", "ugly", "lackluster", "underwhelming",
    "generic", "soulless", "predatory", "scummy", "greedy", "unoptimized",
    "poorly optimized", "poorly", "hot mess", "mess", "hot garbage",
    "dog water", "cringe", "painful to play",
    "uninstalled", "uninstall", "quit", "quitting", "stopped playing",
    "stopped", "dropped", "gave up", "gave up on", "can't recommend",
    "cant recommend", "not recommend", "don't recommend", "not worth",
    "bad port", "console port", "shader compilation", "memory leak",
]

# 否定词（英文）：出现在情感词前一定窗口内翻转极性
NEGATIONS_EN = [
    "not", "no", "never", "hardly", "barely", "cannot", "can't", "cant",
    "don't", "dont", "doesn't", "doesnt", "didn't", "didnt", "isn't",
    "isnt", "aren't", "arent", "wasn't", "wasnt", "weren't", "werent",
    "won't", "wont", "wouldn't", "wouldnt", "nothing", "neither", "without",
    "lack", "lacks", "lacking", "zero", "is not", "was not",
]

# 程度副词（英文）：提升置信度
INTENSIFIERS_EN = [
    "very", "really", "extremely", "super", "incredibly", "absolutely",
    "totally", "so", "such", "quite", "pretty", "highly", "insanely",
    "unbelievably", "completely", "utterly", "truly", "genuinely",
]

# 转折词（英文）：作为分句切分点
CONTRAST_WORDS_EN = ["but", "however", "although", "though", "yet"]

# 分句切分正则（中英通用；英文按词边界，避免误切 button/other 等词）
CONTRAST_SPLIT_RE_EN = r"\b(but|however|although|though|yet)\b"
