# 给 Web 端的联调说明（5 分钟接上）

本目录是「检索 + 问答 Agent」模块（本模块负责人维护）。你不需要读 Python 代码，只需要调 3 个 HTTP 接口。

## 一、先把服务跑起来

需要 Python 3.10+（本机 3.14 验证过）。两种方式：

**A. 用预构建数据（推荐，不用 1GB parquet）**

把 `data/` 一起解压到 `agent-retrieval/` 下，然后：

```powershell
cd agent-retrieval
python -m pip install -r requirements.txt
python -m src.cli serve --port 8000
```

**B. 自己构建（有 v1 parquet 时）**

```powershell
python -m pip install -r requirements.txt
python -m tools.build_knowledge --parquet "<v1>\releases\v1\data\cleaned_reviews.parquet" --out data
python -m src.cli serve --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 即成功。自检：

```powershell
curl http://127.0.0.1:8000/health
# {"ok":true,"data_version":"v1","docs":61729,"encoder":"hash-512"}
```

浏览器打开 http://127.0.0.1:8000/ 能看到演示页；`/docs` 是自动生成的接口文档。

## 二、你只需要这 3 个接口

### 1. 版本页初始化

```
GET /api/patches/{patch_id}
```

返回：版本信息、官方改动列表（`items`）、三种语言分组的指标（`metrics.all/chinese/en`）。

```json
{
  "patch": {"patch_id": "sdv_20201221_15", "patch_name": "Stardew Valley 1.5", "release_date": "2020-12-21"},
  "game": {"appid": "413150", "game_name": "星露谷物语"},
  "items": [{"item_id": "S15_01", "title": "姜岛探索", "description": "...", "source_url": "..."}],
  "metrics": {"all": {"before_rate": 0.9745, "after_rate": 0.9842, "change_percentage_points": 0.96,
                      "before_reviews": 1021, "after_reviews": 4802}}
}
```

### 2. “问这个版本”按钮

```
POST /api/agent/ask
Content-Type: application/json; charset=utf-8
```

```json
{"question": "1.5 更新后推荐率降了多少？", "appid": "413150", "patch_id": "sdv_20201221_15"}
```

可选字段：`language`（`all` / `chinese` / `en`）、`phase`（`before` / `release` / `after` / `all`，也兼容 `release_day`）。
不传 `phase` 时会从问题里解析“更新前 / 发布当天 / 更新后”；语言和阶段会同时作用于已复核引文和数据集原文。
排行榜类问题（“哪个版本推荐率下降最多”）不受页面当前版本影响，会直接返回全组排行。

返回里你要用的字段：

| 字段 | 怎么用 |
|---|---|
| `answer` | 直接渲染，纯文本，不含 Markdown |
| `numbers[]` | 渲染数字卡片（`label` / `display` / `sample`） |
| `citations[]` | 渲染出处：`url` 可点，“查看原文”；`verified=true` 时加“已复核”徽标 |
| `links.stats` / `links.evidence` | 跳“查看统计 / 查看原文”，路由是 `/versions/{patch_id}`，你们页面接住即可 |
| `links.evidence_items` | 多条目回答时每个条目一个原文入口；有它就按条目渲染多个“查看原文”，不要再只用 `links.evidence` |
| `status` | `answered` 正常；`need_context` 展示 `suggestions` 当追问按钮；`outside_catalog` 直接显示拒答话术 |
| `evidence_status` | `verified_quotes` / `mixed_evidence` / `dataset_quotes` / `none`，决定徽标和“暂无已复核引文”文案 |

### 3. 版本页“已复核原文”列表（可选）

```
POST /api/retrieve/verified
{"query": "", "patch_id": "sdv_20201221_15", "item_id": "S15_01", "top_k": 3}
```

返回 `verified_quotes` + 每条带 `review_id`、`url`、`verification`。

## 三、页面上的四个状态怎么显示

| 场景 | 显示 |
|---|---|
| `evidence_status=verified_quotes` | 绿色“已复核”徽标 |
| `evidence_status=mixed_evidence` | 不显示统一徽标；按 `citations[].verified` 逐条标“已复核/未复核” |
| `evidence_status=dataset_quotes` | 灰色“未复核”提示（文案在 `answer` 里已写明） |
| `evidence_status=none` | “暂无已复核引文”，不要显示原文列表 |
| `status=need_context` | 把 `suggestions` 渲染成可点的追问 chip |

## 四、联调检查清单

- [ ] `GET /health` 返回 `ok:true`
- [ ] `GET /api/patches/sdv_20201221_15` 能拿到 5 个 items
- [ ] 问“1.5 更新后推荐率降了多少” → `status=answered`，`numbers` 有 3–4 项
- [ ] 问“玩家怎么评价姜岛探索” → `evidence_status=verified_quotes`，`citations` 有 Steam 链接
- [ ] 问“玩家怎么评价海浪效果” → `evidence_status=none`，回答含“暂无已复核引文”
- [ ] 问“今天要带伞吗” → `status=outside_catalog`

## 五、常见坑

1. **请求体必须 UTF-8**：Java 端用 `StandardCharsets.UTF_8`，否则中文乱码。
2. **端口**：我默认 8000；你们 Tomcat 如果也 8000，改 `--port 8010`。
3. **`data/` 必须在 `agent-retrieval/` 下**：所有接口启动时加载它，缺了会直接报错。
4. **别把“未复核”说成“已复核”**：只有 `citations[].verified=true` 才是上游版本分析模块那 469 条。
5. **不要每次请求重启服务**：索引常驻内存，首次启动约 1 秒。

## 六、出问题找谁

- 接口字段 / 回答逻辑：本模块负责人
- 指标口径（推荐率、样本量）：上游版本分析模块
- 页面渲染 / SERVLET：Web 端
