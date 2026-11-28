# Steam 评测分析台 · 前端

复刻 Steam 商店 / 社区视觉语言的静态前端，用来演示「选游戏和版本 → 查指标 → 提问 → 看引文 → 点开原文」这条完整链路。
离线页面使用仓库内的导出数据包。版本 API 通过公共 Hive 服务从数据仓库取数。

## 目录

```
steam-review-web/
├─ index.html            商店总览：焦点更新、游戏胶囊墙（带筛选）、26 个版本一览表
├─ patch.html            版本详情：概览 / 改动条目 / 评测样本 / 问答入口 四个标签页
├─ nlp.html              NLP 洞察：11 类主题情感分布 + 75 项官方改动反馈统计
├─ ask.html              Agent 问答页（走 /api/ask，接口不可用时自动回退本地规则引擎）
├─ server.py             本地服务：静态站 + 数据接口 + POST /api/ask
├─ backend/
│  ├─ common.py          统一错误类型 ApiError
│  ├─ dataset.py         静态数据集 → JSON 接口（读 data/*.js 并原样返回）
│  └─ routes.py          GET 路由分发 + 错误映射
├─ assets/
│  ├─ steam.css          Steam 风格样式（#171a21 / #1b2838 / #66c0f4 配色）
│  ├─ steam.js           公共库：导航、程序化封面、格式化、SVG 图表、原文链接
│  ├─ index.js           首页逻辑（筛选 / 排序 / 搜索）
│  ├─ patch.js           详情页逻辑（趋势、引文展开、评论筛选）
│  └─ ask.js             问答页逻辑
├─ data/
│  ├─ index.js           12 款游戏 / 26 个版本的清单
│  ├─ verified.js        与 Agent 同源的已复核关联和 Steam 原文链接
│  ├─ patches/*.js       每个版本一个数据包（趋势、阶段统计、改动条目、评论样本）
│  └─ nlp/
│     ├─ topics.js       11 类主题情感：语种 × 阶段 × 版本/游戏 四维切片
│     └─ items.js        75 项官方改动的正/负/中性统计与窗口分布
├─ screenshots/          页面截图（本地规则引擎模式）
├─ screenshots-joint/    与检索 Agent 联调的五个步骤截图
├─ screenshots-nlp/      NLP 洞察页截图
├─ docs/
│  ├─ NLP前端展示说明.md  11 类主题 + 75 项改动的前端落点、口径与验证记录
│  ├─ 联调记录-唐宇杰.md  与 agent-retrieval 的真实联调记录（第 7 章用这份）
│  ├─ joint/*.json       联调时四类问题的完整响应
│  ├─ 联调记录.md        本地自测记录
│  └─ resp-*.json        本地规则引擎的五类响应
├─ test_server.py        问答引擎单元测试
├─ test_dataset_api.py   数据接口与源文件一致性测试
└─ tools/
   ├─ export_web_data.py 从 parquet + 组内 CSV/JSONL 生成 data/ 的导出脚本
   ├─ export_nlp_data.py 从 NLP 模块结果生成 data/nlp/ 的导出脚本
   └─ test_nlp_page.js   用 CDP 驱动真实点击，验证 NLP 页的筛选与排序
```

## 启动步骤

**方式一：本地规则引擎（无需其它服务）**

```bash
cd steam-review-web
python server.py --port 8021
```

**方式二：接通检索 Agent（唐宇杰模块，联调用这个）**

```bash
# 终端 1：先启动对方模块
cd agent-retrieval
python -m src.cli serve --port 8000

# 终端 2：前端指定后端地址
cd steam-review-web
python server.py --port 8066 --agent-base http://127.0.0.1:8000
```

启动日志会打印当前问答后端与回退策略。后端挂掉时自动回退本地规则引擎，页面不白屏。

打开页面入口 <http://127.0.0.1:8066/index.html>。
同时具备问答接口 `POST /api/ask`，页面与接口同源，无需处理跨域。

只想看静态页、不跑接口时，`python -m http.server 8000` 或直接双击 `index.html` 也可以，
此时问答页会自动回退到本地规则引擎。

## Hive 版本 API

