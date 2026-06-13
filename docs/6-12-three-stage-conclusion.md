# 6-12 三段式（Eval + Reviewer + Infiller）实验结论

> 本文记录了从两阶段审查（Reviewer）到三段式（Eval → Reviewer → Infiller）的演进过程、关键发现和最终结论。

---

## 一、演进路线

### 阶段 1：两阶段（Reviewer）— 失败

初始方案：Eval 抽取后，Reviewer 直接补全遗漏实体的所有字段。

**问题**：Reviewer 为了填字段而编造实体，额外调用 ~216 次 LLM 中有 ~197 个噪声（仅 ~6 个命中真漏）。Precision 从 0.996 暴跌至 0.963。

### 阶段 2：三段式 V1（Reviewer 仅输出名称 + Infiller 填空）— 次优

改为 Reviewer 只输出遗漏实体名称（`{"entity": "...", "reason": "..."}`），由 Infiller 逐实体填充字段。

**问题**：500 样本上 recall 0.972 / precision 0.906。噪声仍偏高。

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

## 五、最终结论

| 场景 | 方案 | Recall | Precision |
|------|------|--------|-----------|
| 精确优先（风控/直接输出） | Eval only（Baseline） | 0.873 | **0.996** |
| 覆盖优先（监测/收集） | 三段式（Eval + Reviewer + Infiller） | **0.996** | 0.974 |

两套方案共用同一份 Eval 代码，按需选用。三段式对 recall 极度敏感的场景有价值，代价是 ~2x 耗时和轻微 precision 下降。
