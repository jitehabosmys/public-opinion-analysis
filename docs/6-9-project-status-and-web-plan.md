# 6-9 项目状态与 Web 服务实现记录

> 本项目是对新闻/舆情文章进行商业实体抽取与风险分析的工具。利用 LLM 从文章中提取公司、金融机构等商业实体，判断事件影响方向（利好/中性/利空）、影响程度和风险类别，输出结构化数据。当前已实现 CLI 工具（`entity_eval/run.py`）、judge 评估体系和 Streamlit Web 服务封装。

## 一、当前项目状态

### Eval Prompt 版本

当前 prompt 为"宽松策略 + 后处理兜底"：

- `## 明确不输出` 仅保留硬排除项（个人、政府、指数、商品、媒体）
- 前置了 `核心原则`（行业报告有数据的公司应输出）
- 补充了 `映射示例`（产品→母公司映射）
- 示例替换为人形机器人行业报告

### 后处理 filter pattern（14 条）

```
仅提及、仅作为、仅被提及
无具体事件或数据、无具体事件或业务影响、无具体协议或业务影响
没有具体事件、未提及具体、未涉及其自身
只是背景、作为对比、历史背景
成分股、成交额
```

### 风险分类

| 字段 | 取值 | 条件 |
|------|------|------|
| risk_type | 产品质量、财务风险、监管合规、经营风险、竞争风险、品牌声誉（多选） | 仅利空 |
| impact_level | 大/中/小 | 仅利空 |

### Judge 恢复 recall 评估

- `_compute_scores` 恢复 `entity_recall = keep / (keep + len(missed))`
- 输出格式改为 `{"missed": [{"entity":..., "reason":...}], "extra": [...]}`
- prompt 新增 `## 不应列为 missed` 区块（ETF成分股、纯股价、名单、行业背景、观点传闻、非商业主体、映射后名称、常规治理事项）
- 新增 3 个示例覆盖 ETF、纯股价、名单等不应列为 missed 的场景

### 当前指标（seed 444 / seed 555）

| 指标 | seed 444 | seed 555 |
|------|----------|----------|
| avg_recall | 0.734 | 0.675 |
| avg_precision | 0.992 | 0.981 |

### 已废弃/暂缓的功能

- `--retry-empty`：0 实体用正式 prompt 重抽，恢复率低（1/24），效果有限
- `--rerun-empty`：保留诊断用，但不做主要迭代手段
- 搜索模块：成本与复杂度偏高，暂缓

---

## 二、Web 服务实现

### 技术选型

| 层 | 方案 | 理由 |
|----|------|------|
| 前端 | Streamlit | 单文件即可出界面，无需前后端分离 |
| 后端 | Streamlit 内置 | CLI 的 EntityEvalAgent 可直接复用 |
| 文件 | app.py | 放在项目根目录，复用现有抽取与后处理函数 |
| 依赖 | pyproject.toml + uv.lock | 明确声明 Web 运行依赖，避免只依赖本地 `.venv` |

### 启动方式

```bash
.venv/bin/streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
```

浏览器访问 `http://localhost:8501`。

也可以在依赖已同步后使用：

```bash
uv run streamlit run app.py
```

本地安装 Streamlit 时如默认源过慢，可使用国内镜像：

```bash
uv pip install --python .venv/bin/python -i https://pypi.tuna.tsinghua.edu.cn/simple streamlit
```

### 依赖配置

新增 `pyproject.toml`，核心依赖：

- `streamlit`
- `pandas`
- `openai`
- `python-dotenv`
- `openpyxl`

`uv.lock` 已生成，用于固定解析后的依赖版本。

### 输入方式（两种 Tab）

**Tab 1：CSV 上传**

- 限定 ≤100 行，超过则提示错误
- 校验必需列：`doc_id`、`headline`、`content`
- 缺少列时提示具体错误
- 上传成功后预览前 20 行

**Tab 2：手动输入**

- 每篇包含：标题（可选）+ 内容（必需）
- 可添加多篇（最多 100 篇）
- 自动生成 UUID 作为 doc_id
- 不根据内容反查原始数据中的 `doc_id`，支持 OOD 内容输入

### 处理流程

```
用户点击"开始抽取"
  → st.progress(0) + st.status("正在处理...")
  → ThreadPoolExecutor(max_workers=4)
  → 每完成一篇 → progress_bar.progress(done/total)
  → filter + merge 后处理
  → 全部完成后自动显示结果
```

**并行方案**：使用 `ThreadPoolExecutor` + `as_completed`。

**as_completed 作为天然回调**：每篇完成后立即更新进度条，精度不损失。

**为什么不是单线程**：单线程 100 篇 × 2s ≈ 200s（3 分+），并行 4 个 worker 约 50s，两者进度条精度相同。并行除更快外，还可减少浏览器超时风险。

### 进度显示

