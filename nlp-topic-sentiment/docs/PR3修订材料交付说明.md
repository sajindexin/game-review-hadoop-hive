# PR #3 修订材料交付说明（中文主题与情感分析）

> 对应分支：`nlp-topic-sentiment`；基础提交 `ea3baa2`（feat:中文 NLP 与专题统计）。
> 本文件汇总报告所需的三样材料：①三份新版 CSV；②Hive 查询与结果；③复跑信息。
> 数据批次：`cleaned_reviews.parquet` v1（团队包 `游戏评论交付/releases/v1/data/`），
> 中文评论 165.7 万行、12 款游戏；所有时间口径 UTC。

---

## 一、三份新版 CSV

路径（相对仓库根 `game-review-hadoop-hive/`）：

| 文件 | 行数 | 说明 |
|---|---|---|
| `nlp-topic-sentiment/data/result/item_sentiment_detail.csv` | 1,593 | 明细：一条评论 × 一个具体改动一行 |
| `nlp-topic-sentiment/data/result/item_distribution.csv` | 68 | 分布：每个 (item_id, sentiment) 的条数 |
| `nlp-topic-sentiment/data/result/item_patch_summary.csv` | 30 | 汇总：每个条目的四个阶段条数 |

### 1.1 修订口径（相对 PR 旧版）

- **patch_id 按条目自身所属版本填**：不再按评论日期去猜同游戏版本。
  每条改动来自黄静雯版本建模分支 `items.jsonl`，自带 `patch_id`，直接采用。
  全量 1,593 条的 patch_id 已逐一校验等于 taxonomy 中该条目的 patch_id，
  跨游戏错归（如"姜岛"错归到别的游戏版本）已消除。
- **汇总固定四阶段、零条补零**：`item_patch_summary.csv` 每行固定含
  `pre_14d` / `release_day` / `post_14d` / `out_of_window` 四列，
  某阶段没有记录时写 `0`，不再只留 `post_14d` 一列。
- **窗口按该条目 patch 的发布日期计算**：
  `pre_14d` = [window_start, release_date)，`release_day` = 发布日当天，
  `post_14d` = (release_date, window_end]，`out_of_window` = 其余日期。
  全量复算零 mismatch。
- **未判定（none）统一回退为 neutral**：条目层没有情感证据时不再写 `none`，
  一律记为 `neutral`。因此明细中已无 `none`，分布表合计由旧版 1,225 修正为
  **1,593**，与明细行数完全一致。

### 1.2 姜岛探索 S15_01（星露谷 1.5，`sdv_20201221_15`）分阶段统计

总条数 **634**，全部归到星露谷 1.5（旧版有 9 条误归到 1.6 窗口，已修正）。

| 阶段 | 条数 | 正向 | 中性 | 负向 |
|---|---:|---:|---:|---:|
| 更新前 14 天（pre_14d） | 1 | 1 | 0 | 0 |
| 发布日（release_day） | 0 | 0 | 0 | 0 |
| 更新后 14 天（post_14d） | 2 | 0 | 1 | 1 |
| 窗口外（out_of_window） | 631 | 213 | 347 | 71 |
| **合计** | **634** | **214** | **348** | **72** |

> 绝大多数评论落在窗口外，是因为清洗后的评论日期多为 2024–2026 年，
> 而星露谷 1.5 发布于 2020-12-21；这是数据本身的时间分布，不是归窗错误。

**未判定记录怎么处理**：全部回退为 `neutral`（见 1.1）；S15_01 的 348 条中性里
包含原本无情感证据的 `none`，没有丢弃任何一条。

**占比用哪个分母**（对应指标 9 的 SQL）：
`正向占比 = SUM(sentiment='positive') / COUNT(*)`，`负向占比` 同理，
分母是**该 (item_id, window_bucket) 分组内的全部明细条数**（含 neutral）。
例如 S15_01 窗口外正向占比 = 213 / 631 = 0.3376；
`release_day` 分母为 0，SQL 中会出现 NULL，报告里按"无数据"处理即可。

---

## 二、Hive 查询与结果

