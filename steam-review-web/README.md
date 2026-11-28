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
├─ server.py             本地服务：静态站 + POST /api/ask（联调用，接口契约与 Servlet 一致）
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
