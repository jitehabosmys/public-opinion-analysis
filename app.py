import io
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import database

logger = logging.getLogger("public-opinion-web")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from entity_eval.agent import EntityEvalAgent
from entity_eval.run import (
    ENTITY_COLUMNS,
    _build_raw_lines,
    _dedup_reviewer_entities,
    _filter_entity_rows,
    _merge_entity_rows,
    _write_xlsx,
)
from reviewer.agent import ReviewerAgent

load_dotenv()
database.init_db()

MAX_ARTICLES = 100
MAX_WORKERS = 4


@dataclass
class Article:
    doc_id: str
    headline: str
    content: str


def _normalize_text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def _validate_articles(df: pd.DataFrame) -> tuple[list[Article], list[str]]:
    required = {"headline", "content"}
    missing = sorted(required - set(df.columns))
    if missing:
        return [], [f"缺少必需列：{', '.join(missing)}"]

    if len(df) > MAX_ARTICLES:
        return [], [f"最多支持 {MAX_ARTICLES} 篇，当前上传 {len(df)} 篇。"]

    has_doc_id = "doc_id" in df.columns
    errors = []
    articles = []
    for idx, row in df.iterrows():
        doc_id = _normalize_text(row["doc_id"]) if has_doc_id else ""
        headline = _normalize_text(row["headline"])
        content = _normalize_text(row["content"])
        if not content:
            errors.append(f"第 {idx + 1} 行 content 为空。")
            continue
        if not doc_id:
            doc_id = str(uuid.uuid4())
        articles.append(Article(doc_id=doc_id, headline=headline, content=content))

    return articles, errors


def _manual_articles(count: int) -> list[Article]:
    articles = []
    for i in range(count):
        with st.container(border=True):
            st.caption(f"文章 {i + 1}")
            headline = st.text_input("标题", key=f"manual_headline_{i}")
            content = st.text_area("内容", key=f"manual_content_{i}", height=160)
        content = content.strip()
        if content:
            articles.append(
                Article(
                    doc_id=str(uuid.uuid4()),
                    headline=headline.strip(),
                    content=content,
                )
            )
    return articles


def _article_snippet(article: Article) -> str:
    title = article.headline or article.content
    return title[:36] + ("..." if len(title) > 36 else "")


