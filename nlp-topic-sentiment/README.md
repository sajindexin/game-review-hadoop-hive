# 中文 NLP 与专题统计 —— 交付包（v2 两层标签）

> 负责人模块：主题情感标签 + RBT3 + 小样本验算 Hive 指标
> 输入：游戏评论清洗成果 v1（`cleaned_reviews.parquet`，488.5 万行 / 12 款游戏）
> 官方更新条目来源：黄静雯版本建模分支（`items.jsonl` 75 项 / `patches.jsonl` 26 版本）

## 一、做了什么（两层标签体系）

本模块把"游戏更新反馈分析"拆成两层，一条评论可同时打两层标签：

| 层 | 内容 | 来源 | 数量 |
|---|---|---|---|
| **第一层 · 通用主题** | 内容玩法/联机/画质/性能… 每类正/负/中 | 从评论原文归纳 | 11 类 |
| **第二层 · 具体更新条目** | 姜岛探索/八人联机/盾牌重做… 每条正/负/中 | 黄静雯分支的官方 changelog 条目 | 75 项 |

**为什么要两层**：第一层回答"玩家在聊什么、态度如何"；第二层回答"玩家在聊**哪一条具体改动**、态度如何"。例如"姜岛探索好玩，但八人联机老掉线"→ 第一层 `地图关卡=正 + 联机=负`，第二层 `S15_01姜岛探索=正 + S16_04八人联机=负`。第二层直接对接课程项目主题，也和黄静雯的关联模型、版本窗口天然对齐。

**关键设计**：条目是**游戏特定**的，匹配时按 `appid` 约束候选集——"姜岛"只会在星露谷评论里匹配，不会被误判到 CS2。

具体流程：
1. 抽中文 165.7 万行 → 2. 建两层标签体系 + 情感词典 → 3. 规则弱标注跑全量（当天出结果表）→ 4. 生成带条目层的标注集并内置语义金标 → 5. RBT3 两层微调脚本 → 6. 11 组 Hive 指标（含版本前后窗口）。

## 二、目录结构

```
nlp-topic-sentiment/
├── README.md                        本说明
├── code/
│   ├── lexicon.py                     主题体系 + 情感词典
│   ├── build_item_taxonomy.py         构建两层标签体系 -> taxonomy + item_lexicon.py
│   ├── item_lexicon.py                （自动生成）75 项条目词典 + appid 约束
│   ├── rule_labeler.py                两层规则打标引擎（核心）
│   ├── item_matcher.py                具体条目匹配器（按 appid 约束）
│   ├── build_topic_results.py         第一层全量打标 -> 主题结果表
│   ├── build_item_results.py          第二层全量打标 -> 条目结果表
│   ├── build_annotation_set.py        生成两层标注集 + 工具内嵌数据
│   ├── prepare_rbt3_data_v2.py        两层标注 -> RBT3 训练格式
│   ├── train_rbt3_v2.py               RBT3 两层微调（--task aspect/item）
│   ├── infer_rbt3_v2.py               RBT3 两层推理（含规则回退）
│   ├── evaluate.py                    效果评估/基线对比
│   └── final_check.py                 结果表校验
├── data/
│   ├── taxonomy/
│   │   ├── items_enriched.json         75 项条目（含通用主题归属、匹配词）
│   │   ├── patches.json                26 个版本窗口
│   │   └── appid_game_map.json         appid -> 游戏名
│   ├── annotation/
│   │   ├── annotation_tasks.csv        标注集（含两层预填 + 已合并的语义金标）
│   │   ├── gold_samples.csv            金标样例（含条目层与跨游戏反例）
│   │   └── rbt3_{aspect,item}_{train,eval}.csv  （运行 prepare 后生成）
│   └── result/
│       ├── topic_sentiment_detail.csv.gz      第一层主题情感明细（提交版压缩包）
│       ├── topic_sentiment_review_summary.csv.gz 评论级汇总（提交版压缩包）
│       ├── topic_distribution.csv             主题分布
│       ├── item_sentiment_detail.csv          第二层条目情感明细
│       ├── item_distribution.csv              条目分布
│       └── item_patch_summary.csv             条目×版本窗口统计
├── docs/
│   ├── 两层标签体系说明.md             ★ 两层设计、映射、接入方式
│   ├── 标注规范.md                     标注标准与争议处理
│   ├── 结果表字段说明.md
│   └── 效果对比方案.md
└── sql/
    └── hive_metrics.sql                11 组 Hive 指标 SQL（含两层 + 版本窗口）
```

## 三、环境准备

**规则打标 + 结果表**：本机隔离环境已装好 `pandas` / `pyarrow` / `numpy`，**无需准备**。

**真训 RBT3**（本机已装：torch 2.15.0+cpu / transformers 5.24 / sklearn 1.9.1）：

```powershell
$PY = "C:\Users\hgj\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
# 若 torch 缺失：阿里云源装的是 CPU 版（RBT3-small CPU 也能训，只是慢）
& $PY -m pip install -i https://mirrors.aliyun.com/pypi/simple torch
# 想用 RTX 4060 GPU 加速：先从 PyTorch 官方源装 CUDA 版（需能访问外网/代理）
# & $PY -m pip install torch --index-url https://download.pytorch.org/whl/cu121
& $PY -c "import torch,transformers; print(torch.__version__, 'CUDA=', torch.cuda.is_available())"
```

**模型下载**：脚本已默认走 `hf-mirror.com` 国内镜像（HuggingFace 直连国内常超时）。
若镜像也失败，可先手动预下载：`$env:HF_ENDPOINT="https://hf-mirror.com"; & $PY -c "from huggingface_hub import snapshot_download; snapshot_download('hfl/rbt3')"`

