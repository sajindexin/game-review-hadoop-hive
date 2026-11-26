# Steam 评价与公告采集工具

面向游戏更新反馈分析的 Steam 公开评价与社区公告命令行采集工具。支持按时间倒序常规采集与**按天配额均衡采样**，原始批次按 UTC 采集日期写入 JSONL（或 `.jsonl.gz`），使用 SQLite 持久化任务状态与分页断点，并支持一键合并导出未压缩的标准 JSONL 文件。

---

## 环境安装与运行方式

要求 **Python 3.12 或更高版本**。支持通过 `uv`（推荐）或标准 `venv + pip` 安装运行。

### 方式一：使用 `uv`（推荐）

在 `crawler/` 目录下执行：

```powershell
uv sync
uv run steam-crawler --help
```

> 若从仓库根目录运行，请加上 `--directory crawler`：
> ```powershell
> uv run --directory crawler steam-crawler --help
> ```

### 方式二：未安装 `uv` 时（使用 `venv` + `pip`）

在 `crawler/` 目录下创建虚拟环境并安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

安装完成后，可直接使用 `steam-crawler` 命令，或通过 `python -m steam_crawler` 模块方式运行（无需激活虚拟环境时也可直接调用 `.\.venv\Scripts\steam-crawler.exe`）：

```powershell
steam-crawler --help
# 或
python -m steam_crawler --help
```

> **命令替换说明**：若未安装 `uv`，只需将下文所有示例中的 `uv run steam-crawler` 替换为 **`steam-crawler`**（已激活虚拟环境）或 **`python -m steam_crawler`** 即可。

---

## 核心工作流示例

### 场景一：设定总配额按天均分采集评价 + 采集全部公告 + 合并导出

按天采样（`sample-reviews`）要求使用**独立的数据目录**，因此推荐将「按天采样评价」和「全部公告」分别存入两个目录，再通过 `export` 合并导出为未压缩的 `reviews.jsonl` 与 `announcements.jsonl`。

下面以 CS2（App ID `730`，自发售日 `2023-09-28` 起，总配额 50 万条平均分配到每一天）为例：

#### 1. 按天平均分配配额采集评价

- `--total`：设定评价总采集目标条数。
- `--since`：设定起始日期或时间（必填）；省略 `--until` 时默认截止到命令启动时刻。
- `--quota equal`：将总配额**严格平均分配到每一天**（各日配额最多相差 1 条）。若使用默认的 `--quota duration`，则按每日时间窗口的实际覆盖时长比例分配（首尾不足一整天时按时长折算，完整日期之间仍然等量）。
- `--fill all`（默认）：某天实际评价不足配额时，自动先在同月其余日期补齐，仍有缺额再跨月补齐。

**使用 `uv`：**

```powershell
uv run steam-crawler sample-reviews --app-id 730 --since 2023-09-28 --total 500000 --quota equal --language schinese,tchinese,english --gzip --output data/cs2-reviews-by-day
```

**不使用 `uv`：**

```powershell
steam-crawler sample-reviews --app-id 730 --since 2023-09-28 --total 500000 --quota equal --language schinese,tchinese,english --gzip --output data/cs2-reviews-by-day
```

> **提示**：
> - 可先附加 `--plan-only` 仅生成 `sampling-plan.json` 预览每日时间窗口与配额，确认无误后通过 `resume-sample` 启动正式采集。
> - 采集从最近一天向前推进，每天内部按评价创建时间从新到旧抓取至当日配额。
> - 采集完成后，自动生成按创建时间倒序排列的样本文件 `sample/reviews.jsonl.gz` 以及记录每日条数和语言分布的 `sample/summary.json`。若全范围补齐后仍不足目标总数，任务状态记为 `shortfall`（退出码 `1`），并照常导出已采到的全部样本。

#### 2. 采集该游戏的全部公告

将 `--max-items` 设为 `0`（不限条数），采集 Steam 社区公告源中当前可获取的全部公告：

**使用 `uv`：**

```powershell
uv run steam-crawler news --app-id 730 --max-items 0 --output data/cs2-all-announcements
```

**不使用 `uv`：**

```powershell
steam-crawler news --app-id 730 --max-items 0 --output data/cs2-all-announcements
```

#### 3. 合并导出为未压缩 JSONL