def _run_extraction(articles: list[Article], batch_id: str, source: str,
                    reviewer_mode: str = ""):
    agent = EntityEvalAgent()
    source_map = {article.doc_id: (article.headline, article.content) for article in articles}

    progress = st.progress(0)
    status = st.status("正在抽取...", expanded=True)
    rows: list[dict] = []
    records: list[dict] = []
    failures: list[dict] = []

    with status:
        total = len(articles)
        done = 0

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, total)) as pool:
            futures = {
                pool.submit(
                    agent.evaluate,
                    article.doc_id,
                    article.headline,
                    article.content,
                ): article
                for article in articles
            }

            for future in as_completed(futures):
                article = futures[future]
                done += 1
                try:
                    entities, record = future.result()
                    records.append(
                        {
                            "doc_id": record.doc_id,
                            "success": record.success,
                            "duration_seconds": record.duration_seconds,
                            "prompt_tokens": record.prompt_tokens,
                            "completion_tokens": record.completion_tokens,
                            "entities_count": record.entities_count,
                            "error": record.error,
                        }
                    )
                    logger.info(
                        "OK doc_id=%s headline=%s entities=%d tokens=%d time=%.1fs",
                        article.doc_id, _article_snippet(article),
                        record.entities_count,
                        record.prompt_tokens + record.completion_tokens,
                        record.duration_seconds,
                    )
                    for ent in entities:
                        rows.append(
                            {
                                "doc_id": article.doc_id,
                                "entity": ent.entity,
                                "mapped_from": ent.mapped_from,
                                "entity_sentiment": ent.entity_sentiment,
                                "impact_level": ent.impact_level,
                                "sentiment_reason": ent.sentiment_reason,
                                "risk_type": "，".join(ent.risk_type),
                            }
                        )
                    st.write(f"[{done}/{total}] 完成：{_article_snippet(article)}")
                except Exception as exc:
                    message = str(exc)
                    logger.warning(
                        "FAIL doc_id=%s headline=%s error=%s",
                        article.doc_id, _article_snippet(article), message,
                    )
                    failures.append(
                        {
                            "doc_id": article.doc_id,
                            "headline": article.headline,
                            "error": message,
                        }
                    )
                    records.append(
                        {
                            "doc_id": article.doc_id,
                            "success": False,
                            "duration_seconds": 0,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "entities_count": 0,
                            "error": message,
                        }
                    )
                    st.write(f"[{done}/{total}] 失败：{_article_snippet(article)}")
                progress.progress(done / total)

    kept_rows, filtered_rows = _filter_entity_rows(rows)
    merged_rows = _merge_entity_rows(kept_rows)

    reviewer_recovered = 0
    if reviewer_mode:
        status.update(label="正在遗漏审查...", expanded=True)
        reviewer_agent = ReviewerAgent()
        existing_by_doc = {}
        for r in rows:
            existing_by_doc.setdefault(r["doc_id"], []).append({
                "entity": r["entity"],
                "mapped_from": r.get("mapped_from", ""),
                "entity_sentiment": r.get("entity_sentiment", ""),
                "sentiment_reason": r.get("sentiment_reason", ""),
            })

        if reviewer_mode == "zero":
            zero_doc_ids = {r["doc_id"] for r in records if r["success"] and r["entities_count"] == 0}
            review_articles = [a for a in articles if a.doc_id in zero_doc_ids]
        else:
            review_articles = articles

        reviewer_rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(articles))) as pool:
            meta = {}
            for article in review_articles:
                existing = existing_by_doc.get(article.doc_id, [])
                future = pool.submit(
                    reviewer_agent.review, article.doc_id,
                    article.headline, article.content, existing,
                )
                meta[future] = article

            review_done = 0
            review_total = len(review_articles)
            st.write(f"--- 遗漏审查阶段（{reviewer_mode} 模式） ---")
            for future in as_completed(meta):
                article = meta[future]
                review_done += 1
                try:
                    entities, record = future.result()
                    records.append({
                        "doc_id": record.doc_id,
                        "success": record.success,
                        "duration_seconds": record.duration_seconds,
                        "prompt_tokens": record.prompt_tokens,
                        "completion_tokens": record.completion_tokens,
                        "entities_count": record.entities_count,
                        "error": record.error,
                    })
                    for ent in entities:
                        reviewer_rows.append({
                            "doc_id": article.doc_id,
                            "entity": ent.entity,
                            "mapped_from": ent.mapped_from,
                            "entity_sentiment": ent.entity_sentiment,
                            "impact_level": ent.impact_level,
                            "sentiment_reason": ent.sentiment_reason,
                            "risk_type": "，".join(ent.risk_type),
                        })
                    if record.entities_count > 0:
                        st.write(f"[review {review_done}/{review_total}] 发现 {record.entities_count} 个遗漏")
                except Exception as exc:
                    logger.warning("REVIEW FAIL doc_id=%s error=%s", article.doc_id, exc)
                    st.write(f"[review {review_done}/{review_total}] 失败：{_article_snippet(article)}")

        reviewer_rows, review_filtered = _filter_entity_rows(reviewer_rows)
        rows_before = len(merged_rows)
        merged_rows = _dedup_reviewer_entities(merged_rows, reviewer_rows)
        reviewer_recovered = len(merged_rows) - rows_before
        if reviewer_recovered:
            logger.info("Reviewer recovered %d entities", reviewer_recovered)
            st.write(f"遗漏审查补充了 {reviewer_recovered} 个实体（{len(review_filtered)} 个被后处理过滤）")

    entities_df = pd.DataFrame(merged_rows, columns=ENTITY_COLUMNS)
    raw_lines = _build_raw_lines([article.doc_id for article in articles], merged_rows)

    summary = {
        "total_articles": len(articles),
        "success": sum(1 for r in records if r["success"]),
        "failed": sum(1 for r in records if not r["success"]),
        "total_entities_before_filter": len(rows),
        "post_filtered_entities": len(filtered_rows),
        "total_entities": len(merged_rows),
        "reviewer_recovered": reviewer_recovered,
        "records": records,
        "failures": failures,
        "filtered": filtered_rows + (review_filtered if reviewer_mode else []),
    }

    database.save_batch(batch_id, source, articles, records, failures)
    database.save_entities(batch_id, merged_rows)

    label = "抽取完成"
    if reviewer_recovered:
        label += f"（补充 {reviewer_recovered} 实体）"
    status.update(
        label=label,
        state="complete" if not failures else "error",
        expanded=False,
    )
    return entities_df, raw_lines, summary, source_map


