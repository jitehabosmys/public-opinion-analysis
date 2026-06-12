"""Run reviewer on extraction results to find missed entities and optionally merge."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from entity_eval.run import (
    ENTITY_COLUMNS,
    _build_raw_lines,
    _dedup_reviewer_entities,
    _filter_entity_rows,
    _merge_entity_rows,
)
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
    parser.add_argument("--merge", action="store_true", default=True,
                        help="merge reviewer findings into combined entities.csv (default: True)")
    return parser.parse_args()


def main(args):
    os.makedirs(args.output, exist_ok=True)

    original_df = pd.read_csv(args.entities)
    source_df = pd.read_csv(args.data).set_index("doc_id")

    all_doc_ids = []
    jsonl_path = os.path.join(os.path.dirname(args.entities), "entities.jsonl")
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                if "doc_id" in item:
                    all_doc_ids.append(item["doc_id"])
    if not all_doc_ids:
        all_doc_ids = original_df["doc_id"].unique().tolist() if not original_df.empty else []

    doc_ids_with_entities = original_df["doc_id"].unique().tolist() if not original_df.empty else []
    if not doc_ids_with_entities:
        print("No entities found. Nothing to review.")
        return

    existing_by_doc = {}
    if not original_df.empty:
        for doc_id, group in original_df.groupby("doc_id"):
            existing_by_doc[doc_id] = group[["entity", "mapped_from", "entity_sentiment", "sentiment_reason"]].fillna("").to_dict("records")

    print(f"Reviewing {len(doc_ids_with_entities)} articles for missed entities...")

    agent = ReviewerAgent(model=args.model, base_url=args.base_url)
    records = []
    all_missed: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        meta = {}
        for doc_id in doc_ids_with_entities:
            row = source_df.loc[doc_id]
            existing = existing_by_doc.get(doc_id, [])
            future = pool.submit(
                agent.review, doc_id, row["headline"],
                str(row["content"]), existing,
            )
            meta[future] = doc_id

        done = 0
        total = len(doc_ids_with_entities)
        for future in as_completed(meta):
            doc_id = meta[future]
            done += 1
            headline = (source_df.loc[doc_id, "headline"] if doc_id in source_df.index else "") or ""
            snippet = headline[:50]
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
                    print(f"[{done:>3}/{total}] none | {snippet}")
            except Exception as e:
                records.append(ReviewerFailureRecord(doc_id=doc_id, error=str(e)))
                print(f"[{done:>3}/{total}] err: {e} | {snippet}")

    all_missed, review_filtered = _filter_entity_rows(all_missed)
    missed_df = pd.DataFrame(all_missed, columns=REVIEWER_COLUMNS)
    missed_df.to_csv(f"{args.output}/reviewer_missed.csv", index=False, encoding="utf-8-sig")
    print(f"\nReviewer found {len(all_missed)} missed entities ({len(review_filtered)} filtered).")

    if args.merge:
        orig_rows = original_df.fillna("").to_dict("records")
        merged = _dedup_reviewer_entities(orig_rows, all_missed)
        merged = _merge_entity_rows(merged)
        merged_df = pd.DataFrame(merged, columns=ENTITY_COLUMNS)

        merged_df.to_csv(f"{args.output}/entities.csv", index=False, encoding="utf-8-sig")
        raw_lines = _build_raw_lines(all_doc_ids, merged)
        with open(f"{args.output}/entities.jsonl", "w", encoding="utf-8") as f:
            for line in raw_lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        gained = len(merged) - len(orig_rows)
        print(f"Merged: {len(orig_rows)} original + {gained} new = {len(merged)} total entities.")
        print(f"Overwrote {args.output}/entities.csv, entities.jsonl")


if __name__ == "__main__":
    main(parse_args())
