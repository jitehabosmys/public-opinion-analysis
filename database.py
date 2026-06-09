import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "db-data/opinion_analysis.db")


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            article_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            total_entities INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL REFERENCES batches(id),
            doc_id TEXT NOT NULL,
            headline TEXT,
            content TEXT,
            status TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            duration_seconds REAL DEFAULT 0,
            entities_count INTEGER DEFAULT 0,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id INTEGER NOT NULL REFERENCES articles(id),
            entity TEXT NOT NULL,
            mapped_from TEXT,
            entity_sentiment TEXT,
            impact_level TEXT,
            sentiment_reason TEXT,
            risk_type TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_batch ON articles(batch_id);
        CREATE INDEX IF NOT EXISTS idx_batches_created ON batches(created_at);
    """)
    conn.commit()
    conn.close()


def save_batch(batch_id: str, source: str, articles_data: list, records: list, failures: list):
    conn = get_conn()

    record_map = {r["doc_id"]: r for r in records}
    failed_ids = {f["doc_id"] for f in failures}
    article_count = len(articles_data)
    success_count = 0
    total_entities = 0
    total_tokens = 0

    for article in articles_data:
        record = record_map.get(article.doc_id, {})
        status = "failed" if article.doc_id in failed_ids else "success"
        if status == "success":
            success_count += 1

        prompt_tk = record.get("prompt_tokens", 0) or 0
        completion_tk = record.get("completion_tokens", 0) or 0
        ent_count = record.get("entities_count", 0) or 0
        total_entities += ent_count
        total_tokens += prompt_tk + completion_tk

        cur = conn.execute(
            """INSERT INTO articles
               (batch_id, doc_id, headline, content, status,
                prompt_tokens, completion_tokens, duration_seconds, entities_count, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                batch_id, article.doc_id, article.headline, article.content,
                status, prompt_tk, completion_tk,
                record.get("duration_seconds", 0) or 0,
                ent_count, record.get("error"),
            ),
        )
        article_id = cur.lastrowid

    conn.execute(
        "INSERT OR REPLACE INTO batches (id, source, article_count, success_count, total_entities, total_tokens, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (batch_id, source, article_count, success_count, total_entities, total_tokens),
    )

    conn.commit()
    conn.close()


def save_entities(batch_id: str, entities_data: list):
    conn = get_conn()

    article_map = {}
    for row in conn.execute(
        "SELECT id, doc_id FROM articles WHERE batch_id = ?", (batch_id,)
    ).fetchall():
        article_map[row["doc_id"]] = row["id"]

    for ent in entities_data:
        article_id = article_map.get(ent.get("doc_id", ""))
        if article_id is None:
            continue
        conn.execute(
            """INSERT INTO entities
               (article_id, entity, mapped_from, entity_sentiment, impact_level, sentiment_reason, risk_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                article_id, ent["entity"], ent.get("mapped_from", ""),
                ent["entity_sentiment"], ent.get("impact_level", ""),
                ent.get("sentiment_reason", ""), ent.get("risk_type", ""),
            ),
        )

    conn.commit()
    conn.close()


def list_batches(limit: int = 20) -> list[sqlite3.Row]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


def get_batch_detail(batch_id: str) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    conn = get_conn()
    articles = conn.execute(
        "SELECT id, doc_id, headline, status, entities_count, prompt_tokens, completion_tokens, duration_seconds, error "
        "FROM articles WHERE batch_id = ? ORDER BY id", (batch_id,)
    ).fetchall()
    article_ids = [a["id"] for a in articles]
    entities = []
    if article_ids:
        placeholders = ",".join("?" * len(article_ids))
        entities = conn.execute(
            f"SELECT e.*, a.doc_id FROM entities e "
            f"JOIN articles a ON a.id = e.article_id "
            f"WHERE e.article_id IN ({placeholders}) ORDER BY e.article_id",
            article_ids,
        ).fetchall()
    conn.close()
    return articles, entities
