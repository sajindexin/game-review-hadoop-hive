# 游戏更新反馈 · 检索与问答 Agent（本模块负责人）

对应分工：**检索算法与 Agent**。输入是官方更新日志（上游版本分析模块分支的 26 个版本 / 75 项改动）、
全量清洗数据 v1（488.5 万条），输出是一个**可以独立启动、也可以被 Java Web 调用**的服务：

> 分支说明：本模块提交在 `feat/agent-retrieval`（基于 `main`）。为了让模块自包含、不依赖先 checkout
> 别人的分支，上游版本分析模块的 `items.jsonl`、`patches.jsonl`、`更新前后对比.csv` 和 469 条已复核引文
> 都原样复制在 `data_sources/` 下；该模块的原始分支仍是 `feat/game-update-feedback-analysis`。
> 其中 `verified_quotes.jsonl` 来自该模块的提交 `c26c38a`，SHA-256 为
> `84b4174a42f21a52413de5fd75f571e2982f626d88fc51e1d138ea9fc2f6f9d1`，与 PR2 声明一致；
> 仓库用 `.gitattributes` 的 `data_sources/** -text` 固定字节，不受 `core.autocrlf` 影响。

| 交付物 | 在哪里 | 说明 |
|---|---|---|
| 检索接口 | `POST /api/retrieve/reviews`、`POST /api/retrieve/announcements` | BM25 + 向量混合检索，返回带 `review_id`、Steam 链接、语种、日期的原文 |
| 候选更新条目 | `POST /api/items/candidates` | 给一段玩家原话，返回它可能提到的官方改动（供上游版本分析模块的关联模型联调） |
| Agent 服务 | `POST /api/agent/ask` | 自动判断“查指标 / 查公告 / 查评论”，模板化作答，逐条标注出处 |
| **已复核引文接入** | `data_sources/verified_quotes.jsonl`（469 条） | 上游版本分析模块 `c26c38a` 提交的原文件，哈希校验一致，优先于数据集原文 |
| 48 道验收题 | `eval/acceptance.jsonl`、`eval/run_acceptance.py` | 覆盖数字、已复核原文、语言/阶段筛选、条目相关度、空态、拒答；**当前 48/48 通过** |
| 联调说明 | [`docs/HANDOFF_WEB.md`](docs/HANDOFF_WEB.md) | 给 Web 端的 5 分钟接入清单 |
| 接口说明 | [`docs/API.md`](docs/API.md) | 完整字段与错误码 |
| 设计说明 | [`docs/DESIGN.md`](docs/DESIGN.md) | 数据流、打分、证据分级、防幻觉策略 |

## 快速开始

```powershell
cd agent-retrieval
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 1) 构建知识库（约 10 秒；只需 v1 parquet，目录/引文都在 data_sources/）
python -m tools.build_knowledge `
  --parquet "<v1 发布包>\releases\v1\data\cleaned_reviews.parquet" `
  --out data `
  --web-verified-out ../steam-review-web/data/verified.js

# 2) 命令行问一句
python -m src.cli ask "为什么艾尔登法环 1.12 差评变多了？"

# 3) 启动 HTTP 服务（自带演示页 http://127.0.0.1:8000/ 和 OpenAPI 文档 /docs）
python -m src.cli serve --port 8000