使用 `export` 子命令，通过 `--reviews-from` 与 `--announcements-from` 分别指定上述两个采集目录，导出至目标目录：

**使用 `uv`：**

```powershell
uv run steam-crawler export --reviews-from data/cs2-reviews-by-day --announcements-from data/cs2-all-announcements --output data/cs2-export
```

**不使用 `uv`：**

```powershell
steam-crawler export --reviews-from data/cs2-reviews-by-day --announcements-from data/cs2-all-announcements --output data/cs2-export
```

导出完成后，`data/cs2-export/` 下会生成 UTF-8 编码的未压缩文件：
- `reviews.jsonl`（自动沿用按天采样的 `sample/reviews.jsonl.gz` 样本及创建时间倒序）
- `announcements.jsonl`（按记录 ID 去重并保留最近一次采集的版本）

---

### 场景二：常规采集评价与公告（共用同一目录）

如果不按天切分配额，而是直接按创建时间从新到旧采集指定上限的评价与全部公告（以《杀戮尖塔 2》App ID `2868840` 为例），可将两者存入同一目录：

**使用 `uv`：**

```powershell
# 1. 按创建时间从新到旧采集简中、繁中、英文评价，最多 10 万条
uv run steam-crawler reviews --app-id 2868840 --language schinese,tchinese,english --max-items 100000 --gzip --output data/sts2

# 2. 采集全部社区公告
uv run steam-crawler news --app-id 2868840 --max-items 0 --output data/sts2

# 3. 从同一采集目录直接导出未压缩的 reviews.jsonl 和 announcements.jsonl
uv run steam-crawler export --input data/sts2 --output data/sts2-export
```

**不使用 `uv`：**

```powershell
steam-crawler reviews --app-id 2868840 --language schinese,tchinese,english --max-items 100000 --gzip --output data/sts2
steam-crawler news --app-id 2868840 --max-items 0 --output data/sts2
steam-crawler export --input data/sts2 --output data/sts2-export
```

---

## 任务状态查询与断点续采

### 常规任务（`reviews` / `news`）

网络中断或达到单次 `--max-items` / `--max-pages` 上限时，任务进度保存在 SQLite 中（待续采状态为 `paused`，读完整个查询范围为 `completed`）。

先通过 `status` 查看任务 ID，再用 `resume` 从断点继续采集：

```powershell
# 使用 uv（若无 uv，将 "uv run steam-crawler" 替换为 "steam-crawler"）
uv run steam-crawler status --output data/sts2 --limit 5
$taskId = '替换为实际任务ID'
uv run steam-crawler status --output data/sts2 --task-id $taskId --json
uv run steam-crawler resume $taskId --output data/sts2 --max-items 3000
```

- `resume` 会沿用原任务的时间范围、筛选条件、每页条数与压缩设置，并优先消费已缓存的页面数据。
- 任务统计字段说明：
  - `matched`：符合筛选条件且在当前任务内去重后的条数。
  - `written`：实际新增或内容变更并落盘写入的条数。
  - `unchanged`：匹配但与历史已存记录内容一致、未重复写入的条数。
- 如需调整查询范围或定期增量刷新数据，请直接新建 `reviews` 或 `news` 任务。

### 按天采样任务（`sample-reviews`）

查看按天采样的整体进度或从中断处继续采样：

```powershell
# 使用 uv（若无 uv，将 "uv run steam-crawler" 替换为 "steam-crawler"）
uv run steam-crawler status --sample --output data/cs2-reviews-by-day --json
uv run steam-crawler resume-sample --output data/cs2-reviews-by-day
```

---

## 日志与输出控制

- **实时日志**：启动采集时，终端会打印数据目录和日志文件的绝对路径。完整日志默认同步输出到终端并追加写入 `--output` 目录下的 `collection.log`。
- **采样进度提示**：按天采样每处理完一页都会实时刷新总进度与当日配额进度，例如：
  `采样总进度 120,100/500,000 (24.02%)；2026-02-17 当日 100/436 条`。
- **`--json` 模式**：终端日志改写到 `stderr`，`stdout` 仅输出结构化 JSON 结果（包含 `output_dir`、`log_file` 等路径信息；采样模式下还包含计划、进度及样本文件路径）。
- **`--quiet` 模式**：终端仅显示警告、错误与最终结果摘要，`collection.log` 文件仍保留完整日志。

