# game-review-hadoop-hive

基于 Hadoop、Hive 与 Python 的游戏评论数据清洗和分析课程项目

[Steam 评测与公告采集工具](crawler/README.md)支持按天采样、断点续采和 JSONL 导出。

[中文 NLP 与专题统计模块](nlp-topic-sentiment/README.md)包含两层主题情感标注体系（11 类通用主题 + 75 项官方更新条目）、规则弱标注、RBT3 微调与推理脚本、Hive 指标 SQL，以及与基线模型的效果对比结果。

[公共 Hive 数据访问层](docs/hive-service.md)供各 Python 模块统一连接 HiveServer2，包含配置示例、查询接口和异常处理约定。
