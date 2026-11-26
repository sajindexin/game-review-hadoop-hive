# 接口说明（给 Java Web / Web 端）

Base URL：`http://127.0.0.1:8000`（启动：`python -m src.cli serve --port 8000`）
所有请求/响应均为 `application/json; charset=utf-8`。交互式文档：`/docs`。

## 0. 健康检查

`GET /health`

```json
{"ok": true, "data_version": "v1", "docs": 61729, "encoder": "hash-512"}
```

## 1. 游戏与版本

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/games` | 12 款游戏，含全量评测数、版本数 |
| GET | `/api/games/{appid}/patches` | 某款游戏的 26 个代表版本中的子集，含前后推荐率 |
| GET | `/api/patches/{patch_id}` | 版本详情：版本信息 + 官方改动列表 + 三种语言分组的指标 |
| GET | `/api/patches/{patch_id}/metrics?language=all` | 单版本单分组指标 |

`/api/patches/{patch_id}` 返回示例（截断）：

```json
{
  "patch": {"patch_id": "sdv_20201221_15", "patch_name": "Stardew Valley 1.5",
            "release_date": "2020-12-21", "window_start": "2020-12-07", "window_end": "2021-01-04",
            "source_url": "https://www.stardewvalley.net/..."},
  "game": {"appid": "413150", "game_name": "星露谷物语"},
  "items": [{"item_id": "S15_01", "title": "姜岛探索", "description": "...", "aliases_zh": ["姜岛"]}],
  "metrics": {"all": {"before_rate": 0.9745, "after_rate": 0.9842, "change_percentage_points": 0.96, "...": "..."}}
}
```

## 2. 检索接口

### 2.1 查评测原文

`POST /api/retrieve/reviews`

```json
{
  "query": "玩家怎么评价姜岛",
  "patch_id": "sdv_20201221_15",
  "item_id": "S15_01",
  "language": "chinese",
  "phase": "after",
  "sentiment": "negative",
  "top_k": 5
}
```

必填：`patch_id`；`query` 与 `item_id` 至少给一个。

| 字段 | 取值 | 说明 |
|---|---|---|
| `phase` | `before` / `release` / `after` / `all` | 相对发布日的阶段 |
| `language` | `chinese` / `en` / `all` | `chinese` 含 zh-Hans 与 zh-Hant |
| `sentiment` | `negative` / `positive` / null | 按推荐/不推荐过滤 |
| `item_id` | 如 `S15_01` | 只为该改动找证据，会优先命中别名 |

响应：

```json
{
  "status": "quotes",
  "candidates": 2575,
  "kept": 3,
  "key_terms": ["姜岛"],
  "item_filter": "S15_01",
  "quotes": [{
    "review_id": "83372362", "language_std": "zh-Hans", "phase": "after",
    "voted_up": true, "created_at": "2020-12-28 03:15:25", "votes_up": 12,
    "snippet": "…还有去到姜岛后把背包里的东西一键归类就会闪退。",
    "url": "https://steamcommunity.com/profiles/76561199040786520/recommended/413150/",
    "matched_items": ["S15_01"], "score": 0.87
  }]
}
```

`status` 取值：`quotes` / `no_quotes`（检索到候选但没有命中实体词，**不应展示原文**）/
`unknown_patch` / `unknown_item` / `no_candidates` / `query_required`。

### 2.2 查官方公告

`POST /api/retrieve/announcements`

```json
{"query": "新地图", "patch_id": "steam_359550_20231206", "top_k": 3}
```

返回 `items[]`：`item_id` / `title` / `description` / `source_url` / `aliases_zh` / `score`。
`patch_id` 省略时在全部 75 项改动里检索。

### 2.3 候选改动条目（给关联模型联调）

`POST /api/items/candidates`

```json
{"text": "姜岛真好玩，但是火山地牢太绕了", "appid": "413150", "patch_id": "sdv_20201221_15", "top_k": 3}
```

```json
{"status": "ok", "candidates": [
  {"item_id": "S15_01", "title": "姜岛探索", "score": 7.42, "matched_aliases": ["姜岛"]},
  {"item_id": "S15_03", "title": "火山地牢", "score": 5.10, "matched_aliases": ["火山地牢"]}
]}
```

### 2.4 已复核引文（上游版本分析模块 469 条）

`POST /api/retrieve/verified`

```json
{"query": "姜岛", "patch_id": "sdv_20201221_15", "item_id": "S15_01", "phase": "after", "top_k": 3}
```

响应与 2.1 类似，但每条都带 `verified: true`、`verification`（如 `AI逐条核对原文`）和 `item_title`：

```json
{"status": "verified_quotes", "available": 5, "quotes": [{
  "review_id": "83814498", "language_std": "zh-Hans", "phase": "after",
  "voted_up": true, "created_at": "2021-01-02 12:00:00",
  "snippet": "这次1.5更新归来，也是日常地做一做公会打怪任务，探索探索姜岛…",
  "url": "https://steamcommunity.com/profiles/76561198366866825/recommended/413150/",
  "item_id": "S15_01", "item_title": "姜岛探索", "verification": "AI逐条核对原文", "verified": true
}]}
```

没有该版本/改动的已复核引文时返回 `{"status": "no_verified_quotes", "available": 0, "quotes": []}`。

阶段过滤与上游版本分析模块 `find_evidence.lookup` 的口径一致：

| phase | 额外排除 |
|---|---|
| `before` | `before_review_edited_after_release = true`（更新前发表、发布后才编辑） |
| `after` | `edited_after_window = true`（窗口结束后编辑） |
| `release` | `updated_ts // 86400 > created_ts // 86400`（发布日之后才编辑） |

