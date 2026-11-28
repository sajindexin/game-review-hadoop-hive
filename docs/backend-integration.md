# Hive 后端接口与联调约定

适用于《基于 Hadoop 与 Hive 的游戏更新反馈分析智能系统》。本阶段在原有 `steam-review-web/server.py` 的静态站和问答服务上增加模块化 JSON API。公共 Hive 客户端安装、环境变量和 Python 调用方式见 [公共 Hive 数据访问层](hive-service.md)。

## 运行方式

在仓库根目录安装和设置环境变量后运行：

```powershell
python -m pip install -e .
$env:HIVE_HOST = 'hive.hujiayuan.com'
$env:HIVE_AUTH = 'NONE'
python steam-review-web/server.py --port 8066
```

浏览器页面入口为 `http://127.0.0.1:8066/index.html`。另行启动检索 Agent 后，可指定 `--agent-base http://127.0.0.1:8000`。不安装 Hive 包仍可用原方式启动离线页面和本地规则问答。

## HTTP 接口

各新增 GET 接口响应 `application/json; charset=utf-8`。成功响应直接返回表中描述的对象或数组，页面迁移时只需替换数据加载函数。请求路径使用现有 `patch_id`（ASCII 字母、数字、下划线及连字符），其余接口无需路径或查询参数。NLP 和版本总览首次加载聚合结果，当前的页面筛选逻辑可继续在浏览器中执行。

| 方法和路径 | 响应结构（与现有数据包内部 JSON 一致） | 实现文件 / 负责人 |
| --- | --- | --- |
| `GET /api/health/hive` | `{"status":"ok","source":"hive"}`，每次实际执行 `SELECT 1 AS ok` | `backend/routes.py` / 胡家源，已实现 |
| `GET /api/index` | `SRW_INDEX` 对象，参考 `data/index.js` | `backend/version_api.py::get_index` / 黄静雯 |
| `GET /api/patches/{patch_id}` | `SRW_PATCH` 对象，参考 `data/patches/sdv_20201221_15.js` | `backend/version_api.py::get_patch` / 黄静雯 |
| `GET /api/verified-quotes` | `SRW_VERIFIED` 数组，参考 `data/verified.js` | `backend/version_api.py::get_verified_quotes` / 黄静雯，与唐宇杰核对引文口径 |
| `GET /api/nlp/topics` | `SRW_NLP_TOPICS` 对象，参考 `data/nlp/topics.js` | `backend/nlp_api.py::get_topics` / 何国境 |
| `GET /api/nlp/items` | `SRW_NLP_ITEMS` 对象，参考 `data/nlp/items.js` | `backend/nlp_api.py::get_items` / 何国境 |
| `POST /api/ask` | 既有 `patch_id`、`question` 请求和 `intent`、`answer`、`stats`、`citations` 等响应 | `server.py` / 胡家源总体集成，唐宇杰维护 Agent 上游 |

`backend/version_api.py` 与 `backend/nlp_api.py` 已定义函数和路由，待各负责人实现查询时替换函数体。业务逻辑调用 `from .common import get_hive`，并使用 `get_hive().query(...)`，由公共层取得环境配置和连接。函数直接返回可 JSON 序列化的 `dict` 或 `list`。Hive 的日期转换成 ISO 字符串，ID 转成字符串，比例和数量采用 JSON 数值，空值用 `null`。字段缺失或非法响应会报错。示例：

```python
from .common import ApiError, get_hive

# 在业务函数内部使用受控 SQL 模板，示例为查询存在性。
rows = get_hive().query(
    "SELECT patch_id AS patch_id FROM steam_dw.dim_patch WHERE patch_id=%s",
    (patch_id,), max_rows=1, cache_ttl=60,
)
if not rows:
    raise ApiError(404, "not_found", "版本不存在")
```

新增 GET 接口的失败响应示例：

```json
{"error":{"code":"hive_timeout","message":"Hive 查询超时"}}
```

| 状态 | `error.code` | 场景 |
| --- | --- | --- |
| 400 | `invalid_request` | 参数错误（当前 GET 接口不接受查询字符串） |
| 404 | `not_found` | 未声明路由、未知版本等 |
| 501 | `not_implemented` | 业务模块尚待接入 |
| 503 | `hive_unavailable` | 公共包缺失、配置错误或 Hive 不可达 |
| 504 | `hive_timeout` | 连接或查询超时 |
| 500 | `hive_query_failed` / `internal_error` | SQL、结果上限、序列化或其他服务内部错误 |