SQL 源文件：`nlp-topic-sentiment/sql/hive_metrics.sql`。
建表语句为 part 0 的 OpenCSVSerde 四张 raw 表 + CAST 分析表；
指标 7、指标 11 见该文件对应章节。

### 2.1 修订后的建表语句（CSV 导入）

```sql
CREATE DATABASE IF NOT EXISTS nlp_topic;
USE nlp_topic;

-- 原始层：OpenCSVSerde 支持引号与字段内逗号，跳过表头
-- 注意 escapeChar 必须是数据里不出现的字符（这里用 '!'）：交付 CSV 按 RFC4180
-- 用双写引号 "" 转义，若设 '\\' 会破坏 aspects_json 里的 \"（已用 OpenCSV 2.3 实测）。
CREATE EXTERNAL TABLE item_sentiment_detail_raw (
  review_id STRING, appid STRING, game_name STRING, created_date STRING,
  game_version STRING, patch_id STRING, window_bucket STRING, item_id STRING,
  item_title STRING, aspect STRING, sentiment STRING, confidence STRING,
  matched_term STRING, evidence STRING
) ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
  WITH SERDEPROPERTIES (
    "separatorChar" = ",", "quoteChar" = "\"", "escapeChar" = "!"
  )
  STORED AS TEXTFILE
  LOCATION '/data/nlp_topic/item_sentiment_detail'
  TBLPROPERTIES ("skip.header.line.count"="1");

-- 分析层：显式 CAST 数值字段
CREATE TABLE item_sentiment_detail STORED AS ORC AS
SELECT review_id, appid, game_name, created_date, game_version,
       patch_id, window_bucket, item_id, item_title, aspect, sentiment,
       CAST(confidence AS DOUBLE) AS confidence, matched_term, evidence
FROM item_sentiment_detail_raw;
```

> 另外三张 raw 表（`topic_sentiment_detail_raw`、`topic_sentiment_review_raw`、
> `item_dict_raw`）写法相同，完整语句见 `sql/hive_metrics.sql` 第 26–110 行。

### 2.2 指标 7（主题共现）

```sql
SELECT
  COUNT(DISTINCT review_id) AS `同时提到内容+联机的评论数`
FROM topic_sentiment_detail a
WHERE a.aspect = 'content'
  AND EXISTS (
    SELECT 1 FROM topic_sentiment_detail b
    WHERE b.review_id = a.review_id AND b.aspect = 'multiplayer'
  );
```

结果：`同时提到内容+联机的评论数 = 10840`。

### 2.3 指标 11（两层映射验算，含匹配/未匹配）

```sql
SELECT
  i.aspect AS `归属通用主题`,
  COUNT(*) AS `条目层标注数`,
  COUNT(DISTINCT i.review_id) AS `涉及评论数`,
  SUM(CASE WHEN t.review_id IS NOT NULL THEN 1 ELSE 0 END) AS `主题层匹配数`,
  SUM(CASE WHEN t.review_id IS NULL THEN 1 ELSE 0 END) AS `主题层未匹配数`,
  ROUND(SUM(CASE WHEN t.review_id IS NOT NULL THEN 1 ELSE 0 END)/COUNT(*), 4) AS `匹配占比`,
  ROUND(SUM(CASE WHEN i.sentiment='positive' THEN 1 ELSE 0 END)/COUNT(*), 4) AS `条目层正向占比`
FROM item_sentiment_detail i
LEFT JOIN (
  SELECT DISTINCT appid, review_id, aspect FROM topic_sentiment_detail
) t ON i.appid = t.appid AND i.review_id = t.review_id AND i.aspect = t.aspect
GROUP BY i.aspect
ORDER BY `条目层标注数` DESC;
```

结果（本地等价复算，导出见 `data/result/hive_export/metric11_result.csv`）：