469 条里有 55 条会被上述规则排除；同时要求查询实体词在引文里有命中（BM25 > 0），
否则返回 `no_verified_quotes`，不会拿“看起来最像”的引文凑数。

## 3. Agent 问答

`POST /api/agent/ask`

```json
{"question": "艾尔登法环 1.12 更新后推荐率是多少？", "appid": "1245620", "patch_id": "steam_1245620_20240620", "language": "all", "phase": null}
```

`appid` / `patch_id` 可省略：会从问题里识别（游戏名、版本号、版本名、日期）。
`language` 取 `all` / `chinese` / `en`；`phase` 取 `before` / `release` / `after` / `compare` / `all`
（也兼容 `release_day`），不传时由路由解析（“更新前 / 发布当天 / 更新后 / 更新前后”）。
**显式传参优先**：传了 `language` / `phase` 就按传入值执行，问题里的“英文/更新后”不会覆盖；
`phase=all` 表示不筛阶段，`language=all` 表示全部语言。
`compare` 会分别查 before 和 after 两段证据；语言和阶段会传入评测检索、已复核引文和差评回退三条路径。
响应里的 `route` 字段会回显本次路由（`intent / phases / item_ids / source`），便于联调排查。

响应：

```json
{
  "status": "answered",
  "question": "...",
  "appid": "1245620",
  "game": "ELDEN RING",
  "patch": {"patch_id": "steam_1245620_20240620", "patch_name": "Patch 1.12", "release_date": "2024-06-20"},
  "answer": "《ELDEN RING》Patch 1.12（2024-06-20）的推荐率：更新前 14 天 96.0%（4,657/4,851）…[1]",
  "numbers": [
    {"label": "更新后推荐率", "value": 0.825365, "display": "82.5%", "unit": "%", "sample": 4936, "language_group": "all"}
  ],
  "citations": [
    {"id": 1, "type": "metric", "title": "更新前后推荐率", "detail": "全部语言 · 更新前 14 天 / 发布当天 / 更新后 14 天",
     "source": "上游版本分析模块 Hive 窗口统计（数据版本 v1）", "url": "", "snippet": ""}
  ],
  "links": {"stats": "/versions/steam_1245620_20240620?lang=all",
            "evidence": "/versions/steam_1245620_20240620?tab=feedback",
            "announcement": "https://steamcommunity.com/games/1245620/announcements/detail/..."},
  "tools": [{"name": "lookup_metrics", "args": {"patch_id": "...", "language_group": "all"}, "status": "ok"}],
  "evidence_status": "none",
  "empty_state_text": "暂无已复核引文",
  "suggestions": ["Patch 1.12 更新后推荐率降了多少？", "..."]
}
```