- `st.progress(0)` → 逐篇递增 → `st.progress(1.0)`
- `st.status("正在抽取..."， expanded=True)` 实时显示当前完成的文章标题
- 单篇失败不会中断整批任务；失败文章进入 `failures` 列表

### 结果展示

实际使用 `st.dataframe` 渲染结果表，原因是 `sentiment_reason` 较长，markdown 表格容易横向溢出。

展示内容：

- 文章数、成功数、失败数、实体数 4 个指标
- 实体结果表
- 失败文章 expander
- 后处理过滤记录 expander

上传与手动输入的结果隔离：

- 上传结果存储在 `st.session_state["upload_result"]`
- 手动输入结果存储在 `st.session_state["manual_result"]`
- 两个 Tab 只展示各自结果，避免一个 Tab 的抽取结果出现在另一个 Tab

### 下载选项

| 格式 | 实现 | 备注 |
|------|------|------|
| CSV | `df.to_csv()` → `st.download_button` | 主输出格式 |
| JSONL | `json.dumps` → `st.download_button` | 元数据格式 |
| XLSX | openpyxl 生成 → `st.download_button` | 100 行约 0.1-0.3 秒，开销可忽略 |

XLSX 使用 `@st.cache_data` 装饰器，避免页面重渲染时重复生成。

由于上传结果和手动结果会各自渲染下载按钮，`download_button` 必须设置唯一 key：

- `f"{result_key}_download_csv"`
- `f"{result_key}_download_jsonl"`
- `f"{result_key}_download_xlsx"`

否则 Streamlit 会因自动生成 ID 相同报 `StreamlitDuplicateElementId`。

### API Key 管理

- 默认读取 `.env` 中的 `LLM_API_KEY`
- 启动页面后若未检测到 `LLM_API_KEY`，直接提示并停止
- 暂不支持用户在界面手动输入 Key
- 后续可按需扩展

### 文件结构（新增）

```
public-opinion-analysis/
├── app.py              # Streamlit 应用（新增）
├── pyproject.toml      # 项目依赖声明（新增）
├── uv.lock             # uv 锁文件（新增）
├── data/
│   ├── upload_sample_3.csv
│   ├── upload_sample_10.csv
│   └── upload_sample_30.csv
├── entity_eval/        # 不变
├── judge/              # 不变
└── docs/               # 文档
```

`app.py` 未修改 `entity_eval/` 和 `judge/` 的任何代码，只复用：

- `EntityEvalAgent`
- `ENTITY_COLUMNS`
- `_filter_entity_rows`
- `_merge_entity_rows`
- `_build_raw_lines`
- `_write_xlsx`

### 上传测试样例

从 `data/test_news_0603.csv` 固定随机切分出 3 个上传测试文件：

| 文件 | 行数 | 用途 |
|------|------|------|
| `data/upload_sample_3.csv` | 3 | 快速连通性测试 |
| `data/upload_sample_10.csv` | 10 | 小批量测试 |
| `data/upload_sample_30.csv` | 30 | 稍大批量测试 |

三个文件均只保留上传必需列：`doc_id`、`headline`、`content`。

---

## 三、已确认的设计决策

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 前端框架 | Streamlit | 单文件起步，复用现有 Python 逻辑 |
| 处理方式 | 并行（ThreadPoolExecutor w=4） | 比单线程快 3-4 倍 |
| 进度条 | as_completed 天然回调 | 每篇完成即更新，精度不损失 |
| 结果展示 | st.dataframe 表格 | 更适合展示长理由字段和较宽表格 |
| 行数限制 | ≤100 篇 | 控制处理时间在 1 分钟内 |
| API Key | .env 默认配置 | 简化用户操作 |
| 搜索模块 | 暂不集成 | 成本与复杂度偏高 |
| XLSX 下载 | 支持，缓存生成 | 避免重渲染时重复生成 |
| 手动 doc_id | UUID | 不反查原始数据，支持 OOD 输入 |
| Tab 结果状态 | 分开保存 | 防止上传/手动结果互相串台 |
| 是否需要修改现有核心代码 | 否 | app.py 独立，只 import |

---

## 四、验证记录

已完成：

- 手动输入抽取成功
- 下载 CSV/JSONL/XLSX 可用
- 上传文件抽取可跑完
- 修复上传/手动两个结果区下载按钮 ID 冲突
- `app.py` 编译通过
- `import app` 通过
- 3/10/30 行上传测试文件均通过 `_validate_articles`

验证命令：

```bash
.venv/bin/python -m py_compile app.py
.venv/bin/python -c "import app; print('app import ok')"
```

---

## 五、后续可扩展方向

- **用户自定义 API Key**：界面输入框 + 临时环境变量
- **batch 限制可调**：从 100 放宽到 500 或 1000
- **历史记录**：用 `st.session_state` 或数据库保存最近几次结果
- **搜索模块集成**：mapped_from 查询实体正式名称
- **部署**：用 docker 打包，支持 `docker compose up` 一键启动
