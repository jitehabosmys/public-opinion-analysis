# 6-14 项目总览

> 金融风控动态舆情分析工具。利用 LLM 从新闻/舆情文章中提取商业实体，判断事件影响方向（利好/中性/利空）、影响程度（大/中/小）和风险类别（6 类），输出结构化数据。

---

## 一、任务目标

对新闻/舆情文章进行结构化风险分析，产出：

- 文中涉及哪些商业实体（公司、金融机构、品牌等）
- 事件对每个实体的**情感方向**（利好/中性/利空）
- 利空事件的**影响程度**（大/中/小）
- 利空事件的**风险类别**（产品质量/财务风险/监管合规/经营风险/竞争风险/品牌声誉）

详细任务背景见 [`docs/task_description.md`](task_description.md)。

---

## 二、项目结构

```
public-opinion-analysis/
├── entity_eval/          # Stage 1 — 实体抽取
│   ├── agent.py          #   LLM Agent + Prompt
│   └── run.py            #   CLI 入口
├── reviewer/             # Stage 2 — 遗漏检测
│   ├── agent.py          #   LLM Agent（Prompt = Judge 完整 Prompt）
│   └── run.py            #   CLI 入口
├── infiller/             # Stage 3 — 遗漏实体字段填充
│   └── agent.py          #   LLM Agent + Prompt
├── judge/                # 评估模块
│   ├── agent.py          #   LLM-as-a-Judge（四维评分）
│   └── run.py            #   CLI 入口
├── app.py                # Streamlit Web 应用
├── database.py           # SQLite 历史记录存储
├── data/                 # 测试数据
│   └── test_news_0603.csv  # 50,000 篇新闻
├── output/               # 实验输出
├── docs/                 # 文档
│   ├── task_description.md
│   └── 6-*.md
├── pyproject.toml        # 依赖声明
└── Dockerfile            # Docker 部署
```

---

## 三、核心流程

### 3.1 两方案对比

| | 方案 A（精确优先） | 方案 B（覆盖优先） |
|---|---|---|
| **流程** | Eval only | Eval → Reviewer → Infiller |
| **Recall** | **0.873** | **0.988** |
| **Precision** | **0.996** | **0.976** |
| **耗时（500篇）** | ~205s | ~441s |
| **Token（500篇）** | ~821K | ~1.48M |
| **适用** | 风控直接输出 | 信息收集/监测 |

详细实验结果见 [`docs/6-12-three-stage-conclusion.md`](6-12-three-stage-conclusion.md)。

### 3.2 Eval 流程（方案 A / 方案 B 共用）

```
文章 → EntityEvalAgent → 原始实体列表
                              ↓
                         后处理过滤（14 条 pattern）
                              ↓
                         同向实体合并
                              ↓
                         输出 entities.csv / .jsonl / .xlsx
```

Eval Prompt 迭代记录： [`docs/6-5-eval-iteration-report.md`](6-5-eval-iteration-report.md)
Prompt 压缩实验： [`docs/6-8-prompt-compression-report.md`](6-8-prompt-compression-report.md)

### 3.3 三段式流程（方案 B）

```
Stage 1 — Eval：同上
                ↓
Stage 2 — Reviewer：复制 Judge 完整 Prompt，输出遗漏实体名称
                     （同时检查 missed 和 extra，但只取 missed）
                ↓
Stage 3 — Infiller：针对遗漏实体名，从原文中批量填充所有字段
                ↓
合并 → 后处理过滤 → 去重 → 同向合并 → 最终输出
```

### 3.4 Judge 评估

四维评分：

| 维度 | 评估内容 | 当前准确率 |
|------|---------|-----------|
| **Entity Recall** | 有事件实体是否遗漏 | 0.873~0.988 |
| **Precision** | 已抽实体是否多余 | 0.976~0.996 |
| **Sentiment** | 情感方向判断是否正确 | 0.978 |
| **Risk Type** | 风险类别是否准确 | 0.893 |

Judge 设计见 [`docs/6-5-eval-iteration-report.md`](6-5-eval-iteration-report.md)（evaluation 部分）。

---

## 四、关键文档索引

| 文档 | 内容 |
|------|------|
| [`docs/task_description.md`](task_description.md) | 项目背景、任务目标 |
| [`docs/6-5-eval-iteration-report.md`](6-5-eval-iteration-report.md) | Eval Prompt 5 版迭代、Judge 评估体系 |
| [`docs/6-5-batch100-findings.md`](6-5-batch100-findings.md) | 100 样本实验、多 seed 稳定性 |
| [`docs/6-8-prompt-compression-report.md`](6-8-prompt-compression-report.md) | Prompt 压缩（-39% token，效果不变）|
| [`docs/6-9-project-status-and-web-plan.md`](6-9-project-status-and-web-plan.md) | Web 服务设计、技术选型 |
| [`docs/6-9-deployment-record.md`](6-9-deployment-record.md) | Docker + Cloudflare + Caddy 部署 |
| [`docs/6-12-ui-and-config-enhancements.md`](6-12-ui-and-config-enhancements.md) | Web 页面增强、Caddy basic_auth |
| [`docs/6-12-three-stage-conclusion.md`](6-12-three-stage-conclusion.md) | 三段式实验结果与结论 |

---

## 五、运行方式

### CLI

```bash
# 方案 A：Eval only（精确优先）
uv run python -m entity_eval.run -n 100 --sample-mode random --seed 888 -w 4 -o output/run

# 方案 B：三段式（覆盖优先）
uv run python -m entity_eval.run -n 100 --infill -o output/run

# 评估
uv run python -m judge.run --entities output/run/entities.csv -o output/run
```

### Web

```bash
uv run streamlit run app.py
```

### 部署

```bash
docker-compose build && docker-compose up -d
```

详见 [`docs/6-9-deployment-record.md`](6-9-deployment-record.md)。

---

## 六、实验指标汇总（500 篇，seed 888）

| 指标 | Baseline | Three-stage (zero) |
|------|----------|-------------------|
| Entity Recall | 0.873 | **0.988** |
| Relevance Precision | **0.996** | 0.976 |
| Sentiment Accuracy | **0.983** | 0.978 |
| Risk Type Accuracy | 0.897 | **0.893** |
| Total Entities | 325 | 616 |
| Total Calls | 500 | 923 |
| Wall Clock | 205s | 441s |
| Total Tokens | 821K | 1.48M |