### status 约定

| status | 含义 | 前端行为 |
|---|---|---|
| `answered` | 正常回答 | 渲染 `answer` + `numbers` + `citations` |
| `need_context` | 缺游戏/版本 | 展示 `answer`，把 `suggestions` 渲染成追问按钮 |
| `outside_catalog` | 超出数据范围（天气、退款、客服…） | 直接展示拒答话术 |
| `unsupported` | 有上下文但没有对应工具 | 提示可问的三类问题 |

### citations.type

| type | 内容 | 前端展示 |
|---|---|---|
| `metric` | 指标表出处；`url` 为空，用 `links.stats` 跳“查看统计” | 指标卡片 |
| `announcement` | 官方改动条目，`url` 为官方公告链接 | “查看公告” |
| `review` | 玩家原话，`url` 为 Steam 评测永久链接 | “查看原文” |

`links.evidence` 是第一个条目的原文入口（兼容旧版）；一个问题提到多个改动时，
额外返回 `links.evidence_items`，每个条目一个入口：

```json
"evidence_items": [
  {"item_id": "2358720_20241214_01", "title": "土地庙挑战",
   "url": "/versions/steam_2358720_20241214?tab=feedback&item=2358720_20241214_01"},
  {"item_id": "2358720_20241214_02", "title": "行旅图",
   "url": "/versions/steam_2358720_20241214?tab=feedback&item=2358720_20241214_02"}
]
```

### evidence_status
- `verified_quotes`：引文来自上游版本分析模块的 469 条已复核关联，`citations[].verified=true`，前端应显示“已复核”徽标；
- `mixed_evidence`：同一条回答里既有已复核引文、也有未复核原文（例如“更新前后”只有一段有复核库）。
  顶层不能标“已复核”，必须按 `citations[].verified` 逐条展示；
- `dataset_quotes`：引文来自 v1 数据集窗口样本（未复核），`citations[].verified=false`，可点链接回溯到 Steam 原页；
- `none`：没有可用引文，`answer` 里会出现 `empty_state_text`（“暂无已复核引文”）。

> 规则：某个版本只要存在已复核库，就优先用已复核引文；该改动没有就返回 `none`，不会退回未复核原文。
> 例外是“为什么差评变多”这类归因问题——已复核差评不足时，退回“对应阶段被顶得最多的差评”，
> 并在标题里标注“数据集原文（未复核）”；如果同一回答混合了两类证据，顶层返回 `mixed_evidence`。

`citations[]` 里和证据级别相关的字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `verified` | bool | 是否来自已复核库 |
| `verification` | string | 复核方式，如 `AI逐条核对原文` |
| `review_id` | string | Steam 评测 ID，可用于去重和回溯 |
| `snippet` | string | 已复核引文只截取**复核过的那一段**，不是整条评论 |
| `language_std` | string | 引文实际语种（`zh-Hans` / `en` / …），用于核对语言筛选 |
| `phase` | string | 引文实际阶段（`before` / `release` / `after`），用于核对阶段筛选 |
| `item_id` | string | 引文**实际关联**的官方改动；不是请求里指定的条目时以引文为准 |

## 4. 验收题

`GET /api/qa/examples` 返回 43 道题（含 `appid` / `patch_id`），可直接做成版本页的“示例问题”。

## 5. 错误码

| HTTP | 场景 |
|---|---|
| 400 | `/api/patches/{id}/metrics` 的 `language` 不在 all/chinese/en |
| 404 | `appid` / `patch_id` 不存在 |
| 422 | 请求体缺字段，或 `language` / `phase` 不在枚举内（FastAPI 校验） |
| 500 | 未预期异常（会带 traceback，联调时看服务端日志） |

## 6. Java 调用示例

```java
HttpClient client = HttpClient.newHttpClient();
String body = """
  {"question":"%s","appid":"%s","patch_id":"%s"}
""".formatted(question, appid, patchId);
HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create("http://127.0.0.1:8000/api/agent/ask"))
    .header("Content-Type", "application/json; charset=utf-8")
    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
    .build();
HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
```

注意：请求体必须按 UTF-8 编码，否则中文问题会乱码。
