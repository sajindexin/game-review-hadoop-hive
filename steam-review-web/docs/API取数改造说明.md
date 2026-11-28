# 页面数据改走 API

**日期**：2026-11-28　**分支**：`feat/api-data-loading`　**提交**：`7802603`

## 改了什么

页面原来这样取数：

```html
<script src="data/index.js"></script>
<script src="data/patches/sdv_20201221_15.js"></script>
```

每个数据文件内容是 `window.SRW_INDEX = {...};`，靠脚本注入全局变量。

现在改成：

```js
const index = await fetch("/api/index").then(r => r.json());
```

## 为什么值得改

原方式三个实际问题，不是"为了显得现代"：

**1. 全局命名空间互相覆盖。**
`data/verified.js` 注入 `window.SRW_VERIFIED`，`patch.js` 里直接读它。
如果某个页面加载顺序变了或重复加载，就会读到上一个版本的数据。
之前调 NLP 页时就踩过 `SRW_PATCH` 被复用的坑。

**2. 无法按需取数。**
`verified.js` 是 403 KB，包含 26 个版本的全部 469 条引文。
但版本页打开时只需要当前那一个版本的 39 条。脚本方式做不到过滤，只能整包下载。

**3. 没有缓存协商。**
`<script>` 标签的缓存完全靠浏览器默认策略，没法按内容摘要做条件请求。

改成接口后这三个问题都解决了，其中第 2 条带来 92% 的传输量下降。

## 接口清单

| 接口 | 内容 | 体积 | 缓存 |
| --- | --- | ---: | --- |
| `GET /api/meta` | 12 款游戏 + 26 个版本 ID | 1.7 KB | ETag |
| `GET /api/index` | 完整总览 | 9 KB | ETag |
| `GET /api/patch/<id>` | 单版本数据包 | 平均 23 KB | ETag |
| `GET /api/nlp/topics` | 11 类主题情感切片 | 145 KB | ETag |
| `GET /api/nlp/items` | 75 项改动统计 | 34 KB | ETag |
| `GET /api/verified` | 已复核引文（可过滤） | 403 KB | ETag |
| `GET /api/verified?patch_id=<id>` | 单版本引文 | 31 KB | ETag |
| `GET /api/health/hive` | Hive 探活 | 极小 | 不缓存 |

## 关键实现

### 不改变数据结构

接口只是「读文件 → 取 JSON → 原样返回」：

```python
def _read_js_payload(path):
    text = path.read_text(encoding="utf-8")
    m = _ASSIGN.match(text)          # 匹配 window.SRW_X =
    body = text[m.end():].strip()    # 切掉赋值部分
    if body.endswith(";"):
        body = body[:-1]
    return json.loads(body)          # json.loads 而非 eval
```

**为什么不用 eval**：这些文件虽是构建产物，但用 `eval` 就等于允许执行任意代码。
按固定前缀切掉再 `json.loads` 已经够用，且零风险。

因为返回结构与原来完全一致，`index.js`、`patch.js`、`nlp.js` 的渲染逻辑一行没改。

### ETag 协商缓存

```python
digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]
etag = f'W/"{digest}"'
if self.headers.get("If-None-Match") == etag:
    self.send_response(304)   # 空体，省掉整个响应
    return
```

用内容摘要而非文件 mtime：数据重新导出但内容没变时，ETag 不变，浏览器继续用缓存。

### 按版本过滤引文

```
GET /api/verified                      → 412,032 字节（469 条）
GET /api/verified?patch_id=sdv_..._15  →  31,760 字节（39 条）
```

省 92%。这是接口相对静态脚本最直接的收益。

未登记的 `patch_id` 返回**空数组**而非 404 —— 这表示「该版本没有已复核引文」，
是正常业务状态，不该当错误。但 `/api/patch/<id>` 找不到版本时返回 404，因为那是请求了不存在的资源。

### 保留 file:// 离线能力

