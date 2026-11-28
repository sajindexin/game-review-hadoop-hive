# Vue 3 迁移说明

把前端从「4 个 HTML + 2,000 行 IIFE 脚本」改成 Vue 3 单文件组件，页面表现与数据保持不变。

## 为什么改

原来的写法是每页一个 IIFE，靠 `innerHTML` 拼字符串、再用 `querySelector` 回头绑事件。三个明显问题：

1. **状态散落**：`index.js` 里用 `keyword`、`gFilter`、`gSort`、`sortKey` 四个模块级变量，改一个要手动调两次渲染函数，容易漏。
2. **拼接难维护**：`nlp.js` 里近 60 行模板字符串，缩进和引号嵌套后基本没法读，改一处字段要数括号。
3. **事件重绑**：每次 `renderItems()` 都要重新查 DOM 再绑一次 click，列表重渲染就重复绑一遍。

Vue 版把这三件事分别交给 `ref/computed`、模板、`@click`，逻辑量没变但结构清楚了。

## 目录对照

| 旧 | 新 |
| --- | --- |
| `index.html` + `assets/index.js` | `src/views/HomeView.vue` |
| `patch.html` + `assets/patch.js` | `src/views/PatchView.vue` |
| `nlp.html` + `assets/nlp.js` | `src/views/NlpView.vue` |
| `ask.html` + `assets/ask.js` | `src/views/AskView.vue` |
| `assets/steam.js`（工具 + 导航 + 图表） | 拆成 `src/lib/format.js`、`art.js`、`chart.js`，导航变 `components/GlobalHeader.vue` |
| `assets/steam.css` | `src/styles/steam.css`（原样迁入，类名未改） |
| — | `src/api/index.js`：接口封装 |
| — | `src/lib/answers.js`：问答 HTML 拼装 + 本地规则引擎 |
| — | `src/router/index.js`：路由与旧链接重定向 |

## 关键决策

### 样式一行没改

`steam.css` 1,591 行原样复制到 `src/styles/`，模板沿用原来的类名（`.capsule`、`.topic-row`、`.evi` 等）。
好处是视觉零回归，截图能直接和旧版对比；代价是模板里有一批 `style="..."` 内联样式，
没有抽成 scoped CSS。

### 图表仍是原生 SVG

`chart.js` 是把原来的 `lineChart` / `barChart` 函数搬过来，改成接收宿主元素的纯函数，
再用两个薄组件（`LineChart.vue` / `BarChart.vue`）在 `onMounted` 和 `watch` 时调用。

没有引 ECharts：这两张图只有折线、面积、柱状，手写 SVG 约 150 行，比引入 300 KB 的图表库划算，
而且悬浮提示、更新日高亮带这些细节原实现已经调好了。

### 问答回答仍用 v-html

`/api/ask` 返回的是可直接渲染的 HTML 片段（这是既有的接口契约），所以 `AskView` 里用 `v-html`。

**这里没有引入新的注入面**：内容来自自己的后端或本地数据包，和旧版 `innerHTML` 完全等价。
但要注意两点，改代码时别破坏：

- 用户输入的问题用 `esc()` 转义后再拼进气泡，不直接塞进 `v-html`
- `lib/answers.js` 里所有插值都过了 `esc()`，除了后端返回的 `json.answer` 本身

### 保留旧链接

历史文档、截图、PPT 里都是 `patch.html?id=sdv_20201221_15` 这种地址。路由表里做了重定向：

```js
{ path: '/patch.html', redirect: (to) => ({ path: '/patch/' + (to.query.id || 'latest'), ... }) }
```

`server.py` 的 `translate_path()` 会把不存在的 `.html` 也交给 `index.html`，
所以旧地址能 200 打开并跳到新路由。带其它扩展名（`.js`、`.css`）的请求找不到就照常 404，
避免把 JS 请求错误地返回一段 HTML。

### 数据仍走 API，不做编译期内联

`data/*.js` 那 900 KB 没有打进 bundle，运行时还是 `fetch('/api/index')` 这类请求。

原因：这些文件是导出脚本的产物，会随数据更新而变。内联进 bundle 意味着每次数据更新都要重新
`npm run build`，而且失去 `ETag` 协商缓存（第二次访问 304 空体，比传 145 KB 快得多）。

## 验证记录

迁移后逐项对照，数字与迁移前完全一致：

| 检查 | 迁移前 | 迁移后 |
| --- | --- | --- |
| 首页胶囊 | 12 款 | 12 款 |
| 首页版本表 | 26 行 | 26 行 |
| 版本页推荐率 | 97.5% → 98.4% | 97.5% → 98.4% |
| 版本页统计卡 | 4 张 | 4 张 |
| 版本页三阶段 | 3 张 | 3 张 |
| 版本页改动条目 | 5 项 | 5 项 |
| 版本页引文 | 15 条链接 | 15 条链接 |
| NLP 主题行 | 11 类 | 11 类 |
| NLP 命中数 | 325 | 325 |
| NLP 负向率排序 | 77% / 30% / 17% | 77% / 30% / 17% |
| NLP 语种切换 | 325 → 2,682 | 325 → 2,682 |
| NLP 阶段切换 | → 3,344 | → 3,344 |
| NLP 条目负向排序 | 72 / 8 / 6 | 72 / 8 / 6 |
| 单测 | 27 项 | 27 项 |

CDP 抓包确认三个页面全部走 `/api`，`data/*.js` 请求 0 个：

```
=== index.html   /api/index
=== patch.html   /api/index, /api/verified?patch_id=..., /api/patch/...
=== nlp.html     /api/nlp/topics, /api/nlp/items, /api/index
```

## 顺手修的两处

**分语种列表去掉聚合项。** `language_compare` 里第一行是 `lang: "all"` 的聚合值，
旧版把它和「简中+繁中」「英语」并列显示，读起来像三个语种。现在过滤掉 `all`。

**NLP 改动统计标题随筛选变化。** 旧版标题写死「75 项官方改动」，筛选到单个版本后
表格只有 5 行，标题和内容对不上。现在选版本时显示「5 项官方改动的反馈统计」。

## 怎么改前端

```bash
cd steam-review-web/frontend
npm install          # 首次
npm run dev          # 开发，http://127.0.0.1:5180，/api 代理到 8077
npm run build        # 发布，输出到 ../site
```

`npm run dev` 前需要另开终端跑 `python server.py --port 8077`，代理才有目标。

## 已知取舍

**没有 TypeScript。** 项目里的数据是嵌套很深的 JSON（`by_patch[patchId][lang][phase][aspect]`），
写类型定义的工作量接近重写一遍解析逻辑，收益不明显。

**没有 Pinia。** 四个页面之间没有共享状态，路由参数就是唯一的状态来源。加 store 只会多一层间接。

**没有组件测试。** 图表是命令式 SVG，单测价值低；交互用 `tools/test_nlp_page.js` 的 CDP 脚本
真点真查，比 jsdom 里的假点击更可信。
