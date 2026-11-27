# 结果文件说明（提交版）

## 随仓库提交的文件

| 文件 | 行数 | 大小 | 说明 |
|---|---:|---:|---|
| `topic_sentiment_detail.csv.gz` | 535,623 | 约 14 MB | 第一层主题情感明细（中文规则弱标注全量结果，压缩包） |
| `topic_sentiment_detail_en.csv.gz` | 1,120,567 | 约 32 MB | 英文规则弱标注主题情感明细（282.4 万英文评论全量结果；2026-11-27 分句规则修复后重跑） |
| `topic_sentiment_review_summary_en.csv.gz` | 2,824,133 | 约 51 MB | 英文评论级汇总（压缩包） |
| `topic_sentiment_detail_rbt3.csv.gz` | 1,543,242 | 约 13 MB | 中文 RBT3 模型口径主题情感明细（165.7 万评论全量回灌，(review_id,aspect) 零重复） |
| `topic_sentiment_review_summary_rbt3.csv.gz` | 1,656,752 | 约 16 MB | 中文模型口径评论级汇总（压缩包） |
| `topic_distribution.csv` | - | 小 | 11 类主题 × 情感分布（中文规则口径） |
| `topic_distribution_en.csv` | - | 小 | 11 类主题 × 情感分布（英文规则口径） |
| `item_sentiment_detail.csv` | 1,593 | 小 | 第二层 75 项官方更新条目情感明细 |
| `item_distribution.csv` | 68 行 | 小 | 条目分布，正/负/中合计 1,593 条 |
| `item_patch_summary.csv` | 30 行 | 小 | 条目 × 四个版本窗口统计（固定列、补零） |
| `topic_sentiment_review_summary.csv.gz` | 原表 45 万+ 行 | 约 24 MB | 中文规则口径评论级汇总（压缩包） |
| `../taxonomy/item_dict.csv` | 75 行 | 小 | Hive 条目字典表（OpenCSVSerde 可读） |

## 双口径要点（写报告用）

- 中文规则口径：165.7 万评论，32.5 万条命中主题（19.6%），53.6 万主题-情感对。
- 中文模型口径（RBT3 全量回灌）：165.7 万评论，135.7 万条命中主题（**81.9%**），154.3 万非中性对（positive 136.3 万 / negative 18.1 万）。
- 英文规则口径：282.4 万评论（英文 100%），63.4 万条命中主题（22.4%），112.1 万主题-情感对（1,120,567 条）。
  2026-11-27 修复英文分句（句尾句号分句 + 转折词大小写不敏感）后全量重跑，187,704 个主题-情感对（16.8%）的情感标签相比旧口径发生变化，命中集合与对数不变。
  注意：主题-情感对为**规则弱标注口径**（词库 + 分句 + 就近绑定，非模型输出），分句规则对其影响显著（约 16.8% 的对受影响），结果仅作探索性统计使用，准确率以 240 条金标验证的 RBT3 / 评估结论为准。
- 英文不走模型：RBT3 为中文专用预训练模型（词表/预训练语料/微调数据/prompt 均为中文），英文输入会得到无效表征；英文侧如需模型口径需换多语种底座（如 XLM-R）并重造英文金标，列入展望。

## 本地保留但不提交的文件

- `topic_sentiment_detail.csv`（82 MB）、`topic_sentiment_review_summary.csv`（249 MB）：中文规则口径原表，提交版用 `.gz` 代替。
- `topic_sentiment_detail_en.csv`（190 MB）、`topic_sentiment_review_summary_en.csv`（480 MB）：英文原表，超 GitHub 100 MB 单文件限制；提交版用 `.gz` 代替。
- `topic_sentiment_detail_rbt3.csv`（166 MB）、`topic_sentiment_review_summary_rbt3.csv`（304 MB）：模型口径原表；提交版用 `.gz` 代替。
- `infer_rbt3_progress.json`：回灌断点文件，临时产物。
- `../rbt3_model_aspect/model.safetensors`、`../rbt3_model_item/model.safetensors`：各 154 MB，超过限制；模型的 `config.json` / `tokenizer.json` / `task_meta.json` 已提交，可用训练脚本重建权重。

## 解压命令

PowerShell：

```powershell
cd "C:\Users\hgj\Desktop\大数据大作业\game-review-hadoop-hive\nlp-topic-sentiment\data\result"
& "C:\Program Files\Git\usr\bin\gzip.exe" -dk topic_sentiment_detail.csv.gz
& "C:\Program Files\Git\usr\bin\gzip.exe" -dk topic_sentiment_review_summary.csv.gz
```

或 Python（任意环境）：

```powershell
python -c "import gzip,shutil; pairs=[('topic_sentiment_detail.csv.gz','topic_sentiment_detail.csv'),('topic_sentiment_review_summary.csv.gz','topic_sentiment_review_summary.csv')]; [(lambda f,g:(shutil.copyfileobj(f,g),f.close(),g.close()))(gzip.open(s,'rb'),open(d,'wb')) for s,d in pairs]"
```

> 直接用 pandas 读取压缩版也可以，不需要解压：
> `pd.read_csv(r'..\data\result\topic_sentiment_detail.csv.gz')`。

## 重新生成大结果表（如需重跑）

```powershell
cd "C:\Users\hgj\Desktop\大数据大作业\game-review-hadoop-hive\nlp-topic-sentiment\code"
# 英文规则打标（约 17 分钟，CPU）
python build_topic_results.py --lang en
# 中文 RBT3 全量回灌（GPU 约 3 小时，支持 --resume 断点续跑；监控面板 monitor_rbt3.py）
D:\venvs\rbt3-cuda\Scripts\python.exe infer_full_rbt3.py
```

## 重建模型权重命令

在 `nlp-topic-sentiment/code` 目录执行（需先装 torch/transformers，详见上层 README）：

```powershell
cd "C:\Users\hgj\Desktop\大数据大作业\game-review-hadoop-hive\nlp-topic-sentiment\code"
$PY = "C:\Users\hgj\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
$env:HF_ENDPOINT = "https://hf-mirror.com"

& $PY prepare_rbt3_data_v2.py --source gold
& $PY train_rbt3_v2.py --task aspect --train ..\data\annotation\rbt3_aspect_train.csv --eval ..\data\annotation\rbt3_aspect_eval.csv --epochs 3
# 训练脚本会把本次 eval 的评论 ID 写入 ..\data\rbt3_model_aspect\holdout_ids.json
```

> 条目层暂无人工金标，不提供空训练文件；演示用规则模式即可。若需要银标模型：
> `prepare_rbt3_data_v2.py --source rule --limit 20000` 后再训练 item 层。
> 默认推理会按层检测权重，缺权重自动回退规则；`--rule-fallback` 可强制两层规则。