```js
const IS_HTTP = /^https?:$/.test(location.protocol);

async function fetchJson(apiPath, fallbackScript, sourceKey) {
  if (IS_HTTP) {
    try { ... fetch(apiPath) ... } catch (e) { /* 网络异常 → 继续回退 */ }
  }
  await SRW.loadScript(fallbackScript);   // file:// 或接口异常
  return window[sourceKey];
}
```

把整个目录拷走双击 `index.html` 仍能看图，不用起服务。这对交作业很实用。

**接口 404 不回退**：那是业务错误（版本不存在），回退到脚本只会让错误更难懂。

### 错误格式统一

```json
{ "error": { "code": "patch_not_found", "message": "找不到版本：nope" } }
```

| 场景 | 状态码 | code |
| --- | --- | --- |
| 未知路径 | 404 | `not_found` |
| 版本不存在 | 404 | `patch_not_found` |
| patch_id 含非法字符 | 400 | `invalid_patch_id` |
| 不支持的查询参数 | 400 | `invalid_request` |

`patch_id` 限定 `[A-Za-z0-9_]`，防止 `/api/patch/../../etc/passwd` 读到数据目录外的文件。

## 验证

### 单元测试（13 项新增，合计 21 项全过）

```
test_verify_*_matches_source        5 类数据与源文件逐字节一致
test_every_patch_reachable          26 个版本包全部可读且内容一致
test_verified_filtered_by_patch     过滤结果是全量子集，且与预期筛选逐条相等
test_verified_unknown_patch_empty   未登记版本返回空数组
test_meta_is_lightweight            元信息 < 20 KB 且只含 ID 清单
test_path_traversal_rejected        ../ 穿越被拒
test_missing_patch_404 / test_unknown_route_404 / test_query_rejected
```

### 端到端（CDP 抓包）

`tools/check_api_usage.js` 会拦截真实浏览器的网络请求，确认页面走的是 API：

```
=== index.html
  /api 请求 1 个: /api/index
  data/*.js 请求 0 个
  → 走 API ✓
=== patch.html
  /api 请求 3 个: /api/index, /api/verified?patch_id=sdv_20201221_15, /api/patch/sdv_20201221_15
  data/*.js 请求 0 个
  → 走 API ✓
=== nlp.html
  /api 请求 3 个: /api/nlp/topics, /api/nlp/items, /api/index
  data/*.js 请求 0 个
  → 走 API ✓
```

这个检查比"页面看起来正常"更有说服力：如果回退了，`data/*.js` 会有请求记录。

### file:// 回退

```
file:///.../index.html  →  13 个胶囊、26 行版本表、无报错
```

### 功能未回归

| 检查 | 结果 |
| --- | --- |
| 星露谷 1.5 标题与 97.5/98.4 | 正确 |
| 引文展开面板 | 正常，15 条原文链接 |
| NLP 页 11 主题 / 325 命中 | 与改造前一致 |
| NLP 交互（语种 325→2682、阶段→3344） | 一致 |
| `/api/ask` 问答 | intent=language，3 条引文 |

## 复跑验证

```bash
cd steam-review-web
python server.py --port 8021 &

# 单测
python -m unittest test_dataset_api test_server

# 接口抽查
curl -s localhost:8021/api/meta | head -c 200
curl -s -D - -o /dev/null localhost:8021/api/index | grep -i etag

# 抓包确认页面走 API（需先起 Chrome 调试端口）
chrome --headless=new --remote-debugging-port=9344 about:blank &
node tools/check_api_usage.js
```

## 遗留

- `data/*.js` 仍然保留，作为 `file://` 回退用。如果以后不再需要离线单页，
  可以改成只保留 `.json` 数据文件，省掉 `_read_js_payload` 的字符串切割。
- `/api/verified` 无过滤时仍返回 403 KB。如果有页面确实需要全量，建议再拆一个
  `/api/verified?summary=1` 只返回计数。