| 归属通用主题 | 条目层标注数 | 涉及评论数 | 主题层匹配数 | 主题层未匹配数 | 匹配占比 | 条目层正向占比 |
|---|---:|---:|---:|---:|---:|---:|
| content | 1138 | 1031 | 411 | 727 | 0.3612 | 0.3805 |
| controls | 336 | 336 | 130 | 206 | 0.3869 | 0.3393 |
| level_design | 90 | 87 | 27 | 63 | 0.3000 | 0.4000 |
| monetization | 15 | 15 | 5 | 10 | 0.3333 | 0.2000 |
| multiplayer | 11 | 11 | 8 | 3 | 0.7273 | 0.5455 |
| story | 3 | 3 | 2 | 1 | 0.6667 | 0.6667 |
| **合计** | **1593** | — | **583** | **1010** | — | — |

结果导出文件：
- `nlp-topic-sentiment/data/result/hive_export/metric7_result.csv`
- `nlp-topic-sentiment/data/result/hive_export/metric11_result.csv`

> **结果来源说明（重要）**：本机没有部署 Hive，仓库也没有历史实跑截图。
> 上表是用同一批交付 CSV、按与 SQL 完全相同的逻辑在本地等价复算的结果，
> 数字可复现，但**不是 Hive 引擎实跑输出**。如需"Hive 实跑截图"，
> 请在装有 Hive 的环境执行 2.1–2.3 的语句后自行截图；
> 复算脚本见 `code/` 下对应逻辑，可按需改成 Hive 执行。
> 我不提供伪造的"实跑截图"。

---

## 三、复跑信息

### 3.1 代码分支 / 提交

- 分支：`nlp-topic-sentiment`（远端 `origin/nlp-topic-sentiment`）
- 基础提交：`ea3baa2` `feat:中文 NLP 与专题统计`
- PR #3 修订即本目录当前未提交的工作区改动（8 项审查意见的修复），
  提交信息建议：`fix: address review findings for NLP module`

### 3.2 数据批次

- 原始评论：`cleaned_reviews.parquet` v1，来自团队包
  `游戏评论交付/releases/v1/data/`；清样后中文评论 165.7 万行、12 款游戏。
- 版本/条目来源：黄静雯版本建模分支 `game-update-feedback-analysis`，
  75 项官方更新条目 + 26 个版本窗口（`items.jsonl` / `patches.jsonl`），
  已固化到 `data/taxonomy/`（`items_enriched.json` / `patches.json` / `item_dict.csv`）。

### 3.3 运行命令（PowerShell）

```powershell
$REPO = (Get-Location).Path          # 在仓库根目录执行
$PY   = "python"                     # 换成你自己的解释器路径
Set-Location "$REPO\nlp-topic-sentiment\code"
$env:HF_ENDPOINT = "https://hf-mirror.com"

# 1) 重建条目词典（版本源默认按仓库相对路径查找，找不到则用已提交 taxonomy）
& $PY build_item_taxonomy.py

# 2) 重跑三份 CSV（约 4-6 分钟）
& $PY build_item_results.py            # 明细 / 分布 / 四阶段汇总
& $PY build_topic_results.py           # 第一层主题结果（指标 7 依赖）

# 3) 主题层训练数据（按评论切分，train/eval 零重叠）
& $PY prepare_rbt3_data_v2.py --source gold

# 4) RBT3 微调（3 轮；把本次 eval 评论 ID 写入模型目录 holdout_ids.json）
& $PY train_rbt3_v2.py --task aspect --train ..\data\annotation\rbt3_aspect_train.csv --eval ..\data\annotation\rbt3_aspect_eval.csv --epochs 3

# 5) 严格留出集评估（自动读模型目录 holdout_ids.json）
& $PY evaluate_full.py --rbt3 --heldout

# 6) 推理（缺权重自动规则回退）
& $PY infer_rbt3_v2.py --text "姜岛探索很好玩，但是八人联机老是掉线" --appid 413150
```

### 3.4 情感分析方法

两层方法，报告里按"主方法 + 对照"写：

- **第一层（11 类通用主题）**：最终采用 **RBT3 金标微调的三分类**
  （positive / negative / neutral）。基座 `hfl/rbt3`，输入为
  `评价方面：<主题中文名> + [SEP] + 评论文本`，输出该主题的情感。
  推理带规则召回门控（先用规则/官方别名筛出真正命中的主题，再交给模型判情感）。
