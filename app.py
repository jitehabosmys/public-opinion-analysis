import io
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

logger = logging.getLogger("public-opinion-web")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from entity_eval.agent import EntityEvalAgent
from entity_eval.run import (
    ENTITY_COLUMNS,
    _build_raw_lines,
    _filter_entity_rows,
    _merge_entity_rows,
    _write_xlsx,
)

load_dotenv()

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


def _run_extraction(articles: list[Article]):
    agent = EntityEvalAgent()
    source = {article.doc_id: (article.headline, article.content) for article in articles}

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
    entities_df = pd.DataFrame(merged_rows, columns=ENTITY_COLUMNS)
    raw_lines = _build_raw_lines([article.doc_id for article in articles], merged_rows)

    summary = {
        "total_articles": len(articles),
        "success": sum(1 for r in records if r["success"]),
        "failed": sum(1 for r in records if not r["success"]),
        "total_entities_before_filter": len(rows),
        "post_filtered_entities": len(filtered_rows),
        "total_entities": len(merged_rows),
        "records": records,
        "failures": failures,
        "filtered": filtered_rows,
    }

    status.update(
        label="抽取完成",
        state="complete" if not failures else "error",
        expanded=False,
    )
    return entities_df, raw_lines, summary, source


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
            st.dataframe(pd.DataFrame(summary["failures"]), use_container_width=True)

    if summary["filtered"]:
        with st.expander("后处理过滤记录"):
            st.dataframe(pd.DataFrame(summary["filtered"]), use_container_width=True)

    if entities_df.empty:
        st.info("没有抽取到有效商业实体。")
    else:
        st.dataframe(entities_df, use_container_width=True, hide_index=True)

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

    tab_upload, tab_manual = st.tabs(["CSV 上传", "手动输入"])

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
        uploaded = st.file_uploader("上传 CSV 文件", type=["csv"])
        upload_articles: list[Article] = []
        upload_errors: list[str] = []
        if uploaded is not None:
            try:
                df = pd.read_csv(uploaded)
                upload_articles, upload_errors = _validate_articles(df)
                if upload_errors:
                    for error in upload_errors:
                        st.error(error)
                else:
                    st.success(f"已读取 {len(upload_articles)} 篇文章。")
                    preview_cols = [c for c in ["doc_id", "headline", "content"] if c in df.columns]
                    st.dataframe(
                        df[preview_cols].head(20),
                        use_container_width=True,
                        hide_index=True,
                    )
            except Exception as exc:
                st.error(f"CSV 读取失败：{exc}")

        if st.button("开始抽取上传内容", disabled=not upload_articles):
            logger.info("BATCH start source=upload articles=%d", len(upload_articles))
            st.session_state["upload_result"] = _run_extraction(upload_articles)
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
            logger.info("BATCH start source=manual articles=%d", len(manual_articles))
            st.session_state["manual_result"] = _run_extraction(manual_articles)
            logger.info("BATCH end source=manual")

        _render_results("manual_result", "手动输入抽取结果")


if __name__ == "__main__":
    main()
