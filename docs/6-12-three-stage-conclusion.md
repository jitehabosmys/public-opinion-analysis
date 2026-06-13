# 6-12 三段式（Eval + Reviewer + Infiller）实验结论

> 本文记录了从两阶段审查（Reviewer）到三段式（Eval → Reviewer → Infiller）的演进过程、关键发现和最终结论。

---

## 一、演进路线

### 阶段 1：两阶段（Reviewer）— 失败

初始方案：Eval 抽取后，Reviewer 直接补全遗漏实体的所有字段，Prompt 自写，未对齐 Judge。

**问题**：Reviewer 同时承担"查漏"和"补全"两个任务，互相干扰。查漏质量本身下降——额外调用 ~216 次中仅 ~6 个命中真漏，Precision 从 0.996 暴跌至 0.963。

### 阶段 2：三段式 V1（Reviewer 仅输出名称 + Infiller 填空）— 次优

改为 Reviewer 只输出遗漏实体名称（`{"entity": "...", "reason": "..."}`），由 Infiller 逐实体填充字段。Reviewer Prompt 在 Judge Prompt 基础上多次"修补"，但每次都是局部改动，未完整对齐。

**问题**：Reviewer 与 Judge 的 Prompt 存在细微差异（缺 `## 有效实体` 区块、缺"没有自身新事件或影响"排除规则），导致 Reviewer 比 Judge 判断更宽松，报告了大量 Judge 认为不应抽取的实体。500 样本上 recall 0.972 / precision 0.906，噪声仍偏高。

### 阶段 3：三段式 V2（Reviewer 复制 Judge 完整 Prompt）— 成功

核心发现：**让 Reviewer 同时检查 missed 和 extra，比只检查 missed 效果更好。**

| 版本 | Recall | Precision | Sentiment | Risk Type |
|------|--------|-----------|-----------|-----------|
| Baseline | 0.873 | 0.996 | 0.983 | 0.897 |
| 旧三段式（仅 missed） | 0.972 | 0.906 | 0.961 | 0.833 |
| **新三段式（含 extra）** | **0.996** | **0.974** | **0.971** | **0.866** |

---

## 二、关键教训

### 错误的分析：任务定义不同导致噪声

在调试过程中，我（AI 助手）反复解释说 Reviewer 和 Judge 的"任务定义不同"——Judge 同时检查 missed 和 extra，Reviewer 只检查 missed，因此 Reviewer 缺乏批判性思维，倾向于宽松抽取。

### 正确的做法：直接复制 Judge Prompt

这个解释是错的。**根本问题不是任务定义，而是 Reviewer 的 Prompt 没有完整复制 Judge。** 我每次"基于 Reviewer 旧版修修改改"，导致版本越来越偏离 Judge。真正的修复方式很简单：

1. 把 Judge 的完整 Prompt 直接复制给 Reviewer
2. `_parse_response` 只取 `missed` 字段，忽略 `extra`、`sentiment_errors`、`risk_type_errors`
3. 让 Reviewer 同时理解"什么该抽"和"什么不该抽"——**Extra 判断本质上就是在定义"什么不该抽"**

效果立竿见影：extra 从 ~258 个降到 80 个，precision 从 0.906 回升到 0.974。

---

## 三、三段式最终方案

```
Stage 1 — Eval：全文抽取，输出实体记录（全字段）
                ↓
Stage 2 — Reviewer：复制 Judge Prompt，输出遗漏实体名称
                     （只取 missed 字段，extra 判断作为内部激活）
                ↓
Stage 3 — Infiller：针对每个遗漏实体名，从原文中填充所有字段
                     （逐文章批量调用，非逐实体）
                ↓
合并 → 后处理过滤 → 去重 → 同向合并 → 最终输出
```

你提到的批量方案（逐文章而非逐实体）也已应用，省 ~60% Token。

---

## 四、代码变更记录

| 变更 | 文件 | 说明 |
|------|------|------|
| Reviewer 重写 | `reviewer/agent.py` | Prompt 复制 Judge，只取 missed |
| Infiller 新增 | `infiller/agent.py` | 单实体/批量填空模块 |
| Infiller 两步验证 | `infiller/agent.py` | 先验证实体是否有效，再填充 |
| 三段式 CLI | `entity_eval/run.py` | `--infill` 参数 |
| Web 移除 | `app.py` | 评审模式选项已移除 |
| 实验文档 | `docs/6-12-reviewer.md` | 两阶段实验记录 |
| 本文件 | `docs/6-12-three-stage-conclusion.md` | 三段式结论 |

---

## 五、Infiller 模式对比：all vs zero

三段式支持两种 reviewer 扫描范围：

| 模式 | 扫描范围 | Reviewer 调用 | Infiller 调用 |
|------|---------|--------------|--------------|
| `all` | 全部文章 | 500 | 视遗漏数而定 |
| `zero` | 仅 eval 抽出 0 实体的文章 | ~50 | 同上（更少） |

Zero 模式基于之前的发现：大部分真漏集中在 0 实体文章中，扫描已有实体的文章边际收益低。

### 500 样本指标对比

| 指标 | Baseline | +infill all | +infill zero |
|------|----------|-------------|--------------|
| Recall | 0.873 | 0.996 | 0.988 |
| Precision | 0.996 | 0.974 | 0.976 |
| Sentiment | 0.983 | 0.971 | 0.978 |
| Risk Type | 0.897 | 0.866 | 0.893 |

### Token 与耗时对比（500 样本）

| | Baseline | +infill all | +infill zero |
|------|----------|-------------|--------------|
| 总调用 | 500 | 1,160 | 923 |
| 总耗时 | 205s | 520s | 441s |
| Prompt tokens | 795,613 | 1,795,769 | 1,415,363 |
| Completion tokens | 25,455 | 69,253 | 64,177 |
| **总 tokens** | **821,068** | **1,865,022** | **1,479,540** |

Zero 模式相比 all 模式：
- 节省 **21% token**（约 0.4M）
- 节省 **15% 时间**（约 1.3 分钟）
- Recall 仅下降 0.008（0.996 → 0.988）
- Precision 反而微升 0.002（0.974 → 0.976）

**推荐 `--infill zero` 作为默认**。调用 `--infill`（不带参数）即使用 zero 模式，`--infill all` 使用全量扫描。

---

## 六、最终结论

| 场景 | 方案 | Recall | Precision | 耗时 | Tokens |
|------|------|--------|-----------|------|--------|
| 精确优先（风控/直接输出） | Eval only（Baseline） | 0.873 | **0.996** | **205s** | **821K** |
| 覆盖优先（监测/收集） | 三段式（`--infill zero`） | **0.988** | 0.976 | 441s | 1.48M |

两套方案共用同一份 Eval 代码，按需选用。三段式对 recall 极度敏感的场景有价值，代价是 ~2x 耗时和约 1.8x token 消耗。