@st.cache_data
def _to_xlsx_bytes(df: pd.DataFrame, source: dict, doc_ids: list[str]) -> bytes:
    buffer = io.BytesIO()
    _write_xlsx(df, buffer, "entities", source_data=source, doc_ids=doc_ids)
    buffer.seek(0)
    return buffer.read()


def _to_jsonl(raw_lines: list[dict]) -> str:
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in raw_lines) + "\n"


def _render_results(result_key: str, title: str):
    result = st.session_state.get(result_key)
    if not result:
        return

    entities_df, raw_lines, summary, source = result
    st.divider()
    st.subheader(title)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("文章数", summary["total_articles"])
    col2.metric("成功", summary["success"])
    col3.metric("失败", summary["failed"])
    col4.metric("实体数", summary["total_entities"])

    if summary["failures"]:
        with st.expander("失败文章", expanded=True):
            st.data_editor(pd.DataFrame(summary["failures"]), use_container_width=True, disabled=True)

    if summary["filtered"]:
        with st.expander("后处理过滤记录"):
            st.data_editor(pd.DataFrame(summary["filtered"]), use_container_width=True, disabled=True)

    if entities_df.empty:
        st.info("没有抽取到有效商业实体。")
    else:
        st.data_editor(entities_df, use_container_width=True, hide_index=True, disabled=True)

    doc_ids = list(source.keys())
    csv_bytes = entities_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    jsonl_bytes = _to_jsonl(raw_lines).encode("utf-8")
    xlsx_bytes = _to_xlsx_bytes(entities_df, source, doc_ids)

    left, middle, right = st.columns(3)
    left.download_button(
        "下载 CSV",
        csv_bytes,
        file_name="entities.csv",
        mime="text/csv",
        key=f"{result_key}_download_csv",
        use_container_width=True,
    )
    middle.download_button(
        "下载 JSONL",
        jsonl_bytes,
        file_name="entities.jsonl",
        mime="application/jsonl",
        key=f"{result_key}_download_jsonl",
        use_container_width=True,
    )
    right.download_button(
        "下载 XLSX",
        xlsx_bytes,
        file_name="entities.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{result_key}_download_xlsx",
        use_container_width=True,
    )


