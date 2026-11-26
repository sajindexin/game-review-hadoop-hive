# Steam 评测分析台 · 前端

复刻 Steam 商店 / 社区视觉语言的静态前端，用来演示「选游戏和版本 → 查指标 → 提问 → 看引文 → 点开原文」这条完整链路。
数据全部来自本仓库已有的离线产物，**不依赖任何 CDN、不用构建工具、不用打包**。

## 目录

```
steam-review-web/
├─ index.html            商店总览：焦点更新、游戏胶囊墙（带筛选）、26 个版本一览表
├─ patch.html            版本详情：概览 / 改动条目 / 评测样本 / 问答入口 四个标签页
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
│  └─ patches/*.js       每个版本一个数据包（趋势、阶段统计、条目 + 引文、评论样本）
├─ screenshots/          页面与联调步骤截图
├─ docs/
│  ├─ 联调记录.md        五个步骤的实跑记录与实际结果
│  └─ resp-*.json        五类问题的真实接口响应
└─ tools/
   └─ export_web_data.py 从 parquet + 组内 CSV/JSONL 生成 data/ 的导出脚本
```

## 启动步骤

```bash
cd steam-review-web
python server.py --port 8021
```

打开页面入口 <http://127.0.0.1:8021/index.html>。
同时具备问答接口 `POST http://127.0.0.1:8021/api/ask`，页面与接口同源，无需处理跨域。

只想看静态页、不跑接口时，`python -m http.server 8000` 或直接双击 `index.html` 也可以，
此时问答页会自动回退到本地规则引擎。

## 页面入口

| 入口 | 地址 | 内容 |
| --- | --- | --- |
| 商店总览 | `/index.html` | 焦点更新、游戏胶囊墙、筛选工具条、26 个版本一览表 |
| 按口碑筛选 | `/index.html?q=星露谷` | 关键词过滤游戏与版本表 |
| 版本详情 | `/patch.html?id=sdv_20201221_15` | 统计卡、推荐率趋势、每日评论量、三阶段、分语种 |
| 改动与引文 | `/patch.html?id=sdv_20201221_15&item=S15_01` | 展开指定改动条目的引文面板 |
| 评测样本 | `/patch.html?id=sdv_20201221_15#reviews` | 按阶段 / 极性筛选，每条带 Steam 原文链接 |
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

## 接口契约

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

识别意图：`overall` / `language` / `items` / `trend` / `negative`。

接 Java Servlet 时，把服务地址填进 `assets/ask.js` 的 `S.backendBase` 即可（`S.useBackend` 在 http(s) 下默认开启）：

```js
S.useBackend = true;
S.backendBase = "http://localhost:8080/review-api";
```

## 联调记录

见 [`docs/联调记录.md`](docs/联调记录.md)，含五个步骤的截图、接口实际响应与数据核对结论。

## 已知限制

- 封面为程序化渐变色块，不含真实游戏美术资源，避免版权与外部依赖。
- 页面样式模仿 Steam 仅为课程演示，与 Valve 无关。
- 分语种统计中小语种样本可能只有几十条，页面已提示不宜直接下结论。
- 「金核桃解锁」「火山地牢」在 1.5 窗口内没有任何评测提及，页面如实显示 0，不做补数。
