# 6-12 Reviewer 模块实验结论

> 500 篇大样本实验证实，两阶段审查（Reviewer）在当前方案下边际收益极低，不推荐继续投入。

---

## 实验目的

在 Eval（实体抽取）之后增加 Reviewer 阶段，通过"对答案"方式寻找遗漏的商业实体，提升 recall。

## 实验条件

| 维度 | 值 |
|------|-----|
| 样本量 | 500 篇（random, seed=888） |
| 模型 | deepseek-v4-flash |
| Eval 参数 | temperature 0.3, max_workers 4 |
| Judge 参数 | temperature 0.0（消除随机性） |
| Review 范围 | 仅 0 实体文章 |
| Baseline 路径 | `output/baseline_500_seed888_t0` |
| Review 路径 | `output/review_500_seed888_t0` |

## 结果对比

| 指标 | Baseline | +Review | 变化 |
|------|----------|---------|------|
| **Recall** | **0.873** | **0.876** | **+0.003** |
| **Precision** | **0.996** | **0.963** | **-0.033** |
| Sentiment accuracy | 0.983 | 0.966 | -0.017 |
| Risk type accuracy | 0.897 | 0.816 | -0.081 |
| 实体总数 | 325 | 528 | +203 |

## 关键发现

### 1. Eval 的真实 recall 本身就不低

先前的 100 篇实验出现 recall 0.53 → 0.78 的"大幅提升"，是两类随机性叠加的假象：

- **Judge 随机性**（temperature=0.2，每次结果差异大）
- **小样本偏差**（100 篇中刚好抽到了 eval 表现差的一组）

500 篇 + temperature=0.0 后，baseline recall 实为 **0.873**。

### 2. Reviewer 边际收益极低

- Recall 仅提升 **0.003**（500 篇中多找到约 2 篇的实体）
- Precision 下降 **0.033**（reviewer 宽松倾向带来 20+ 个噪声实体）
- Risk type accuracy 下降 **0.081**（新增的噪声利空实体 risk_type 随机填写，拖累指标）

### 3. 噪声的来源

Reviewer 与 Eval 使用**相同的模型和相似的 prompt 口径**。它能"发现"的遗漏，本质上与 eval 已经拒掉的是同一批边界案例。宽松倾向让它带回了一些 eval 正确过滤掉的噪声，而这些噪声又因为标了利空/risk_type 而加倍伤害指标。

### 4. Reviewer 的复杂性不匹配收益

- 额外 LLM 调用 ~216 次（+43%）
- 额外耗时 ~30s（+15%）
- Recall 增益几乎不可测量

## 实验教训

| 教训 | 说明 |
|------|------|
| **样本量要够大** | 100 篇在小样本下的结论不可靠 |
| **Judge 温度要设为 0** | 否则指标波动会掩盖真实信号 |
| **两阶段要在同一份 eval 输出上做比较** | 不能重新跑 eval，否则无法区分是 eval 波动还是 reviewer 贡献 |
| **同模型审查同模型，收益有限** | Reviewer 和 Eval 共享同一知识边界，能捡到的漏非常少 |

## 最终结论

**Reviewer 模块（两阶段审查）在当前方案下不成立。** Eval 本身 recall 已达 0.87、precision 达 0.996，继续调优应聚焦于：

1. **系统性漏抽模式分析**：行业报告中嵌入的数据、小型冷门公司的主体事件
2. **Eval prompt 针对性增强**：而非引入第二阶段
3. **评估方法改进**：考虑人工标注 golden set 替代 judge 做评估

## 代码状态

- `reviewer/` 目录保留，`entity_eval/run.py` 的 `--review` 参数保留
- 不在默认流程中启用
- 文档记录在此以备后续参考
