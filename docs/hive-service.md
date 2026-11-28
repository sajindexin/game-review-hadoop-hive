# Hive 服务使用文档

## 连接信息

| 项目 | 值 |
| --- | --- |
| HiveServer2 主机 | `hive.hujiayuan.com` |
| 端口 | `10000` |
| 认证方式 | `NONE` |
| 默认数据库 | `default` |
| 网络 | 项目 Tailscale 网络 |

连接 Hive 后执行 `SELECT 1` 验证连通性。

## 安装和配置

公共包与 `crawler/` 共用 uv workspace 和根目录的 `uv.lock`。工作区需要 Python 3.12+（公共 Hive 包自身支持 Python 3.10+）。安装 `uv` 后，在仓库根目录执行：

```powershell
uv sync --all-packages --locked
$env:HIVE_HOST = 'hive.hujiayuan.com'
$env:HIVE_PORT = '10000'
$env:HIVE_AUTH = 'NONE'
$env:HIVE_DATABASE = 'default'
uv run --locked python -c "from hive_service import get_hive_client; print(get_hive_client().query('SELECT 1 AS ok'))"
```

连接前加入项目的 Tailscale 网络。`.env.example` 列出可设置的环境变量，公共包读取进程环境，无需加载额外的配置库。Linux/macOS 可用 `export` 设置同名变量。配置变更后重启进程。

| 环境变量               | 默认         | 说明                                                       |
| ---------------------- | ------------ | ---------------------------------------------------------- |
| `HIVE_HOST`            | 必填         | HiveServer2 主机                                           |
| `HIVE_PORT`            | `10000`      | Thrift TCP 端口                                            |
| `HIVE_USERNAME`        | 当前系统用户 | Hive 会话用户名，按团队服务端配置指定                      |
| `HIVE_AUTH`            | `NONE`       | 已验证的 SASL PLAIN 无密码认证，另支持 `NOSASL`            |
| `HIVE_DATABASE`        | `default`    | 会话默认库，SQL 使用显式库名以便跨库查询                   |
| `HIVE_CONNECT_TIMEOUT` | `10`         | 连接及会话初始化的单次网络 I/O 超时，单位秒                |
| `HIVE_QUERY_TIMEOUT`   | `60`         | 查询执行和取数时间预算，单位秒，同时设置 Hive 会话查询超时 |
| `HIVE_MAX_ROWS`        | `10000`      | 默认最多返回的行数，超出会抛出异常                         |
| `HIVE_CACHE_ENTRIES`   | `32`         | 单进程缓存的最大条目数，`0` 禁用                           |

## Python 接口

```python
from hive_service import get_hive_client

hive = get_hive_client()
rows = hive.query(
    """
    SELECT patch_id AS patch_id, release_date AS release_date
    FROM steam_dw.dim_patch
    WHERE appid = %(appid)s
    ORDER BY release_date
    """,
    {"appid": "413150"},
    max_rows=100,
    cache_ttl=60,
)
```

- `get_hive_client()` 延迟读取环境，返回进程内共享客户端。也可以在测试或命令行脚本中用 `HiveClient(HiveConfig(...))` 显式初始化。
- `query(sql, params=None, *, max_rows=None, cache_ttl=0)` 返回 `list[dict]`，无匹配时为 `[]`。保留 Hive/PyHive 返回的值类型（含 `None`、日期时间、Decimal）。SQL 建议使用独立列别名，以避免 Hive 返回限定列名或同名列。
- 参数使用 PyHive 的 `%s`（位置参数元组/列表）或者 `%(name)s`（字典参数）。参数用于值，库名、表名、列名、排序方向等 SQL 结构必须由受控模板或白名单提供。参数化 SQL 中的字面百分号需写成 `%%`。
- `query()` 接受 SELECT、WITH、SHOW、DESCRIBE 查询模板。应用只从可信代码传入 SQL，外部输入只作为值绑定；数据库权限由 HiveServer2 控制。
- 每次查询独立打开会话与游标，允许线程共享客户端实例。查询采用异步执行及轮询，执行超时会尝试取消操作；查询、读取结果和网络 I/O 失败后都会释放本地资源。网络断开时服务端操作的清理由 HiveServer2 的超时和会话机制负责。DNS、会话初始化和资源清理可能使请求总耗时超过查询预算。
- 大数据查询应优先用 Hive 聚合、限制行数或按稳定键分批读取。Agent 如需加载 62,007 条语料，可以显式设置 `max_rows=70000`，并根据运行情况调整超时。超过上限会抛异常，避免悄悄丢失数据。

