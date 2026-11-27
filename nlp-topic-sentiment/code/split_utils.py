# -*- coding: utf-8 -*-
"""切分工具：按"评论组"整组切分，避免同一评论或其相同文本跨 train/eval。

为什么需要分组：
  - 一条评论会命中多个主题/条目，三元组数远多于评论数；按三元组随机切分会把
    同一条评论拆到两边，等于让模型背答案。
  - 不同原始评论 ID 也可能有**完全相同的评论文本**（刷屏复制粘贴），只按 review_id
    分组仍会让同一段文本同时出现在训练与验证集，训练前置校验会直接拒绝这种数据。

这里用并查集把两类节点连成连通分量，整组同进同出：
  - ("r", review_id)：同一个原始评论 ID 的所有样本
  - ("t", text)     ：文本完全相同的样本

金标（prepare_gold_3way）与银标（prepare_rbt3_data_v2）共用本模块，避免两套切分
逻辑各自演化出不同漏洞。
"""
from __future__ import annotations

import random


def group_by_review(items):
    """并查集分组，返回 {组根: [item, ...]}。

    item 至少包含两个可下标元素：[0] = review_id（可为空串），[1] = 评论文本。
    review_id 为空时退化为纯按文本分组。
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:      # 路径压缩
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for it in items:
        rid, text = it[0], it[1]
        union(("r", rid) if rid else ("t", text), ("t", text))

    groups = {}
    for it in items:
        rid, text = it[0], it[1]
        node = ("r", rid) if rid else ("t", text)
        groups.setdefault(find(node), []).append(it)
    return groups


def split_grouped(groups, ratio, seed, unit=lambda g: len(g)):
    """把分组按比例切成 (train_groups, eval_groups)，返回扁平后的 (train, eval)。

    贪心装箱：按 seed 打乱组顺序，train 未达到目标量就整组放入，达到后其余整组进 eval。
    组是不可分割的最小单位，所以 train 可能因最后一组而略超目标——宁可比目标多一点，
    也不能把一条评论/一段相同文本拆到两边。
    """
    random.seed(seed)
    keys = sorted(groups.keys())
    random.shuffle(keys)
    total = sum(unit(groups[k]) for k in keys)
    target = int(total * ratio)

    train, ev = [], []
    for k in keys:
        g = groups[k]
        if len(train) < target:
            train.extend(g)
        else:
            ev.extend(g)
    return train, ev, target, total