# 4) 跑 48 道验收题，生成 eval/REPORT.md
python eval/run_acceptance.py
```

环境：Python 3.10+（本机 3.14 验证通过）。`data_sources/catalog/` 里的 `items.jsonl`、`patches.jsonl`、
`更新前后对比.csv` 是从上游版本分析模块分支原样复制的（为了不依赖“先 checkout 该模块的分支”），
`data_sources/verified_quotes.jsonl` 是该模块的 469 条已复核引文。
想直接用该模块的分支目录时，加 `--branch <game-update-feedback-analysis 路径>` 即可覆盖；
脚本会在分支下一层自动查找 `items/items.jsonl`、`patches/patches.jsonl`、`更新前后对比.csv`，不依赖中文目录名前缀。

`data/` 是生成物（约 40MB），不入库；换机器只要重新跑第 1 步。

## 主题情感问答

构建知识库时读取同仓库 `nlp-topic-sentiment/data/result/` 中的中英文规则标注明细（可用 `--nlp-results` 指定目录），按游戏、UTC 日期和版本前后 14 天聚合。问答示例：

- 玩家主要在讨论什么？
- 英文玩家更新后主要在讨论什么？
- 更新后哪些方面的负面反馈增加了？

主题排名按主题情感对计数，支持正向、负向和中性标签，前后对比同时给出负向数量及该主题内部负向标签占比。全部语种选项在此工具中覆盖中文和英文。两种语言采用统一的规则弱标注口径，英文尚无独立金标评估。中文 RBT3 模型结果保留在 NLP 模块中。

构建时优先读取上游版本分析模块的 `窗口内已复核可引用关联.jsonl`，可用 `--verified` 覆盖。指定 `--web-verified-out` 时，Agent 的 `verified.json` 与 Web 的 `verified.js` 从同一批标准化记录生成，含 Steam 原文链接。仅独立使用 Agent 时可省略 Web 输出参数。

## 它怎么回答

一次提问会走四步，**每一步都留下可核对的痕迹**：

1. **路由**：LLM 输出结构化 JSON（`intent / phases / item_ids / language / ranking`），
   失败或未配置时退回确定性规则；模型只做路由，不生成数字和原文；
2. **定位实体**：版本仍用确定性规则识别（`1.12` / `Deep Freeze` / `7.33`），条目优先用路由结果，
   没有再用别名词典兜底；
3. **调工具**：
   - `lookup_metrics`：读 26 个版本的窗口指标（更新前 14 天 / 发布当天 / 更新后 14 天，全部 / 中文 / 英文）；
   - `search_announcements`：在 75 项官方改动里检索；
   - `search_reviews` / `search_verified`：在 6.2 万条窗口样本或 469 条已复核引文里检索，
     按条目、语种、阶段、好评/差评过滤；
   - `lookup_topic_stats`：查询版本窗口内的中英文 NLP 主题分布及前后负向标签变化；
   - `top_negative_reviews`：问“为什么差评变多”时，取对应阶段被顶得最多的差评；
4. **模板化作答**：数字来自指标表和 NLP 窗口统计，原话来自检索结果，逐条打 `[n]` 出处；检索不到就写“暂无已复核引文”。

LLM 路由通过环境变量开启（不配也能跑，自动用规则）：

```powershell
$env:GRA_LLM_API_KEY="sk-..."                 # 必填
$env:GRA_LLM_MODEL="deepseek-chat"            # 可选
$env:GRA_LLM_BASE_URL="https://api.deepseek.com/v1"  # 可选，OpenAI 兼容即可
```

例：

> 《ELDEN RING》Patch 1.12（2024-06-20）的推荐率：更新前 14 天 96.0%（4,657/4,851），发布当天 97.2%（344/354），
> 更新后 14 天 82.5%（4,074/4,936），前后相比下降 13.5 个百分点（全部语言）[1]
>
> 数据集原文（未复核，更新后差评按点赞排序）：
> 1. “The performance sucks. I get lags and stutter even on RTX4090…” —— en · 2024-06-22 · 不推荐 [2]

## 证据分级（答辩时按这个口径说）

| 级别 | 来源 | 什么时候用 | API 标记 |
|---|---|---|---|
| **已复核** | 上游版本分析模块 `窗口内已复核可引用关联.jsonl`（469 条、20 个版本、37 项改动，`AI逐条核对原文`） | 能命中该版本/改动时**优先**用，且不再混入别的原文 | `evidence_status=verified_quotes`，引用 `verified=true` |
| 数据集原文 | v1 窗口样本（62,007 条），带 `review_id` + Steam 链接 | 该版本没有已复核库，或问“为什么差评多”需要代表性差评 | `evidence_status=dataset_quotes`，引用 `verified=false`，答案里写“未复核” |
| 混合 | 同一回答里同时有已复核和未复核 | “更新前后”只有一段有复核库 | `evidence_status=mixed_evidence`，按 `citations[].verified` 逐条标 |
| 无 | — | 该改动没有可引用原话 | `evidence_status=none`，答案写“暂无已复核引文” |

**严格模式**：只要这个版本有已复核库，就只用已复核引文；某条改动没有就返回空态，不拿整款游戏的评论凑数。
唯一的例外是“为什么差评变多”这类归因问题——已复核库不足时，退回“对应阶段被顶得最多的差评”，并在标题里写明**未复核**。
如果同一回答里两段证据级别不同，顶层标 `mixed_evidence`，前端不能统一显示“已复核”。

**上游 PR2 口径**（`PR2_全量核验说明与验收记录.zip`）：全量 4,884,580 条评测、6,622,542 对模型候选、
75 个评分分块，`status=verified`（17:17 复跑一致）；469 对引文涉及 445 条不同评测，覆盖 20/26 个版本、
37/75 项改动，6 个版本与 38 项改动暂无引文、19 项仅 1–2 对。引文文件 SHA-256
`84b4174a42f21a52413de5fd75f571e2982f626d88fc51e1d138ea9fc2f6f9d1`，与本仓库 `data_sources/` 里的文件一致。

## 同义词词典（词面覆盖）

别名词典解决“玩家说法和官方标题对不上”的问题，分三层：

- `data_sources/lexicon/items_extra.jsonl`：75 个条目共 **410 个同义词** + 负向词，
  由人工重点词 + LLM 批量扩词组成，构建时合并进条目索引；
- `data_sources/lexicon/global_synonyms.json`：10 组通用词（闪退/崩溃/crash、卡顿/掉帧、联机/多人…），
  查询时做同义词扩展；
- 负向词用于防误命中，例如问“外挂”不会命中“武器挂件”。

效果用独立问法集评测：`python eval/run_lexicon_eval.py`，39 条同义/繁体/英文/错字问法，
当前 **Hit@1 = 39/39（100%）**。

## 检索口径（写报告时请照抄）

- **语料**：v1 全量 4,884,580 条里，落在 26 个版本窗口（发布前后各 14 天）内的评测；
  每个窗口保留「提到官方改动」的评测（最多 1,200 条）+ 每个阶段随机样本（最多 900 条），合计 62,007 条。
  也就是说：**指标是全量口径，原文检索是窗口样本口径**，页面上必须写清楚。
  “为什么差评变多”里的高赞差评同样来自这 62,007 条窗口样本，不是全量 488 万条，回答里会标“数据集原文（未复核）”。
- **筛选**：`language`（all / chinese / en）与 `phase`（before / release / after）会传入检索、已复核引文和差评回退三条路径；
  `phase` 不传时从问题里解析（“更新前 / 发布当天 / 更新后”），筛选后没有引文就返回空态。
- **分词**：不依赖 jieba。中文按「单字 + 相邻双字」，英文按连续字母/数字；单字只用于召回，证据门槛用双字实体词。
- **打分**：BM25（k1=1.2, b=0.75）与向量余弦按 0.7 / 0.3 融合；条目明确时给命中该条目别名的评测加权。
- **证据门槛**：候选必须命中问题里的实体词（如「姜岛」「挂件」「差评」），否则不返回，宁缺毋滥。
- **向量**：默认 `hash-512`（零依赖离线兜底）。装好 `sentence-transformers` 并设置
  `GRA_BGE_MODEL=<bge-small-zh-v1.5 路径>` 后，重新构建即切换为 BGE，接口不变。

## 验证到哪一步了（别只看“做完了”）

| 项目 | 怎么验的 | 结果 |
|---|---|---|
| 48 道验收题 | `python eval/run_acceptance.py` | ✅ 48/48，见 `eval/REPORT.md` |
| 冒烟测试 | `python -m unittest discover -s tests` | ✅ 30/30 |
| 已复核引文 | 469 条全部入索引，20 个版本 37 项改动 | ✅ 引用带 `verified=true` 与 Steam 链接 |
| 阶段编辑规则 | 469 条里 55 条按原 `find_evidence` 规则会被排除 | ✅ 已保留字段并在 `search_verified` 同步过滤 |
| 同义词词典 | `python eval/run_lexicon_eval.py`（39 条同义/繁体/英文/错字问法） | ✅ Hit@1 39/39 |
| HTTP 接口 | `/health`、`/api/games`、`/api/patches/{id}`、`/api/agent/ask` | ✅ 200，UTF-8 正常；12 款游戏 `review_count` 均 > 0 |
| 答案格式 | 断言回答里不含 `**` / `](` 等 Markdown 残留 | ✅ 纯文本，链接只出现在 `citations` |
| 版本详情页 | `/versions/{patch_id}` 路由 | ✅ 200，含“玩家原文”已复核列表 |
| 浏览器视觉 | 沙箱里 Edge headless 起不来 | ⚠️ **未截图验证**，CSS/JS 只做了语法与接口检查 |
| 真 BGE | 沙箱 pip 装不上 sentence-transformers | ⚠️ 未验证，默认 hash 向量 |

## 已知限制（别在答辩时被问倒）

1. **已复核库只覆盖 26 个版本里的 20 个、75 项改动里的 37 项**。其余版本走“数据集原文（未复核）”，
   答案和引用里都会明确标注，不能混着说成“已复核”。
2. **中文别名覆盖有限**：例如 Rust 的“海浪”在窗口内 0 条中文提及（全量也只有 8 条英文 water），
   所以问“海浪效果”会明确返回“暂无已复核引文”，这是设计行为，不是 bug。
3. **指标分组只有 all / chinese / en**：上游版本分析模块原表还有 zh-Hant、ru 等 8 个语种，小语种样本为 0 时
   `rate` 为空，本模块只保留前三个稳定分组。
4. **LLM 只做路由，不做生成**：模型输出 `intent / phases / item_ids` 等结构化字段；
   数字仍由模板从指标表填，原文仍从已复核库取。没配 Key 时退回规则路由，行为可复现。
   因此“回答自然度”不会像自由生成那么高，但每个数字和引文都能核对。
5. **条目别名决定召回**：像 GTA「保金执法事务所」在窗口样本里没有任何中文提及，
   问它会明确返回“暂无已复核引文”，不会拿整款游戏的评测凑数（这是修过的行为）。

## 和 Web 端联调

Web 端的 Servlet 只需要两次调用：

```java
// 1) 版本页初始化：拿指标 + 官方改动
GET  http://127.0.0.1:8000/api/patches/sdv_20201221_15

// 2) “问这个版本”按钮
POST http://127.0.0.1:8000/api/agent/ask
{"question":"1.5 更新后推荐率降了多少？","appid":"413150","patch_id":"sdv_20201221_15"}
```

返回里的 `answer` 直接渲染；`numbers` 可做数字卡片；`citations` 渲染“查看统计 / 查看原文”；
`status=need_context` 时把 `suggestions` 渲染成可点的追问。字段细节见 [`docs/API.md`](docs/API.md)。

## 目录结构

```
agent-retrieval/
├── tools/build_knowledge.py    # parquet + 上游版本分析模块分支 + 已复核引文 -> catalog/corpus/索引
├── tools/build_lexicon.py      # 人工重点词 + LLM 扩词 -> 同义词表
├── data_sources/
│   ├── verified_quotes.jsonl   # 上游版本分析模块 469 条已复核引文（原样保留，勿改）
│   ├── lexicon/
│   │   ├── items_extra.jsonl   # 75 条目同义词 + 负向词
│   │   └── global_synonyms.json
│   └── catalog/                # 从上游版本分析模块分支复制的 items / patches / 指标表
│       ├── items.jsonl
│       ├── patches.jsonl
│       └── 更新前后对比.csv
├── src/
│   ├── tokenize.py             # 中英混合分词
│   ├── bm25.py                 # 纯 numpy BM25 + 倒排索引
│   ├── dense.py                # 向量编码（hash 兜底 / BGE 可选）
│   ├── catalog.py              # 版本目录、指标表、版本号解析
│   ├── retrieval.py            # 评测/已复核引文/公告检索、候选条目
│   ├── agent.py                # 路由 -> 工具 -> 模板作答
│   ├── router.py               # LLM 结构化路由 + 规则兜底
│   ├── api.py                  # FastAPI 接口
│   └── cli.py                  # ask / serve / meta
├── web/                        # 演示页（原生 JS，无需构建）
├── eval/                       # 48 道验收题 + 词典评测 + 报告
├── docs/                       # API.md / DESIGN.md
└── tests/test_smoke.py         # 冒烟测试
```

## 数据版本

所有输出都基于 v1：`cleaned_reviews.parquet` SHA256
`6602f7bbf9983a58627a100f238e71483b5cf3e8deb19070fc761afb39702e08`，
指标来自上游版本分析模块 `更新前后对比.csv`，改动目录来自 `items.jsonl` / `patches.jsonl`。
