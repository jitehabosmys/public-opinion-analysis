# 6-12 页面增强与配置优化

> 对 Web 页面和 Caddy 配置的一系列改进。

---

## 一、Caddy basic_auth + 媒体下载免鉴权

### 动机

防止未授权访问消耗 API Token，同时允许 FDM 等第三方下载器正常下载文件。

### 改动

`/etc/caddy/Caddyfile` 中 `opinion.sytssmys.top` 站点配置：

```caddy
@protected {
    not path /media/*
}
basic_auth @protected {
    Shiodome $2a$14$...
}
```

| 路径 | 认证要求 | 说明 |
|------|----------|------|
| `/` | 需要 | 首页、抽取功能 |
| `/media/*` | 免认证 | Streamlit 的下载文件（URL 含随机 hash，不可猜测） |

### 效果

- 浏览器访问首页弹登录框
- 下载按钮生成的 `/media/xxx.csv` 地址直接下载，不弹框（FDM 可用）

---

## 二、CSV 上传优化

### 2a. Schema 提示

上传区域上方增加 CSV 格式说明：

```
必需列：headline（标题）、content（正文）
可选列：doc_id（文章 ID，不填自动生成 UUID）

示例：
headline,content
"公司A获投资","公司A今日宣布完成新一轮融资..."
```

### 2b. doc_id 可选

`_validate_articles` 中 `doc_id` 不再强制。缺失或为空时自动生成 UUID。

### 2c. 多文件上传

```python
st.file_uploader(..., accept_multiple_files=True)
```

支持一次性选择多个 CSV 文件，合并后校验总行数 ≤ 100。

---

## 三、表格组件升级

`st.dataframe` 替换为 `st.data_editor(disabled=True)`，视觉上基本一致，但每列顶部增加筛选输入框，可按值过滤行：

| 位置 | 文件 | 行数 |
|------|------|------|
| 抽取结果表 | `_render_results` | 1 |
| 失败文章 expander | `_render_results` | 1 |
| 后处理过滤记录 | `_render_results` | 1 |
| 历史列表 | `tab_history` | 1 |
| 历史详情展开 | `tab_history` | 1 |

---

## 四、历史记录时间筛选

### 数据库

`database.py` — `list_batches()` 新增 `since` / `until` 参数：

```python
def list_batches(since=None, until=None, limit=100):
```

### 页面

历史 Tab 顶部新增横向单选按钮组：

```
[近24小时] [近7天] [近30天] [全部] [自定义]
```

- 预设按钮直接切换
- "自定义"展开日期选择器（从/至）
- 筛选联动下方的批次列表和详情
- 默认限制取 100 条批次

---

## 涉及的代码文件

| 文件 | 改动 |
|------|------|
| `/etc/caddy/Caddyfile` | basic_auth + /media/* 免认证 |
| `entity_eval/agent.py` | one-shot 示例更新（另见 docs/6-9-db-and-prompt-update.md） |
| `database.py` | list_batches 增加时间范围参数 |
| `app.py` | 多文件上传、data_editor、历史筛选、CSV 提示、doc_id 可选 |
| `docker-compose.yml` | db-data volume 挂载（另见 docs/6-9-db-and-prompt-update.md） |

---

## 对应提交

```
cfb00d4  add web logging, optional doc_id in csv, and csv schema hint
0bccf55  fix variable shadowing bug
d891d27  update one-shot example
a8f4844  add sqlite storage for extraction history
```