## 四、运行步骤（可直接复制）

在 `nlp-topic-sentiment/code` 目录执行：

```powershell
cd "C:\Users\hgj\Desktop\大数据大作业\game-review-hadoop-hive\nlp-topic-sentiment\code"
$PY = "C:\Users\hgj\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

# 0) 重建两层标签体系（改了条目映射/词典后执行）
& $PY build_item_taxonomy.py

# 1) 两层规则弱标注（结果表已生成，需重跑时执行；各约 4-6 分钟）
& $PY build_topic_results.py
& $PY build_item_results.py

# 2) （可选）重新生成两层标注集；当前 annotation_tasks.csv 已内置 240 条语义金标，通常跳过
& $PY build_annotation_set.py --per-game 60 --item-quota 20
#    若本地另行标注并导出了 gold_labels_*.csv，再合并回标注集（会先自动备份）：
& $PY merge_gold.py --gold ..\data\annotation\gold_labels_2026-11-23.csv

# 3) 效果评估（规则基线）
& $PY evaluate.py

# 4) 演示推理（无需模型，规则回退；含两层）
& $PY infer_rbt3_v2.py --text "姜岛探索很好玩，但是八人联机老是掉线" --appid 413150 --rule-fallback

# --- 以下需先装 torch；金标已合并进 annotation_tasks.csv，无需手动标注 ---

# 5) 两层标注 -> RBT3 训练格式
& $PY prepare_rbt3_data_v2.py --source gold

# 6) 分别训练两层模型（各 3 轮；输出 macro-F1）
& $PY train_rbt3_v2.py --task aspect --train ..\data\annotation\rbt3_aspect_train.csv --eval ..\data\annotation\rbt3_aspect_eval.csv --epochs 3
& $PY train_rbt3_v2.py --task item   --train ..\data\annotation\rbt3_item_train.csv   --eval ..\data\annotation\rbt3_item_eval.csv   --epochs 3

# 7) 用训练好的两层模型推理（条目层默认带规则召回门控）
& $PY infer_rbt3_v2.py --text "姜岛探索很好玩，但是八人联机老是掉线" --appid 413150
#    金标补齐"未提及"负样本重训后，可加 --no-rule-gate 让模型独立判别条目相关性
```

## 五、当前结果摘要

**第一层（通用主题，全量 165.7 万中文评论）**

| 指标 | 数值 |
|---|---|
| 主题情感标注数 | 535,623 |
| 内容/玩法-正向 | 30,830（最多）|
| 性能/优化-负向 | 21,773（最多负向）|
| 混合情感评论 | 9,332 |

**第二层（具体更新条目）**

| 指标 | 数值 |
|---|---|
| 官方更新条目 | 75 项 / 12 款游戏 |
| 条目标注数 | 1,593（命中 1,437 条评论）|
| 金标样例 | 5 条（含一条多条目 + 跨游戏反例）|

> 注：第二层命中率天然低（如"姜岛探索"只在星露谷评论里出现），这是**精确率优先**的设计——
> 已剔除"连战/复战/挑战"等会大量误命中的歧义短词，只保留明确的官方标题与专名别名。

**RBT3 已用 240 条语义金标重训并实测（CPU，模型已保存）**

金标对比结果（**严格留出集 48 条**——RBT3 训练时未见过的评论，三方法口径一致；
命令 `evaluate_full.py --rbt3 --heldout`，详见 `docs/效果对比方案.md`）：

| 方法 | micro-F1 | macro-F1 |
|---|---|---|
| baseline_voted_up | 0.6542 | 0.5885 |
| rule_absm（规则）| 0.2687 | 0.2122 |
| **rbt3-aspect（金标微调）** | **0.6256** | **0.5518** |

> 金标由语义理解逐条标注 240 条（`code/gold_annotations.py`），已合并进标注集，**用户无需手动标注**。
> RBT3 远超规则引擎（+36 点），略低于 voted_up 基线（受限于 192 条训练样本 + 整体情感评论多）。
> 早期 5 条样例上规则 F1=0.92 是虚高，240 条真实金标暴露了规则召回缺陷（0.17）。
> 若不带 `--heldout` 会在全部 240 条上评估（含训练用过的评论，数字偏乐观：rbt3 0.6887），**写报告请用严格口径**。

## 六、注意事项

- **条目按 appid 约束**：推理时必须传 `--appid`，否则条目层会跳过。
- 规则弱标注是**银标**（冷启动/演示用）；正式效果以人工金标 + RBT3 为准。
- 版本窗口（pre/post 14 天）来自黄静雯分支的 `patches.jsonl`，与她的关联模型口径一致，联调可直接复用。
- 所有时间口径 UTC；每份结果请注明数据版本 v1。
- **提交到 Git 时**：82MB 的 `topic_sentiment_detail.csv`、249MB 的 `topic_sentiment_review_summary.csv`、两个 154MB 模型权重，以及过程工具 `标注工具.html` / 个人备忘 `你现在该怎么做.md` 均按 `.gitignore` 忽略，保留本地；仓库内提交 `.csv.gz` 压缩版与模型配置/分词器，详见 `data/result/README.md`。

## 七、提交命令

在仓库根目录执行：

```powershell
$REPO = "C:\Users\hgj\Desktop\大数据大作业\game-review-hadoop-hive"
cd $REPO

git status --short
git add nlp-topic-sentiment README.md
git status --short
git diff --cached --stat

git commit -m "Add Chinese NLP topic sentiment module"
# 需要推到远端再执行：
git push origin main
```
