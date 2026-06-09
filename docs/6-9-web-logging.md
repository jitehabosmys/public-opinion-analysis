# 6-9 Web 服务日志功能

> 为 Streamlit Web 应用添加结构化日志输出，记录用户提交的抽取任务和每篇文章的处理结果。

---

## 改动说明

仅修改 `app.py`，新增 3 处共 6 行日志代码，未修改 `entity_eval/` 等核心模块。

### 1. 日志初始化（文件顶部）

```python
import logging

logger = logging.getLogger("public-opinion-web")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
```

日志通过标准库 `logging` 输出到 stderr，会被 Docker 的日志驱动捕获，通过 `docker-compose logs` 查看。

### 2. 每篇处理结果日志（`_run_extraction` 中）

成功时：

```python
logger.info(
    "OK doc_id=%s headline=%s entities=%d tokens=%d time=%.1fs",
    article.doc_id, _article_snippet(article),
    record.entities_count,
    record.prompt_tokens + record.completion_tokens,
    record.duration_seconds,
)
```

失败时：

```python
logger.warning(
    "FAIL doc_id=%s headline=%s error=%s",
    article.doc_id, _article_snippet(article), message,
)
```

### 3. 批次起止日志（按钮 handler 中）

```python
logger.info("BATCH start source=upload articles=%d", len(articles))
# ... 执行抽取 ...
logger.info("BATCH end source=upload")
```

### 日志字段说明

| 字段 | 示例 | 说明 |
|------|------|------|
| `doc_id` | `2025022600005345213` | 文章唯一标识 |
| `headline` | `安琪酵母买入评级` | 标题（截断至 36 字） |
| `entities` | `2` | 抽取到的商业实体数 |
| `tokens` | `2276` | 本次 API 调用总 token 消耗（prompt + completion） |
| `time` | `4.1s` | API 调用耗时 |
| `error` | `Connection timeout` | 失败时的错误信息（仅 FAIL 行） |
| `source` | `upload` / `manual` | 提交来源（BATCH 行） |

---

## 查看方式

```bash
docker-compose logs -f
# 或带过滤
docker-compose logs --tail 100 --no-color 2>/dev/null
# 只查看应用日志（过滤 httpx 请求行）
docker-compose logs --tail 100 2>/dev/null | grep "public-opinion-web"
```

---

## 实际日志输出示例

以下为 2026-06-09 上传 10 篇文章的完整日志输出：

```
2026-06-09 08:06:18,163 [public-opinion-web] INFO BATCH start source=upload articles=10
2026-06-09 08:06:21,126 [public-opinion-web] INFO OK doc_id=2025022600005345213 headline=1股获买入评级 最新:安琪酵母 entities=0 tokens=1088 time=2.8s
2026-06-09 08:06:21,196 [public-opinion-web] INFO OK doc_id=2025031121780372826 headline=平安基金管理有限公司关于旗下基金新增兴业证券股份有限公司为申购赎回代办机... entities=0 tokens=1293 time=2.9s
2026-06-09 08:06:21,284 [public-opinion-web] INFO OK doc_id=1209d7cdcecd2339bec23a5f06f835f4 headline=机械制造行业铣床高精度解决方案白皮书 entities=0 tokens=1571 time=3.0s
2026-06-09 08:06:22,419 [public-opinion-web] INFO OK doc_id=1000d7c3b10a2f33e0f4546c4132d3e4 headline=新浪券商热点小时报丨2026年05月16日22时_今日实时券商热点速递 entities=2 tokens=2276 time=4.1s
2026-06-09 08:06:22,601 [public-opinion-web] INFO OK doc_id=2025022121777771215 headline=沪电股份(002463.SZ):生产印制电路板，不生产大模型 entities=0 tokens=1172 time=1.4s
2026-06-09 08:06:23,452 [public-opinion-web] INFO OK doc_id=1cb1ab098570246425c5c4eb2a58e769 headline=双杰电气跌2.05%，成交额2.64亿元，主力资金净流出445.88万元 entities=1 tokens=1511 time=2.3s
2026-06-09 08:06:23,495 [public-opinion-web] INFO OK doc_id=2025022521770484587 headline=国轩高科投资成立光伏发电新公司 entities=1 tokens=1148 time=2.2s
2026-06-09 08:06:24,458 [public-opinion-web] INFO OK doc_id=2025022721787889605 headline=博瑞生物2024年归母净利润1.96亿元 entities=1 tokens=1191 time=1.9s
2026-06-09 08:06:24,543 [public-opinion-web] INFO OK doc_id=2025022521784419303 headline=步长制药(603858):山东步长制药股份有限公司第五届董事会第十六次会... entities=1 tokens=1871 time=2.1s
2026-06-09 08:06:25,320 [public-opinion-web] INFO OK doc_id=2025022321775530660 headline=零跑汽车与浙江职业足球俱乐部签约 entities=1 tokens=1129 time=1.9s
2026-06-09 08:06:25,373 [public-opinion-web] INFO BATCH end source=upload
```

此批次汇总：10 篇文章、4 worker 并行、总耗时 ~7s、共 7 个实体、总 token 消耗约 14k。

---

## 注意事项

- 日志输出到 stderr，Docker 默认驱动会将其捕获到 `docker logs`
- 日志**不会**记录 `LLM_API_KEY` 等敏感信息
- 日志**不会**记录用户输入的完整原文内容（headline 截断至 36 字）
- 如需更详细追踪（如抽出实体列表），可在 `for ent in entities:` 循环中增加一条日志
