# 6-12 Reviewer 遗漏审查模块

> 为实体抽取流程增加第二阶段的"遗漏审查"（Reviewer），用于找回首轮 Eval 漏掉的商业实体。

---

## 一、动机

### 问题

Eval 的 recall 约 0.73（seed 444 / 100 篇），precision 约 0.99。recall 瓶颈主要来自：

- 小型/冷门公司的主事件被遗漏
- 行业综述中嵌入的经营数据未被提取
- 合作交易参与方被忽略

调 prompt 已几乎无法继续提升 recall（retry-empty 恢复率仅 1/24），需要换思路。

### 思路

Judge 在评估中能发现大量 eval 遗漏的实体，这说明"对答案"式的审查比"从零生成"式的抽取更容易发现遗漏。因此复用类似思路，在 eval 之后跑一个 reviewer，专门负责捡漏。

---

## 二、改动汇总

### 新增文件

| 文件 | 内容 |
|------|------|
| `reviewer/__init__.py` | 包初始化 |
| `reviewer/agent.py` | `ReviewerAgent`，基于 judge prompt 改造，输出与 eval 同格式的遗漏实体 |
| `reviewer/run.py` | Reviewer 独立 CLI，输入 entities.csv 输出 reviewer_missed.csv |

### 修改文件

| 文件 | 改动 |
|------|------|
| `entity_eval/run.py` | 新增 `--review {all,zero}` 参数，集成 reviewer 阶段 + dedup + merge |
| `app.py` | 新增遗漏审查模式 selectbox（不启用 / all / zero） |

### Reviewer Agent 设计

基于 `judge/agent.py` 的 prompt 改造，差异：

| 维度 | Judge | Reviewer |
|------|-------|----------|
| 任务 | 同时检查遗漏和多余 | 仅找遗漏 |
| 输出格式 | `{"missed": [...], "extra": [...]}` | `[entity_record, ...]`（同 eval 格式） |
| 排除规则 | 8 条（不应列为 missed） | 11 条（对齐 judge + 原始不输出项） |
| 示例 | 4 个 | 3 个（ETF、纯股价、名单） |

**传递给 reviewer 的已有实体**包含完整字段：`entity`、`mapped_from`、`entity_sentiment`、`sentiment_reason`。这样 reviewer 能识别已通过 `mapped_from` 覆盖的产品/品牌名，避免误判为遗漏。

### 后处理 + 去重

Reviewer 的输出经过与 eval 相同的后处理 filter pattern 过滤，然后以 `(doc_id, entity)` 为 key 与 eval 结果去重。最终的合并由 `_merge_entity_rows` 统一处理（同向合并、impact_level 取最高、risk_type 取 union）。

---

## 三、实验结论

### 对比条件

- seed 444，random 100 篇
- 模型：deepseek-v4-flash，temperature 0.3
- 三组对比：baseline（无 reviewer）、review all、review zero

### 指标

| 指标 | Baseline | Review All | Review Zero |
|------|----------|------------|-------------|
| **recall** | 0.534 | **0.778** | **0.757** |
| **precision** | 0.988 | 0.964 | **0.974** |
| 实体总数 | 62 | 162 | 138 |
| reviewer 找回 | - | 105 | 86 |
| 额外耗时 | - | +50.6s | +35s |
| 额外 LLM calls | - | +100 | +57 |

### 找回质量

| 来源 | 找回数 | 找对（非 extra） | 找多（extra） |
|------|--------|----------------|--------------|
| **All 找回** | 105 | 78 (74%) | 27 (26%) |
| **Zero 找回** | 86 | 80 (93%) | 6 (7%) |
| 两者共找回 | 55 | 50 (91%) | 5 (9%) |
| All 独有 | 50 | 28 (56%) | 22 (44%) |

### 结论

1. **Reviewer 有效提升 recall**：从 0.534 → 0.778（All）/ 0.757（Zero），提升约 42-45%。
2. **Precision 下降可控**：从 0.988 降至 0.964（All）/ 0.974（Zero）。
3. **Zero 模式性价比最高**：93% 的找回实体正确，噪声仅 7%，接近效果上限。
4. **All 独有部分噪声高**（44%），多来自 eval 已有实体的文章中追加的行情/名单类实体。

### 推荐

- 日常使用：**`--review zero`**（仅扫描 eval 抽出 0 实体的文章）
- 对 recall 要求极高的场景：**`--review all`**
- Web 页面 Selectbox：不启用 / all / zero

---

## 四、使用方法

### CLI

```bash
# 全量 review
uv run python -m entity_eval.run -n 100 --sample-mode random --seed 444 -w 4 --review -o output/review_test

# 仅 review 0 实体文章
uv run python -m entity_eval.run -n 100 --sample-mode random --seed 444 -w 4 --review zero -o output/review_test

# 独立运行 reviewer（基于已有 entities.csv）
uv run python -m reviewer.run --entities output/xxx/entities.csv --data data/test_news_0603.csv -o output/review_only
```

### Web

在页面顶部 selectbox 选择遗漏审查模式：不启用 / all / zero。