版本查询复用 [公共 Hive 客户端](../docs/hive-service.md)，直接读取 [已验收的 `hadoop_delivery` 表](../docs/hive-data-acceptance.md#3-版本目录hive-统计与模型证据)。按服务文档配置项目 Tailscale 与 Hive 后，在仓库根目录启动。

```powershell
uv sync --all-packages --locked
$env:HIVE_HOST = 'hive.hujiayuan.com'
$env:HIVE_PORT = '10000'
$env:HIVE_AUTH = 'NONE'
uv run --locked python steam-review-web/server.py --port 8021
```

| 入口 | 可选参数 | Hive 来源及返回内容 |
| --- | --- | --- |
| `GET /api/versions` | `appid` | `patches` 目录及 `patch_comparison` 的全语种 `summary` |
| `GET /api/versions/{patch_id}` | `appid` | 版号、上线日、窗口、公告和全语种摘要 |
| `GET /api/versions/{patch_id}/changes` | `appid`、`item_id` | `patch_items` 的中英文名称、别名、说明和公告链接 |
| `GET /api/versions/{patch_id}/stats` | `appid`、`language_group` | `patch_daily`、`patch_phase`、`patch_comparison` 的逐日、阶段和前后统计 |
| `GET /api/versions/{patch_id}/evidence` | `appid`、`item_id`、`phase`、`language_group`、`offset`、`limit` | `verified_quotes` 的原句、复核标记、公告、Steam 原页和分页 |

**主题分析与情感统计（第四部分，`nlp_topic` 库，实时查 Hive，`source: "hive"`，60 秒缓存）**

| 入口 | 可选参数 | Hive 来源及返回内容 |
| --- | --- | --- |
| `GET /api/nlp/stats/distribution` | `lang`（zh/en，默认 zh） | `topic_distribution` / `topic_distribution_en`：11 类主题 × 正/负/中 计数 |
| `GET /api/nlp/stats/summary` | `appid`、`lang` | `topic_sentiment_review_summary`：评论总数、命中主题数、主情感分布、正/负主题对合计 |
| `GET /api/nlp/topics/trend` | `appid`、`aspect`、`lang`、`days`（7–3650，默认 365） | `topic_sentiment_detail`：主题情感按日计数（`days` 控制返回天数） |
| `GET /api/nlp/items/feedback` | `appid`、`item_id` | `item_sentiment_detail` + `item_patch_summary`：改动反馈。不给 `item_id` 返回该游戏全部条目情感汇总；给 `item_id` 返回单条目情感分布 + `patch_id` 版本窗口计数（pre_14d / release_day / post_14d / out_of_window，与第三部分版本目录对齐） |
| `GET /api/nlp/reviews` | `appid`、`aspect`、`sentiment`、`lang`、`offset`、`limit`（默认 50、最大 500） | `topic_sentiment_detail`：按主题+情感筛评论，带 `evidence` 引文，按置信度降序分页 |

- `aspect` 为 11 类主题 ID：content / level_design / multiplayer / graphics / performance / story / controls / monetization / cheating / audio / community；`sentiment` 为 positive / negative / neutral。输入错误 400，条目未命中 404。
- `lang=en` 时自动切换英文结果表（`topic_sentiment_detail_en` 等）；英文为规则弱标注口径，供探索性统计。
- 页面已在用的静态端点 `GET /api/nlp/topics`、`GET /api/nlp/items` 保持不变（离线数据包，可协商缓存）。

- `language_group` 默认 `all`。推荐率取 0–1 的比例，分母为零时返回 `null`。`change_percentage_points` 采用 Hive 表原精度，星露谷物语 1.5 的全语种前后差显示为 `+0.96` 个百分点。
- 引文 `phase=all` 查询完整复核库。指定 `before`、`release` 或 `after` 时，按所选阶段的编辑时间截止规则筛选。`chinese` 汇总简中和繁中。`available` 为筛选后的总数，`offset` 默认 0，`limit` 默认 100、最大 500。
- 成功响应标有 `source: "hive"`。无引文时返回空数组。输入错误为 400，找不到版本、改动或统计语种为 404。连接异常及超时沿用公共错误结构。小查询使用公共客户端的 60 秒缓存。

```text
GET /api/health/hive
GET /api/versions?appid=413150
GET /api/versions/sdv_20201221_15/changes?item_id=S15_01
GET /api/versions/sdv_20201221_15/stats?language_group=all
GET /api/versions/sdv_20201221_15/evidence?item_id=S15_01&phase=after
```

## 页面入口

| 入口 | 地址 | 内容 |
| --- | --- | --- |
| 商店总览 | `/index.html` | 焦点更新、游戏胶囊墙、筛选工具条、26 个版本一览表 |
| 按口碑筛选 | `/index.html?q=星露谷` | 关键词过滤游戏与版本表 |
| 版本详情 | `/patch.html?id=sdv_20201221_15` | 统计卡、推荐率趋势、每日评论量、三阶段、分语种 |
| 改动与引文 | `/patch.html?id=sdv_20201221_15&item=S15_01` | 展开指定改动条目的引文面板 |
| 评测样本 | `/patch.html?id=sdv_20201221_15#reviews` | 按阶段 / 极性筛选，每条带 Steam 原文链接 |
| NLP 洞察（全局） | `/nlp.html` | 11 类主题情感分布 + 75 项官方改动统计 |
| NLP 洞察（按版本） | `/nlp.html?patch=sdv_20201221_15&lang=zh&phase=after` | 单版本 + 语种 + 更新阶段的切片 |
| Agent 问答 | `/ask.html?patch=sdv_20201221_15` | 选版本、提问、看引文、点原文 |

## 数据来源与口径

| 内容 | 来源 |
| --- | --- |
| 评测明细（4,884,580 条） | `team_package_v1/游戏评论交付/releases/v1/data/cleaned_reviews.parquet` |
| 阶段 / 逐日 / 前后对比统计 | `game-update-feedback-analysis/黄静雯_全组Hive统计/*.csv` |
| 版本与改动条目 | `game-update-feedback-analysis/黄静雯_全组版本与改动/{patches,items}/*.jsonl` |
| 游戏评论总数 | `team_package_v1/游戏评论交付/releases/v1/reports/game_counts.csv` |

- **窗口**：更新日前 14 天、更新当日、更新后 14 天（UTC 日期切分），与组内 Hive 统计完全一致。
- **推荐率**：`voted_up = true` 的评论数 ÷ 窗口内有效评论数，缺失 `voted_up` 不计入。
- **两种评论口径**（页面上均有标注，报告里请用 Hive 口径）：
  - `window_reviews_total`：Hive 口径，三阶段有效评论合计，对应 `分阶段统计.csv`；
  - `pool_reviews`：其中正文长度 40–1400 的可分析样本数，用于抽取引文与评测样本。
- **改动条目提及数**：官方条目 + 组内人工别名表，对评测正文做不区分大小写的子串匹配。
  只衡量「该改动有没有被说」，不代表「说好还是说坏」，页面标注为近似指标。
- **评论样本**：每版本从窗口内取 200 条高赞 + 700 条随机，过滤 ASCII 艺术刷屏后，
  优先保留 7 条中文样本，再按阶段与极性补齐，约 27 条/版本。
- **原文链接**：`https://steamcommunity.com/profiles/<steamid>/recommended/<appid>/`。
  Steam 同一用户对同一游戏只有一条评测，用 `steamid + appid` 即可定位，实测返回 200。

## 重新生成数据

```bash
# 依赖：duckdb（pip install duckdb）
python steam-review-web/tools/export_web_data.py \
  --parquet team_package_v1/游戏评论交付/releases/v1/data/cleaned_reviews.parquet \
  --analysis game-update-feedback-analysis \
  --out steam-review-web/data
```

更新已复核引文库时，在 `agent-retrieval` 目录运行：

```powershell
python -m tools.build_knowledge `
  --parquet "<v1 发布包>/releases/v1/data/cleaned_reviews.parquet" `
  --out data `
  --web-verified-out ../steam-review-web/data/verified.js
```

详情页加载 `data/verified.js`，按版本和改动条目展示已复核原文。该文件与 Agent 复核索引使用同一批记录；点击 Agent 问答中的复核引文入口可以定位到详情页的对应评测。

## 接口契约

公共 Hive 服务连接配置见 [Hive 服务使用文档](../docs/hive-service.md)。

配置 Hive 后，可在仓库根目录通过 `uv run --locked python steam-review-web/server.py --port 8021` 启动联调服务。

### 数据读取接口（GET）

页面数据统一走接口，不再用 `<script src="data/*.js">` 注入全局变量。
接口只做「读出 → 原样返回」，不改变任何数据结构，所以前端渲染逻辑与旧版一致。

| 接口 | 说明 | 大小 | 缓存 |
| --- | --- | --- | --- |
| `GET /api/meta` | 轻量元信息：12 款游戏 + 26 个版本 ID 清单 | 1.7 KB | ETag |
| `GET /api/index` | 完整总览：游戏、版本、推荐率区间 | 9 KB | ETag |
| `GET /api/patch/<patch_id>` | 单个版本包：趋势、阶段统计、改动条目、评论样本 | 平均 23 KB | ETag |
| `GET /api/nlp/topics` | 11 类主题情感的四维切片 | 145 KB | ETag |
| `GET /api/nlp/items` | 75 项官方改动的正/负/中性统计 | 34 KB | ETag |
| `GET /api/verified` | 已复核引文索引（可用 `?patch_id=` 过滤到单版本） | 403 KB / 31 KB | ETag |
| `GET /api/health/hive` | Hive 探活 | 极小 | 不缓存 |

缓存策略：响应带弱 ETag（内容 SHA-256 前 32 位）+ `Cache-Control: no-cache`，
浏览器下次请求带 `If-None-Match`，命中返回 `304` 空体。数据更新后摘要变化，自动失效。

错误统一返回 JSON，HTTP 状态码与 `code` 对应：

```json
{ "error": { "code": "patch_not_found", "message": "找不到版本：nope" } }
```

| 场景 | 状态码 | code |
| --- | --- | --- |
| 未知路径 | 404 | `not_found` |
| 版本不存在 | 404 | `patch_not_found` |
| `patch_id` 含非法字符 | 400 | `invalid_patch_id` |
| 该接口不接受查询参数 | 400 | `invalid_request` |

`patch_id` 只允许 `[A-Za-z0-9_]`，`/api/patch/../server` 这类路径穿越会被拒绝（有单测覆盖）。

`/api/verified` 支持按版本过滤，这是接口相对静态脚本最实际的收益：

```
GET /api/verified?patch_id=sdv_20201221_15   →  31 KB（39 条）
GET /api/verified                            → 403 KB（469 条）
```

版本页只用一个版本的引文，过滤后省掉 92% 传输量。若传入未登记的 `patch_id` 返回空数组
（而不是 404）——这表示"该版本没有已复核引文"，属于正常业务状态。

### 取数回退

`assets/steam.js` 里的 `fetchJson()` 按运行环境自动切换：

| 打开方式 | 行为 |
| --- | --- |
| `http://` / `https://` | 走 `/api/*` 接口 |
| `file://` 双击打开 | 接口不可用，回退到 `data/*.js` 脚本注入 |

回退是为了保留离线单页能力——把整个目录拷走双击 `index.html` 也能看，不用起服务。
接口返回 404 时**不会**回退（那是业务错误，不是环境问题），会直接抛给页面显示。

### 问答接口（POST）

```
POST /api/ask
Content-Type: application/json

{ "patch_id": "sdv_20201221_15", "question": "中文玩家怎么看这次更新？" }

→ 200
{
  "intent": "language",
  "answer": "<p>可直接渲染的 HTML</p>",
  "stats": { "before_rate": 97.45, "release_rate": 100.0, "after_rate": 98.42,
             "delta_pp": 0.97, "window_reviews": 6076, "pool_reviews": 4597 },
  "citations": [
    { "review_id": "81959365", "steamid": "76561198318525543", "language": "zh-Hans",
      "voted_up": true, "created_at": "2020-12-10 10:10:00", "votes_up": 6314,
      "text": "…", "url": "https://steamcommunity.com/profiles/…/recommended/413150/" }
  ]
}
```

识别意图：`overall` / `language` / `items` / `trend` / `negative` / `evidence` / `topic` / `topic_change`。

连接检索 Agent 后即可查询主题分布及更新前后负面反馈变化。主题问答的响应含 `topic_stats`（结构化主题统计）和 `topic_citations`（NLP 数据出处）。

接 Java Servlet 时，把服务地址填进 `assets/ask.js` 的 `S.backendBase` 即可（`S.useBackend` 在 http(s) 下默认开启）：

```js
S.useBackend = true;
S.backendBase = "http://localhost:8080/review-api";
```

## 联调记录

- [`docs/联调记录-唐宇杰.md`](docs/联调记录-唐宇杰.md) —— **与检索 Agent（`feat/agent-retrieval`）的真实联调**，
  五个步骤的截图、接口实际响应、量纲与时序问题的修复记录、降级验证。第 7 章直接用这份。
- [`docs/联调记录.md`](docs/联调记录.md) —— 仅本地规则引擎的自测记录，保留备查。

## 已知限制

- 封面为程序化渐变色块，不含真实游戏美术资源，避免版权与外部依赖。
- 页面样式模仿 Steam 仅为课程演示，与 Valve 无关。
- 分语种统计中小语种样本可能只有几十条，页面已提示不宜直接下结论。
- 「金核桃解锁」「火山地牢」在 1.5 窗口内没有任何评测提及，页面如实显示 0，不做补数。