---

## 参数参考

工具包含以下子命令：
- `reviews`：常规采集评价
- `sample-reviews`：按天配额采样评价
- `news`：采集公告与新闻
- `resume TASK_ID`：续采常规任务
- `resume-sample`：续采按天采样计划
- `status`：查询本地任务或采样进度
- `export`：导出未压缩 JSONL

所有子命令均支持 `--help` 查看帮助；`steam-crawler --version` 显示版本号。

### 通用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--output DIR` | `data/` | 数据根目录。在 `sample-reviews`、`resume-sample` 和 `export` 中为必填项。相对路径基于当前工作目录，含空格时请加引号。 |
| `--json` | 文本输出 | `stdout` 仅输出 JSON 结果；采集时终端日志改写入 `stderr`，文件日志照常记录。 |

### 导出参数：`export`

| 参数 | 说明 |
| --- | --- |
| `--input DIR` | 从同一个采集目录导出已有的数据类型。当三个来源参数均省略时，默认读取 `data/`。 |
| `--reviews-from DIR` | 单独指定评价的采集目录（优先级高于 `--input`）。 |
| `--announcements-from DIR` | 单独指定公告的采集目录（优先级高于 `--input`）。 |
| `--output DIR` | **必填**，导出未压缩 JSONL 文件的保存目录。 |
| `--app-id ID` | 仅导出指定 App ID 的游戏数据；省略时不限游戏。 |
| `--overwrite` | 允许覆盖目标目录中已存在的同名导出文件；省略时遇到同名文件将报错退出。 |
| `--json` | 以 JSON 格式输出导出目录、生成的文件路径和导出条数。 |

> 说明：仅指定 `--reviews-from` 或 `--announcements-from` 且未传 `--input` 时，只导出所指定的数据类型。导出完全基于本地已采集文件，保留每条记录的完整原始字段（含 `payload` 与采集元数据）。

### 新建任务公共参数：`reviews` 与 `news`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--app-id ID` | **必填** | Steam 商店链接 `/app/` 后的数字（范围 `1`～`4294967295`）。在 `reviews` 和 `news` 中可重复传参以为多款游戏分别建任务。 |
| `--since DATE_OR_TIME` | Unix 纪元起点 | 起始时间（包含）。仅写日期 `YYYY-MM-DD` 时从当日 `00:00:00` 开始。 |
| `--until DATE_OR_TIME` | 命令启动时刻 | 结束时间。仅写日期时包含整天；精确到时间时为左闭右开边界 `[since, until)`。 |
| `--timezone ZONE` | `Asia/Shanghai` | 用于解析无时区偏移的日期/时间（IANA 时区名，如 `UTC`）。输入自带 `Z` 或 `+08:00` 时自带偏移优先。 |
| `--page-size N` | 评价 `100`，公告 `50` | 单页请求条数。评价支持 `1`～`100`，公告支持 `1`～`1000`（公告遇到同秒分页边界时会自动按需扩容）。 |
| `--keyword TEXT` | 不限 | 包含关键词筛选，可重复传参，命中任意一个即匹配（大小写不敏感）。 |
| `--exclude-keyword TEXT` | 不排除 | 排除关键词筛选，可重复传参，命中任意一个即跳过（优先于 `--keyword`）。 |
| `--gzip` | 未压缩 `.jsonl` | 原始批次文件使用 gzip 压缩，扩展名为 `.jsonl.gz`。 |

- **时间格式示例**：默认时区下，`--since 2026-11-01 --until 2026-11-19` 覆盖 11 月 1 日 00:00 至 11 月 19 日 24:00；若写为 `--until 2026-11-19T12:00:00+08:00`，则在 19 日中午 12:00 前截止。
- **关键词匹配范围**：按字面子串匹配（忽略大小写）；评价匹配正文，公告同时匹配标题和正文。含空格的短语请加引号，如 `--keyword "frame rate"`。