def main():
    st.set_page_config(page_title="金融舆情实体风险分析", layout="wide")
    st.title("金融舆情实体风险分析")

    if not os.getenv("LLM_API_KEY"):
        st.error("未检测到 LLM_API_KEY。请在 .env 中配置后再启动应用。")
        st.stop()

    reviewer_mode = st.selectbox(
        "遗漏审查模式", ["", "all", "zero"],
        format_func=lambda x: {"": "不启用", "all": "全部文章（~2x 耗时）", "zero": "仅 0 实体文章（~1.3x 耗时）"}[x],
        help="在首次抽取后，由 AI 审查遗漏的商业实体并补充。all 覆盖更全，zero 性价比更高。",
    )

    tab_upload, tab_manual, tab_history = st.tabs(["CSV 上传", "手动输入", "历史记录"])

    with tab_upload:
        st.info(
            "**CSV 格式说明**  \n"
            "必需列：`headline`（标题）、`content`（正文）  \n"
            "可选列：`doc_id`（文章 ID，不填自动生成 UUID）  \n\n"
            "示例：\n"
            "```\n"
            "headline,content\n"
            "\"公司A获投资\",\"公司A今日宣布完成新一轮融资...\"\n"
            "\"公司B发布财报\",\"公司B昨日公布2024年财报...\"\n"
            "```"
        )
        uploaded_files = st.file_uploader("上传 CSV 文件（支持多个，总行数 ≤100）", type=["csv"], accept_multiple_files=True)
        upload_articles: list[Article] = []
        upload_errors: list[str] = []
        if uploaded_files:
            dfs = []
            for f in uploaded_files:
                try:
                    dfs.append(pd.read_csv(f))
                except Exception as exc:
                    st.error(f"文件 {f.name} 读取失败：{exc}")
            if dfs:
                df = pd.concat(dfs, ignore_index=True)
                upload_articles, upload_errors = _validate_articles(df)
                if upload_errors:
                    for error in upload_errors:
                        st.error(error)
                else:
                    st.success(f"已读取 {len(upload_articles)} 篇文章（来自 {len(uploaded_files)} 个文件）。")
                    preview_cols = [c for c in ["doc_id", "headline", "content"] if c in df.columns]
                    st.dataframe(
                        df[preview_cols].head(20),
                        use_container_width=True,
                        hide_index=True,
                    )

        if st.button("开始抽取上传内容", disabled=not upload_articles):
            batch_id = str(uuid.uuid4())
            logger.info("BATCH start source=upload articles=%d", len(upload_articles))
            st.session_state["upload_result"] = _run_extraction(upload_articles, batch_id, "upload", reviewer_mode)
            logger.info("BATCH end source=upload")

        _render_results("upload_result", "上传文件抽取结果")

    with tab_manual:
        count = st.number_input(
            "文章数量",
            min_value=1,
            max_value=MAX_ARTICLES,
            value=1,
            step=1,
        )
        manual_articles = _manual_articles(int(count))
        st.caption(f"已填写 {len(manual_articles)} / {int(count)} 篇有效内容。")
        if st.button("开始抽取手动内容", disabled=not manual_articles):
            batch_id = str(uuid.uuid4())
            logger.info("BATCH start source=manual articles=%d", len(manual_articles))
            st.session_state["manual_result"] = _run_extraction(manual_articles, batch_id, "manual", reviewer_mode)
            logger.info("BATCH end source=manual")

        _render_results("manual_result", "手动输入抽取结果")

    with tab_history:
        preset = st.radio(
            "时间范围",
            ["近24小时", "近7天", "近30天", "全部", "自定义"],
            horizontal=True,
            label_visibility="collapsed",
        )

        since = None
        until = None
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if preset == "近24小时":
            since = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        elif preset == "近7天":
            since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        elif preset == "近30天":
            since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        elif preset == "自定义":
            date_col1, date_col2 = st.columns(2)
            with date_col1:
                d_from = st.date_input("从")
            with date_col2:
                d_to = st.date_input("至")
            if d_from:
                since = str(d_from)
            if d_to:
                until = str(d_to) + " 23:59:59"

        batches = database.list_batches(since=since, until=until, limit=100)
        if not batches:
            st.info("暂无历史记录。")
        else:
            data = []
            for b in batches:
                data.append({
                    "时间": b["created_at"][:19],
                    "来源": b["source"],
                    "文章": b["article_count"],
                    "成功": b["success_count"],
                    "实体数": b["total_entities"],
                    "Token": b["total_tokens"],
                })
            st.data_editor(pd.DataFrame(data), use_container_width=True, hide_index=True, disabled=True)

            batch_options = {f"{b['created_at'][:19]} ｜ {b['source']} ｜ {b['article_count']}篇 ｜ {b['total_entities']}实体": b["id"] for b in batches}
            selected_label = st.selectbox("查看详细记录", list(batch_options.keys()))
            selected = batch_options.get(selected_label)

            if selected:
                articles, entities = database.get_batch_detail(selected)
                if not articles:
                    st.info("无法获取详情。")
                else:
                    for a in articles:
                        label = f"{a['headline'] or '(无标题)'} — {a['status']} ({a['entities_count']} 实体, {a['prompt_tokens'] + a['completion_tokens']} token, {a['duration_seconds']:.1f}s)"
                        with st.expander(label):
                            if a["status"] == "failed":
                                st.error(f"失败原因：{a['error']}")
                            article_entities = [e for e in entities if e["doc_id"] == a["doc_id"]]
                            if article_entities:
                                st.data_editor(
                                    pd.DataFrame([{
                                        "entity": e["entity"],
                                        "mapped_from": e["mapped_from"],
                                        "sentiment": e["entity_sentiment"],
                                        "impact": e["impact_level"],
                                        "reason": e["sentiment_reason"],
                                        "risk_type": e["risk_type"],
                                    } for e in article_entities]),
                                    use_container_width=True,
                                    hide_index=True,
                                    disabled=True,
                                )


if __name__ == "__main__":
    main()