- **第二层（75 项官方改动）**：**没有人工金标**，采用规则引擎
  （appid 约束 + 官方别名匹配 + 情感词 + 就近绑定），即银标口径；
  明细/分布/汇总三份 CSV 都是这一层的结果。条目层不提供 RBT3 模型，验收用规则模式。
- **对照基线**：`voted_up`（整体好评当主题情感）、`rule_absm`（纯规则方面级）。
  全量结果表（535,623 条主题标注）由规则弱标注生成，属银标，用于专题统计而非模型评估。

### 3.5 RBT3 训练 / 测试划分

- 金标：语义理解逐条标注 240 条评论（跨 12 款游戏），
  合并进 `data/annotation/annotation_tasks.csv` 的 `gold_aspects_json` 列；
  唯一评论文本 239 条：其中评论 93940703 与 33393799 的文本完全相同，切分时按同一组处理。
- 切分：`prepare_gold_3way.py` 按**评论组** 8:2 划分，每条评论抽取未提及主题作 neutral
  负例（默认 3 个）。分组用并查集（`code/split_utils.py`，金标与银标共用）把
  「同一 review_id」与「文本完全相同的不同 review_id」连成一组，整组同进同出。
  标注集里恰有一组重复文本（评论 93940703 与 33393799 内容一字不差），只按 review_id
  切分会让它俩分处两侧，训练前置校验必然拒绝——现在两者固定落在同一侧。
  - 训练集：`rbt3_aspect_train.csv`，958 个三元组 / **192 条评论**
    （positive 193 / negative 157 / neutral 608）
  - 测试集：`rbt3_aspect_eval.csv`，250 个三元组 / **48 条评论**
    （positive 66 / negative 34 / neutral 150）
  - 训练与测试的评论文本交集为 0、review_id 交集为 0（切分脚本运行时会自检并打印）。
- 严格留出集 ID：训练数据 CSV 首列即 `review_id`，训练脚本直接取**本次 `--eval` 文件里
  实际出现的 review_id**，与权重同批写入模型目录 `data/rbt3_model_aspect/holdout_ids.json`，
  并在 `task_meta.json` 记录这组 ID 的 `holdout_sha256` 指纹。
- 严格评估的指纹校验（不再有"元数据缺失就放行"的缺口）：
  - `task_meta.json` 缺失 / 内容为 `{}` / 缺 `holdout_sha256` / 值为空串 / 指纹与留出 ID 不符，
    一律非零退出；
  - 校验对象是**实际选中的 ID 集合**（用该集合现算 sha256 与模型记录比对），因此
    `--eval-ids` 指向别处、把过期文件复制到其他路径，同样会被识破；
  - 留出 ID 有部分在标注集里找不到金标时，会打印缺失条数并默认报错，避免悄悄少评一部分；
  - 确实只要参考数字时显式加 `--allow-unverified-holdout`（或沿用 `--allow-fallback-ids`），
    输出会明确标为「参考评估」，不会再写成"模型未见过的评论"。
- 训练前校验（**全部在创建模型目录之前完成**）：
  - train/eval 的评论文本集合与 review_id 集合双双零重叠；
  - **两侧**的 `review_id` 都必须完整、为纯数字，并统一转成 int 再比（`"0161798229"` 与
    `"161798229"` 是同一评论，字符串比较会漏）；
  - 任一项不过立即非零退出，已有模型权重原样保留。
- 模型发布契约：权重、分词器、`holdout_ids.json`、`task_meta.json` 先写同级临时目录，
  再只替换「模型产物」白名单内的文件——输出目录里用户自己的 `train.csv`、`notes.txt`
  等不会被删；输出路径的尾部反斜杠会先规范化（旧实现会因此拼出目录内部的备份路径，
  Windows 报 WinError 87）；替换中途失败会删除新文件并把旧产物移回。
- 金标唯一来源：`annotation_tasks.csv` 的 `gold_aspects_json`。新增标注用
  `merge_gold.py` 合并后，训练与评估都会自动纳入，无需改代码里的固定字典。
