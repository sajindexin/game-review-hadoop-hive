# 结果文件说明（提交版）

## 随仓库提交的文件

| 文件 | 行数 | 大小 | 说明 |
|---|---:|---:|---|
| `topic_sentiment_detail.csv.gz` | 535,623 | 约 14 MB | 第一层主题情感明细（规则弱标注全量结果，压缩包） |
| `topic_distribution.csv` | - | 小 | 11 类主题 × 情感分布 |
| `item_sentiment_detail.csv` | 1,593 | 小 | 第二层 75 项官方更新条目情感明细 |
| `item_distribution.csv` | 68 行 | 小 | 条目分布，正/负/中合计 1,593 条 |
| `item_patch_summary.csv` | 30 行 | 小 | 条目 × 四个版本窗口统计（固定列、补零） |
| `topic_sentiment_review_summary.csv.gz` | 原表 45 万+ 行 | 约 24 MB | 评论级汇总原表的压缩包 |
| `../taxonomy/item_dict.csv` | 75 行 | 小 | Hive 条目字典表（OpenCSVSerde 可读） |

## 本地保留但不提交的文件

- `topic_sentiment_detail.csv`：原表 82 MB，接近 GitHub 单文件 100 MB 限制；提交版用 `.gz` 代替。
- `topic_sentiment_review_summary.csv`：原表 249 MB，超过限制；提交版用 `.gz` 代替。
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