### 评价专用参数：`reviews`（及 `sample-reviews`）

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--language LANG` | `all` | 按 Steam 语言标识筛选，可逗号分隔或重复传参（详见下方语言代码表）。 |
| `--time-field FIELD` | `created` | 仅 `reviews` 可选：`created` 按创建时间倒序采集；`updated` 按最后修改时间倒序采集（适合回查旧评价的近期修改）。`sample-reviews` 固定按 `created`。 |
| `--review-type TYPE` | `all` | `all` 全部评价；`positive` 仅推荐（好评）；`negative` 仅不推荐（差评）。 |
| `--purchase-type TYPE` | `all` | `all` 不限购买来源；`steam` 仅 Steam 购买；`non-steam` 非 Steam 购买（如 CD Key 激活）。 |
| `--exclude-offtopic` | 默认包含 | 附加该标志后，排除被 Steam 标记为跑题/离题轰炸的评价。 |
| `--min-playtime-hours N` / `--max-playtime-hours N` | 不限 | 按评价发表时的累计游玩时长（小时，非负整数）筛选；同时指定时下限须 `<=` 上限。 |

#### 语言代码格式

`--language` 对应 [Steamworks 语言表](https://partner.steamgames.com/doc/store/localization/languages) 中的 **API language code**。工具同时支持常见简写别名（大小写不敏感），会自动规范化后请求接口：

| 语言 | Steam 标准标识 | 支持的快捷别名 |
| --- | --- | --- |
| 简体中文 | `schinese` | `zh`、`zh-CN`、`zh-Hans`、`中文` |
| 繁体中文 | `tchinese` | `zh-TW`、`zh-Hant` |
| 英文 | `english` | `en` |
| 日语 | `japanese` | `ja` |
| 韩语 | `koreana` | `ko` |
| 俄语 | `russian` | `ru` |
| 德语 | `german` | `de` |
| 法语 | `french` | `fr` |

- 多语言示例：`--language schinese,tchinese,english` 与 `--language zh-CN --language zh-TW --language en` 等价。
- 省略 `--language` 时默认采集全部语言（`all`）。

### 公告专用参数：`news`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--feed NAME` | `steam_community_announcements` | 按 `feedname` 指定新闻源，可重复传参或用英文逗号分隔。 |
| `--all-feeds` | 仅默认官方公告源 | 采集接口返回的全部新闻源（包含第三方媒体报道），与 `--feed` 互斥。 |

> 注：Steam 官方新闻接口不支持按语言过滤，因此 `--language` 仅适用于评价采集。

### 执行与网络参数：`reviews`、`news`、`resume`、`sample-reviews`、`resume-sample`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--max-items N` | `1000` | 仅常规任务（`reviews` / `news` / `resume`）：单任务本次最多处理的匹配条数（`0` 表示不限；内容未变化的已存记录也计入）。 |
| `--max-pages N` | `0` | 本次执行最多新请求的页数（`0` 表示不限；网络重试和读取已缓存页不计入）。达到上限后可续采。 |
| `--interval SECONDS` | `2` | 两次 HTTP 请求开始之间的最小间隔秒数（支持小数）。 |
| `--jitter SECONDS` | `0.5` | 在最小间隔基础上附加 `0`～`N` 秒的随机抖动。 |
| `--timeout SECONDS` | `30` | 单次 HTTP 请求的连接与读写超时时间（秒）。 |
| `--retries N` | `4` | 遇到网络异常或 HTTP `408` / `429` / `5xx` 时的最大重试次数（`0` 表示不重试）。 |
| `--proxy URL` | 读取环境变量 | 指定 HTTP/SOCKS 代理（如 `http://127.0.0.1:7890`）；未指定时自动读取 `HTTP_PROXY` / `HTTPS_PROXY`。 |
| `--ca-bundle FILE` | 系统信任证书库 | 自定义 PEM CA 证书文件路径；也可通过 `SSL_CERT_FILE` 或 `SSL_CERT_DIR` 环境变量配置。 |
| `--user-agent TEXT` | `steam-feedback-crawler/<版本>` | 自定义 HTTP `User-Agent` 请求头。 |
| `--quiet` | 显示进度 | 终端仅输出警告、错误与最终结果，日志文件仍记录完整进度。 |

