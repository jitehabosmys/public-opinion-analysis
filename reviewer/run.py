"""Run reviewer on extraction results to find missed entities."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from reviewer.agent import ReviewerAgent


REVIEWER_COLUMNS = [
    "doc_id",
    "entity",
    "mapped_from",
    "entity_sentiment",
    "impact_level",
    "sentiment_reason",
    "risk_type",
]
OUTPUT_FORMATS = {"csv", "jsonl"}
SOURCE_COLUMNS = ["headline", "content"]


@dataclass
class ReviewerFailureRecord:
    doc_id: str
    success: bool = False
    duration_seconds: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    entities_count: int = 0
    error: str = ""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", required=True, help="extraction results CSV path")
    parser.add_argument("--data", default="data/test_news_0603.csv", help="original data CSV path")
    parser.add_argument("--output", "-o", default="output", help="output directory")
    parser.add_argument("--workers", "-w", type=int, default=4, help="parallel worker count")
    parser.add_argument("--model", default=None, help="model name")
    parser.add_argument("--base-url", default=None, help="API base URL")
    return parser.parse_args()


def _dedupe_entity_rows(rows: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for row in rows:
        key = (row["doc_id"], row["entity"])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


def main(args):
    os.makedirs(args.output, exist_ok=True)

    entities_df = pd.read_csv(args.entities)
    source_df = pd.read_csv(args.data).set_index("doc_id")
    headline_map = source_df["headline"].to_dict()

    doc_ids = entities_df["doc_id"].unique().tolist() if not entities_df.empty else []
    if not doc_ids:
        print("No entities found. Nothing to review.")
        return

    existing_by_doc = {}
    if not entities_df.empty:
        for doc_id, group in entities_df.groupby("doc_id"):
            existing_by_doc[doc_id] = group[["entity", "mapped_from", "entity_sentiment", "sentiment_reason"]].fillna("").to_dict("records")

    print(f"Reviewing {len(doc_ids)} articles for missed entities...")

    agent = ReviewerAgent(model=args.model, base_url=args.base_url)
    records = []
    all_missed: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        meta = {}
        for doc_id in doc_ids:
            row = source_df.loc[doc_id]
            existing = existing_by_doc.get(doc_id, [])
            future = pool.submit(
                agent.review, doc_id, row["headline"],
                str(row["content"]), existing,
            )
            meta[future] = doc_id

        done = 0
        total = len(doc_ids)
        for future in as_completed(meta):
            doc_id = meta[future]
            done += 1
            snippet = (headline_map.get(doc_id, "") or "")[:50]
            try:
                entities, record = future.result()
                records.append(record)
                for ent in entities:
                    all_missed.append({
                        "doc_id": doc_id,
                        "entity": ent.entity,
                        "mapped_from": ent.mapped_from,
                        "entity_sentiment": ent.entity_sentiment,
                        "impact_level": ent.impact_level,
                        "sentiment_reason": ent.sentiment_reason,
                        "risk_type": "，".join(ent.risk_type),
                    })
                if record.entities_count > 0:
                    names = [e.entity for e in entities]
                    print(f"[{done:>3}/{total}] found {record.entities_count} missed: {names}")
                else:
                    print(f"[{done:>3}/{total}] no missed entities")
            except Exception as e:
                records.append(ReviewerFailureRecord(doc_id=doc_id, error=str(e)))
                print(f"[{done:>3}/{total}] [err] {e} | {snippet}")

    all_missed = _dedupe_entity_rows(all_missed)
    missed_df = pd.DataFrame(all_missed, columns=REVIEWER_COLUMNS)

    missed_df.to_csv(f"{args.output}/reviewer_missed.csv", index=False, encoding="utf-8-sig")
    print(f"\nFound {len(all_missed)} missed entities total.")
    print(f"Saved to {args.output}/reviewer_missed.csv")


if __name__ == "__main__":
    main(parse_args())
