# 6-9 数据库存储与 Prompt One-Shot 更新

---

## 一、SQLite 数据库

### 动机

文本日志只能流式查看，无法回溯历史抽取记录。新增 SQLite 存储，每次抽取自动入库，并在 Web 页面增加"历史记录"Tab。

### Schema（3 张表）

#### batches

| 列 | 类型 | 说明 |
|------|------|------|
| id | TEXT PK | 批次 UUID |
| source | TEXT | upload / manual |
| article_count | INTEGER | 文章总数 |
| success_count | INTEGER | 成功数 |
| total_entities | INTEGER | 抽取实体总数 |
| total_tokens | INTEGER | 总 token 消耗（prompt + completion） |
| created_at | TEXT | 入库时间 |

#### articles

| 列 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| batch_id | TEXT FK | 所属批次 |
| doc_id | TEXT | 文章 ID |
| headline | TEXT | 标题 |
| content | TEXT | 正文 |
| status | TEXT | success / failed |
| prompt_tokens | INTEGER | 输入 token 数 |
| completion_tokens | INTEGER | 输出 token 数 |
| duration_seconds | REAL | API 耗时 |
| entities_count | INTEGER | 该文章抽取实体数 |
| error | TEXT | 失败原因 |

#### entities

| 列 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| article_id | INTEGER FK | 所属文章 |
| entity | TEXT | 实体名称 |
| mapped_from | TEXT | 产品/品牌名映射 |
| entity_sentiment | TEXT | 利好/中性/利空 |
| impact_level | TEXT | 大/中/小 |
| sentiment_reason | TEXT | 判断理由 |
| risk_type | TEXT | 风险类别 |

### 文件结构

- `database.py` — 数据库模块（init / CRUD）
- `db-data/opinion_analysis.db` — SQLite 文件（通过 docker-compose volume 持久化到宿主机）
- `app.py` — 在 `_run_extraction` 末尾调用 `save_batch` + `save_entities`

### docker-compose.yml 变更

新增 `volumes` 挂载：

```yaml
volumes:
  - ./db-data:/app/db-data
```

确保容器重建不丢失数据。

### Web 历史记录 Tab

第三个 Tab"历史记录"显示：

1. 最近 20 批次汇总表格（时间 / 来源 / 文章数 / 实体数 / Token）
2. 下拉选择框展示每批详情
3. 展开后逐篇显示实体抽取结果

### 已知限制

- 所有登录用户共享同一份历史记录（Caddy basic_auth 仅一个账号 `Shiodome`）
- 历史数据不可删除（可后续加清除按钮）

---

## 二、Prompt One-Shot 示例更新

### 改动文件

`entity_eval/agent.py` — `SYSTEM_PROMPT` 中的示例

### 旧示例问题

- 3 个实体全部为**利好**，无利空示例
- 利好实体填了 `impact_level`（与指令矛盾：指令写明"仅在利空时填写"）
- 无法体现利空时 `impact_level` + `risk_type` 的正确填写方式

### 新示例

```
标题：特斯拉因刹车隐患召回超10万辆Model Y，比亚迪迎来新机遇
正文：国家市场监管总局公告，特斯拉因刹车系统隐患召回2023年至2025年生产的
部分Model Y，共计10.2万辆。受此影响，特斯拉股价下跌5%。而比亚迪同日
宣布旗舰车型订单突破50万辆。
输出：
[
  {"entity": "特斯拉", "mapped_from": "Model Y", "entity_sentiment": "利空",
   "impact_level": "中",
   "sentiment_reason": "因刹车隐患召回10.2万辆Model Y，股价下跌5%",
   "risk_type": "产品质量"},
  {"entity": "比亚迪", "mapped_from": "", "entity_sentiment": "利好",
   "impact_level": "",
   "sentiment_reason": "旗舰车型订单突破50万辆，反映市场竞争力增强",
   "risk_type": ""}
]
```

| 实体 | 情感 | impact_level | risk_type | 说明 |
|------|------|-------------|-----------|------|
| 特斯拉 | 利空 | 中 ✅ | 产品质量 ✅ | 正确填写 |
| 比亚迪 | 利好 | 空 ✅ | 空 ✅ | 按指令留空 |

保留了 `mapped_from`（Model Y → 特斯拉）的映射用法。