- 银标演示（可选）：`prepare_rbt3_data_v2.py --source rule --item-only --limit 20000`
  只写 `rbt3_item_silver_*.csv`，**不覆盖**金标 train/eval 与留出 ID。
  银标切分按 review_id 分组（并查集把同一评论、同一文本连成一组整组切分），
  `--limit` 也按组抽样，所以同一评论的多个条目不会一半在训练集、一半在验证集。

**交付前自检加固（本轮补充，均可用命令直接验证）**

- `code/verify_split_guard.py` 为**纯标准库**实现（不需要 torch / numpy / pandas / pyarrow），
  干净 Python 解释器即可跑完 49 项回归；`evaluate_full.py` 不带 `--rbt3` 时同样零第三方依赖。
- 原子发布补了**回滚分支**：只替换模型产物（权重/分词器/元数据），目录里的用户文件原样保留；
  替换中途失败会删除新文件并把旧产物移回。守卫测试用例 6 用打桩 `shutil.move` 覆盖了这条路径，
  同时覆盖"输出路径尾部带分隔符"（旧实现会因此报 WinError 87）与"目录内非模型文件保留"。
- 切分随机性按层固定种子：`--item-only` 与"两层全跑"得到的条目层切分**完全一致**
  （615/154，可直接 `diff` 两个文件验证），不再依赖前面那层跑没跑。
- `task_meta.json` 里的 `train_file` / `eval_file` / `model` 改为仓库相对路径，
  重跑不会因为本机绝对路径产生整份 diff，也不会泄露个人目录。
- 新增 `requirements.txt`（核心：pandas/pyarrow/numpy）与 `requirements-train.txt`
  （再加 torch/transformers/scikit-learn）；README 与本文档的示例命令去掉本机绝对路径。
- `.gitignore` 增加 `data/rbt3_model_*.old-*`、`data/.rbt3_model_*.tmp-*`，
  避免回滚/中断留下的权重备份被误提交。

### 3.6 复跑评估结果（严格留出集，48 条评论，三方法同口径）

| 方法 | micro-F1 | macro-F1 |
|---|---:|---:|
| voted_up（基线） | 0.8169 | 0.8299 |
| rule_absm（规则） | 0.2836 | 0.2936 |
| RBT3（金标微调） | 0.5361 | 0.5255 |

> RBT3 比纯规则高约 36 个百分点，证明模型路线有效；
> 略低于 voted_up 是因为金标样本仅 240 条、且整体好评占比高，
> 这一口径已如实写入 `docs/效果对比方案.md`，可直接引用。

### 3.7 模型获取方式

- 基座 `hfl/rbt3`，国内用镜像下载：
  ```powershell
  $env:HF_ENDPOINT = "https://hf-mirror.com"
  ```
  训练时 `AutoModelForSequenceClassification.from_pretrained("hfl/rbt3")` 自动拉取。
- 训练产物目录：`data/rbt3_model_aspect/`
  - 已提交：`config.json`、`tokenizer.json`、`task_meta.json`、`holdout_ids.json`
  - 未提交（体积约 154 MB，受 GitHub 单文件 100 MB 限制）：`model.safetensors`
  - 重建方式：按 3.3 第 4 步重跑训练即可生成权重；
    推理脚本按层检测权重，缺权重时自动规则回退，不会报错退出。
- 条目层模型目录 `data/rbt3_model_item/` 同样配置齐全但无金标权重，
  默认走规则模式。

---

## 附：材料清单核对

- [x] 三份新版 CSV（明细 / 分布 / 四阶段汇总）
- [x] 姜岛 S15_01 分阶段条数 + 正负中 + 未判定处理 + 占比分母
- [x] 修订后建表语句 + 指标 7 / 指标 11 SQL + 结果导出
- [ ] Hive 实跑原始截图 —— 本机无 Hive 环境、仓库无历史截图，**未提供伪造截图**；
      请在 Hive 环境执行 SQL 后自行截取
- [x] 分支 / 提交 / 数据批次 / 运行命令 / 情感分析方法
- [x] RBT3 训练测试划分 / 复跑评估 / 模型获取方式
