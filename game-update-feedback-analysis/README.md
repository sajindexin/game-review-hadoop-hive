# 游戏版本建模与玩家反馈分析

黄静雯负责版本建模、Hive 窗口统计，以及玩家评测与官方改动的关联模型。分析覆盖 12 款游戏、26 个代表版本和 75 项官方改动。

## 查看版本前后的推荐率

打开更新前后对比表，按 `patch_id` 选版本，按 `language_group=all` 看该版本的全语言样本。例如，星露谷物语 1.5 在 2020-12-21 上线，随后 14 天的样本有 4,802 条评测，其中 4,726 条推荐。逐日表可以画趋势，阶段表可以核对分子和分母。日期按 UTC 和评测首次发表时间计算。[前后对比](黄静雯_全组Hive统计/更新前后对比.csv) · [逐日表](黄静雯_全组Hive统计/逐日统计.csv) · [阶段表](黄静雯_全组Hive统计/分阶段统计.csv)

课程 SQL 把更新前 14 天、发布当天和更新后 14 天分别统计。版本目录记录每个改动的中英文名称、别名与官方出处。[Hive SQL](黄静雯_全组Hive统计/windows.hql) · [生成独立复算库的脚本](00_交付工具/prepare_hive_sql.py) · [版本与改动目录](黄静雯_全组版本与改动/全组版本与改动说明.md)

## 用一句话验证关联模型

下面使用一条自拟评测。先进入 `game-update-feedback-analysis` 目录，在 Python 3.12 环境中运行 `python -m pip install -r requirements.txt`，然后运行以下 Python 示例。它调用与正式批量预测相同的候选检索、分类器和星露谷阈值。

```python
from pathlib import Path
import sys
import joblib

model_dir = Path("黄静雯_全组评测改动关联").resolve()
sys.path.insert(0, str(model_dir))
from group_model import features, index_catalog, pm

model = joblib.load(model_dir / "全组关联模型.joblib")
patch = index_catalog()["sdv_20201221_15"]
sample = {"review": "我很喜欢在姜岛探索，新区域让我又玩了一整天。"}
candidates = features(patch, [sample], "base")[0]
scored = pm.score(model["old_estimator"], [(sample, candidates)])[0][1]
for candidate, score in scored:
    if score >= model["old_threshold"]:
        print(candidate[1])
```

输出 `S15_01`，在条目表中对应“姜岛探索”。阈值以上的输出是模型预测，正式页面展示的引文还经过原文核对。[官方条目表](黄静雯_全组版本与改动/items/items.jsonl) · [批量评分代码](黄静雯_全组评测改动关联/full_run.py)

## 全组运行与评估

全组 v1 的 4,884,580 条清洗评测全部进入覆盖表，2,740,535 条产生候选，共得到 6,622,542 对评测与改动的模型分数。正式版本窗口中，筛选与复核后得到 469 对可引用关联。完整评测明细、标注、引文和语义检索索引通过组内最终成果包交接；复跑全量训练、预测或引文问答时使用该批次的输入与目录结构。

评测关联使用字符 TF-IDF 生成候选，再用逻辑回归判断具体改动是否被提及。标注由 AI 完成。原四款留出游戏的 F1 为 82.05%，在后来新增的三个版本窗口上为 48.00%。多语言 E5 用于在选定版本中查找玩家引文。[训练代码](黄静雯_全组评测改动关联/group_model.py) · [四款游戏的评估](黄静雯_全组评测改动关联/跨游戏独立评估.json) · [三个新版本的评估](黄静雯_全组评测改动关联/增强验证/新版本独立验证结果.json) · [引文查询接口](黄静雯_全组评测改动关联/增强验证/find_evidence.py)