`POST /api/ask` 保持现有请求、响应和 Agent 降级机制，完整格式见 [Web README](../steam-review-web/README.md#接口契约) 与 [Agent API](../agent-retrieval/docs/API.md)。GET 接口不自动返回本地静态数据，便于联调期间识别 Hive 未接入或读取失败。

## 业务数据格式与口径

### 版本与统计（黄静雯）

- `GET /api/index` 顶层包含 `generated_at`、`source:{games,patches}` 和 `games[]`。每款游戏包含 `appid`、`name`、`reviews_total`、`patches[]`、`avg_delta`、`latest`、`first`；版本摘要含 `patch_id`、`patch_name`、`title_zh`、`release_date`、`before_rate`、`after_rate`、`delta_pp`、`window_reviews`、`item_count`、`top_item`。
- `GET /api/patches/{patch_id}` 包含 `patch`（含 `items[]`）、`stats`（按语言组和三阶段组织，每组含 `reviews`、`recommended`、`rate`）、`delta_pp`、`trend[]`、`trend_lang`、`language_compare[]`、`comments[]`、`window_reviews_total`、`pool_reviews`，内部字段和示例见 `data/patches/*.js`。阶段名 `before/release/after`，语种组 `all/chinese/en` 及表中的其他组。逐日记录包含 `date`、`offset`、`reviews`、`recommended`、`rate`。
- 来源优先 `steam_dw.dim_patch`、`dim_item`、`ads_patch_stage`、`ads_patch_daily`、`ads_patch_compare`，清洗评测来自 `game_reviews.reviews_v1`。抽样评测、条目提及和 `pool_reviews` 的过滤、采样与近似匹配口径沿用 `steam-review-web/tools/export_web_data.py`，如修改算法须与前端和 Agent 一起确认。有效评测量与正文长度 40–1400 的样本池量需分别统计。
- Hive 推荐率是 0~1，Web `rate`、`before_rate`、`after_rate` 等字段用 0~100 百分数，保留两位小数。为兼容当前问答适配器及界面，展示 `delta_pp=round(after_rate-before_rate, 2)`（使用已转换、舍入至两位的小数百分数）。报告原始差值来自 Hive `change_percentage_points`，单位已是百分点。星露谷 1.5 的原始差值约 0.96384913 pp，页面按 98.42−97.45 得 0.97 pp。未有有效数据的比例为 `null`。
- 所有版本窗口使用 UTC 更新前 14 天、发布当天、更新后 14 天共 29 日；以有效评测数为分母。`window_reviews_total` 为三阶段 `reviews` 之和，并与逐日统计的合计一致。

### 已复核引文（黄静雯、唐宇杰协同）

`GET /api/verified-quotes` 返回逐条引文，保留 `review_id`、`appid`、`patch_id`、`item_id`、`item_title`、`phase`、`language_std`、`voted_up`、`created_at`、`evidence`、`span_start`、`span_end`、`verification`、`score`、`source_url`、`url` 等现有字段。以 `hadoop_delivery.verified_quotes` 或经过核对的 `game_review_upload.gr_verified_quotes` 为来源。其中 `hadoop_delivery` 的 Steam 链接字段为 `steam_review_url`，按 Web 契约映射为 `url`。连接 `game_reviews.reviews_v1` 补充日期与所需明细时用 `appid + review_id`，引文身份用 `review_id + patch_id + item_id` 校验，与现有 `data/verified.js` 比对。引文必须保留原文和复核元数据；候选得分或未复核语料不能标为已复核。

### NLP（何国境）

- `GET /api/nlp/topics` 包含 `generated_at`、`aspects[]`（`id/name_zh`）、`sentiments`、`phases`、`sources`、`global[lang][aspect]`、`by_game[appid][lang][aspect]`、`by_patch[patch_id][lang][phase][aspect]`、`patch_meta`。每个情感桶含 `positive/negative/neutral` 三个整数，`lang=zh/en`，版本阶段 `before/release/after/all`。
- `GET /api/nlp/items` 包含 `generated_at`、`total_items`、`hit_items`、`note`、`items[]`。记录保留 `item_id`、版本及游戏信息、`aspect`、`sentiment`、`total`、`bucket`（具体字段见 `data/nlp/items.js`）。登记的 75 项均返回，包括零命中项。
- 对应 `nlp_topic` 中英主题明细/汇总、`steam_dw.ods_nlp_*`、`ads_topic_dist`、`ads_item_dist`、`ads_item_patch` 等表，选择与现有数据包一致的规则标注方法。若提供 RBT3，应显式标注方法及数据源，勿与规则结果直接混合。无版本筛选时按全时间线统计；条目阶段的 `pre_14d/release_day/post_14d` 对应 `before/release/after`，窗口外另计。一个评测可命中多个通用主题，主题命中数和评测数应区分。现有页面规则参见 [NLP 展示说明](../steam-review-web/docs/NLP前端展示说明.md)。

## 并行开发与接入依赖

| 成员 | 本轮独立修改入口 | 依赖或验收项 |
| --- | --- | --- |
| 胡家源 | `hive_service/`、`steam-review-web/server.py`、`backend/common.py`、`backend/routes.py`、总体联调 | 共享连接、错误映射、API 回归、问答数据源切换与最终静态文件清理 |
| 黄静雯 | `steam-review-web/backend/version_api.py` | 版本总览、详情、统计与复核引文接口，负责 SQL 和响应结构测试 |
| 何国境 | `steam-review-web/backend/nlp_api.py` | 中英主题、条目情感接口，负责聚合口径与响应结构测试 |
| 唐宇杰 | `agent-retrieval/` | 安装公共 Hive 包，为语料、版本统计、词库、复核引文及 NLP 检索接入 Hive；保持 `POST /api/agent/ask` 及现有检索契约 |
| 石曜铭 | `steam-review-web/assets/`、页面 HTML | 用 `fetch` 接入上述 GET API，识别加载、空结果、错误状态；继续调用 `/api/ask` |
| 张成鑫 | `crawler/` 及原始采集层交付 | 确认原始评测、公告、两个清单的 Hive 库名、表名和有效行数，通知总体联调 |

Agent 继续在独立进程运行，现有 HTTP 转发方式无需调整。语料 `game_review_upload.gr_agent_corpus_62007`、词库 `gr_lexicon_items` / `gr_lexicon_global` 等均可由共享客户端访问。检索索引、向量模型等可重建或运行所需的派生资源需按模块使用情况处理。

### 最终集成顺序

1. 业务负责人在各自 API 函数/Agent 数据源实现 Hive 查询，按现有数据包、Agent 文档和数据口径验证数据与字段。
2. Web 负责人将 `SRW.loadIndex`、`SRW.loadPatch`、NLP 加载函数及复核引文加载接入 GET API。各页面使用实际 Hive 接口完成浏览器验证后，由胡家源统一验证问答页、Agent 转发、缺数据和服务异常场景。
3. 胡家源将 `server.py` 当前的 `load_index` / `load_patch` / `verified_item_quotes` 等静态依赖接入已实现的版本 API（涉及启动信息、规则问答、Agent `appid` 补全和指标补全）。需先验证 Hive 可用状态下的现有问答，并明确 Hive 不可用时的降级行为。
4. 检查 Web 前端、后端、Agent 构建脚本、离线入口和检索运行时是否仍依赖业务数据文件，完成数据一致性及接口回归后再逐批清理可从 Hive 读取的静态交付文件。训练、评估、模型、索引和复现所需数据分别核对依赖。

## 验证命令

在仓库根目录执行：

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s steam-review-web -p 'test_*.py' -v
# 连接 Tailscale、配置 Hive 后，执行真实只读联调：
$env:HIVE_QUERY_TIMEOUT = '120'
python steam-review-web/tools/check_hive_integration.py
# Web 启动后：
Invoke-RestMethod http://127.0.0.1:8066/api/health/hive
```

联调脚本目前与保留的静态数据包比对星露谷 1.5 推荐率、阶段与逐日数量、中文英文主题全局分布、复核引文标识，以及语料、清洗评测、交付清单总行数。业务 API 完成后应补充实际 HTTP 结果与这些基准的对比。清理静态数据前以确认过的 Hive 数据和接口测试作为后续回归基准。

## 2026-11-28 联调记录

使用公共客户端连接 `hive.hujiayuan.com:10000`，认证 `NONE`。已实际执行的代表性只读查询如下。

| 查询 | 实际结果 |
| --- | --- |
| `SELECT 1 AS ok` | `1` |
| `steam_dw.ads_patch_compare`，`sdv_20201221_15`，`all`（位置参数绑定） | 更新前 1,021 条、当天 253 条、更新后 4,802 条，共 6,076 条，与 Web 静态数据一致；原始推荐率为 0.9745347698、1.0、0.9841732611 |
| `nlp_topic.topic_distribution`（`LIMIT 1`） | `content / positive = 30,830`，与 Web 中文全局对应桶一致 |
| `hadoop_delivery.verified_quotes` 行数 | 469，与 Web 静态引文数组行数一致 |
| `game_review_upload.gr_agent_corpus_62007` 行数 | 62,007 |
| `game_reviews.reviews_v1` 行数及去重 appid 数 | 4,884,580 条、12 款游戏 |
| `game_review_upload.gr_manifest` 行数 | 23，本轮未对清单每项重新验算 |

`SHOW TABLES` 已确认 `steam_dw` 的版本、统计和 NLP ODS/ADS 表、`nlp_topic` 的中英及 RBT3 表、`hadoop_delivery` 引文和覆盖表、`game_review_upload` 的语料及词库表均可见。原始采集层的全量原始评测、公告和两个清单尚未看到明确对应的表，等待张成鑫确认交付表名。

本地公共层 11 项、Web API 与既有问答 16 项单元/HTTP 回归均通过（包含模拟 Hive 超时、连接错误，使用本地模拟上游验证 Agent HTTP 转发与降级）。实际 Web 路由检查确认 5 个未接入的 GET 业务端点返回 501。Agent 的完整知识库和 Hive 数据源迁移尚待唐宇杰完成，本轮未启动真实 Agent 检索服务。

执行完整联调脚本时，Hive TCP 连接发生超时，延长连接超时重试仍无法建立连接，Tailscale 本地服务运行但对 Hive 节点的 ping 无回复。真实 HTTP 健康检查正确返回 504 `hive_timeout`，模拟连通性检查返回 200。逐日/阶段完整对账、中英全局 NLP 对账、复核引文身份对账需在网络恢复后运行脚本补验。当前记录不代表各业务 API 已接入或完整联调通过。
