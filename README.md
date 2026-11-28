# game-review-hadoop-hive

基于 Hadoop、Hive 与 Python 的游戏评论数据清洗和分析课程项目

[Steam 评测与公告采集工具](crawler/README.md)支持按天采样、断点续采和 JSONL 导出。

[中文 NLP 与专题统计模块](nlp-topic-sentiment/README.md)包含两层主题情感标注体系（11 类通用主题 + 75 项官方更新条目）、规则弱标注、RBT3 微调与推理脚本、Hive 指标 SQL，以及与基线模型的效果对比结果。

[Hive 服务使用文档](docs/hive-service.md)包含 HiveServer2 连接信息、公共 Python 客户端使用方法和主要数据表。