## 缓存机制

Hive 客户端提供进程内内存缓存，适合使用频繁且更新较少的维表、小型聚合查询。

```python
from hive_service import get_hive_client

hive = get_hive_client()
sql = "SELECT patch_id AS patch_id FROM steam_dw.dim_patch WHERE appid = %s"
rows = hive.query(sql, ("413150",), cache_ttl=60)
# 同一查询在 60 秒内可复用缓存结果
rows = hive.query(sql, ("413150",), cache_ttl=60)
```

| 项目 | 行为 |
| --- | --- |
| `cache_ttl` | 缓存有效期，单位秒，默认 `0`（关闭） |
| `HIVE_CACHE_ENTRIES` | 每个客户端的最大缓存条目数，默认 `32`，设为 `0` 禁用 |
| 缓存键 | SQL、查询参数、行数上限和 TTL |
| 缓存条件 | 成功返回且结果不超过 1,000 行 |
| 淘汰策略 | 超期时重新查询，容量满时淘汰最久未使用的条目 |
| 作用范围 | 客户端实例内部，`get_hive_client()` 在同一进程共享实例 |

缓存由线程锁保护，返回结果的修改不会影响已保存的结果。需要读取最新数据时，调用 `hive.query(sql, params, cache_ttl=0)`。数据重载后可调用 `hive.clear_cache()` 清空当前客户端所有缓存，或重启进程。

## HTTP API

Hive 健康检查由 Web 后端提供。在已配置 Hive 环境变量的终端中，从仓库根目录启动服务：

```powershell
uv run --locked python steam-review-web/server.py --port 8021
```

在另一终端查询健康状态：

```powershell
Invoke-RestMethod http://127.0.0.1:8021/api/health/hive
```

`GET /api/health/hive` 每次执行真实的 `SELECT 1 AS ok` 查询，无需请求参数。响应类型为 `application/json`，连接成功时返回 HTTP 200：

```json
{"status":"ok","source":"hive"}
```

失败时返回对应 HTTP 状态和 JSON 错误信息：

| HTTP 状态 | `error.code` | 含义 |
| --- | --- | --- |
| 400 | `invalid_request` | 请求包含不支持的查询参数 |
| 503 | `hive_unavailable` | Hive 客户端未安装、配置有误或服务不可用 |
| 504 | `hive_timeout` | Hive 连接或查询超时 |
| 500 | `hive_query_failed` / `internal_error` | 查询失败或服务内部错误 |

例如连接超时时：

```json
{"error":{"code":"hive_timeout","message":"Hive 查询超时"}}
```

现有问答接口 `POST /api/ask` 参见 [Web 接口文档](../steam-review-web/README.md#接口契约)。

## 异常处理

| 异常                                          | 处理约定                             |
| --------------------------------------------- | ------------------------------------ |
| `HiveConfigurationError`                      | 检查环境变量配置                     |
| `HiveUnavailable`                             | 检查 Tailscale、服务状态和 Hive 会话 |
| `HiveTimeout`                                 | 检查查询成本、超时配置或网络         |
| `HiveQueryError`                              | 检查 SQL、参数或列别名               |
| `HiveResultTooLarge`（继承 `HiveQueryError`） | 调整聚合、分页或明确提高结果上限     |

所有异常继承 `HiveError`。底层异常保留在 `__cause__` 供开发调试。公共客户端不会自动重试昂贵查询。

## 已有数据库和表

以下是 2026-11-28 实际查询确认可见的部分数据表。

| 数据库 | 主要数据表 |
| --- | --- |
| `game_reviews` | `reviews_v1`（12 款游戏，4,884,580 条评测） |
| `steam_dw` | `dim_patch`、`dim_item`、`ads_patch_daily`、`ads_patch_stage`、`ads_patch_compare` 及 NLP ODS/ADS 表 |
| `nlp_topic` | `topic_distribution`、`topic_distribution_en`、中英及 RBT3 主题明细与汇总、条目情感表 |
| `hadoop_delivery` | `review_coverage`、`candidate_scores`、`verified_quotes`（469 条引文） |
| `game_review_upload` | `gr_agent_corpus_62007`（62,007 条）、`gr_lexicon_items`、`gr_lexicon_global`、`gr_manifest` |

可以查询数据库、表清单及字段信息：

```python
print(hive.query("SHOW DATABASES"))
print(hive.query("SHOW TABLES IN steam_dw"))
print(hive.query("DESCRIBE steam_dw.ads_patch_compare"))
```

## 验证

```powershell
uv run --locked python -m unittest discover -s tests -v
```