### 按天采样参数：`sample-reviews` 与 `resume-sample`

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--total N` | **必填** | 整个采样计划的目标评价总条数（正整数）。 |
| `--since DATE_OR_TIME` | **必填** | 评价创建时间的起始边界；可通过 `--until` 指定截止边界（省略时取命令启动时刻）。 |
| `--timezone ZONE` | `Asia/Shanghai` | 按该时区的当地零点（`00:00:00`）切分自然日窗口。 |
| `--quota MODE` | `duration` | 配额分配方式：<br>• `equal`：**按天严格等量分配**，首尾日期与中间日期配额相同（取整后最多相差 1 条）。<br>• `duration`：按每日时间窗口的实际覆盖时长比例分配（完整自然日之间配额相等，首尾不足一天时按时长折算）。 |
| `--fill MODE` | `all` | 缺额回填策略：<br>• `all`：某日不足配额时，先在同月其余有余量的日期均摊补齐，仍有缺额再跨月均摊补齐。<br>• `month`：仅在同月其他日期内补齐。<br>• `none`：不补齐，严格以各日初始配额为上限。 |
| `--plan-only` | 直接开始采集 | 仅计算并保存 `sampling-plan.json`，暂不发起采集；后续用 `resume-sample` 启动。 |
| `--output DIR` | **必填** | 采样专用的独立数据目录（每个采样目录绑定单一 `--app-id`）。 |
| `--max-pages N` | `0` | 本次运行跨所有日期最多新请求的页数（`0` 为不限）。 |

### 任务查询参数：`status`

| 参数 | 适用命令 | 说明 |
| --- | --- | --- |
| `--limit N` | `status` | 列出最近创建的 `N` 个任务（默认 `20`）。 |
| `--task-id ID` | `status` | 查看指定任务的完整查询配置、分页游标位置与待处理缓存条数。 |
| `--sample` | `status` | 查看按天采样计划的整体完成度（与 `--task-id` 互斥）。 |

---

## 数据目录结构与存储格式

`--output` 指定数据根目录。程序在根目录下按数据类型和 UTC 采集日期组织原始批次文件，并用 SQLite 保存断点状态：

```text
data/sts2/
├── collection.log                             # 采集运行日志
├── raw/
│   ├── reviews/ingest_date=YYYY-MM-DD/        # 评价原始批次 (.jsonl 或 .jsonl.gz)
│   └── announcements/ingest_date=YYYY-MM-DD/  # 公告原始批次 (.jsonl 或 .jsonl.gz)
└── state/
    └── crawler.db                             # 任务配置、分页断点与去重摘要数据库
```

按天采样目录（如 `data/cs2-reviews-by-day/`）还会额外生成：

```text
data/cs2-reviews-by-day/
├── sampling-plan.json      # 每日时间窗口与初始配额计划
├── sampling-progress.json  # 采样实时进度与状态汇总
└── sample/
    ├── reviews.jsonl.gz    # 采样完成后导出的总样本（按创建时间倒序）
    └── summary.json        # 每日实际采集条数与语言分布统计
```

### 记录字段说明

- 批次文件命名为 `<任务ID>-batch-<六位序号>.jsonl[.gz]`，其中任务 ID 由数据类型、App ID 与 12 位十六进制随机后缀组成。
- 每行 JSON 对象包含：
  - `payload`：Steam 官方接口返回的原始条目内容
  - `query`：当前任务的查询筛选配置
  - `fetched_at`：UTC 采集时间戳
  - 元数据字段：`schema_version`、`appid`、`item_id`、`source`、`task_id`、`content_hash`
  - 公告记录额外包含 `annotations`：`{"announced_at": <发布时间戳>, "effective_at": null, "time_basis": "announcement_published"}`
- **增量去重机制**：`state/crawler.db` 按条目 ID 记录最新 `content_hash`，后续采集同一游戏时仅将新增或内容发生变更的记录写入新批次。备份数据时请将 `raw/` 与 `state/` 一并保存。`raw/` 下的 `ingest_date=YYYY-MM-DD` 目录结构可直接用于挂载 Hive 分区表。

官方接口文档：[Steam 评价接口 (IUserReviewsService)](https://partner.steamgames.com/doc/webapi/IUserReviewsService) · [Steam 新闻接口 (ISteamNews)](https://partner.steamgames.com/doc/webapi/ISteamNews)

---

## 开发与测试验证

**使用 `uv`：**

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

**不使用 `uv`（在已激活的虚拟环境中安装开发依赖后运行）：**

```powershell
pip install pytest ruff
pytest
ruff check .
ruff format --check .
```
