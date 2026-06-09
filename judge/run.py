"""Run judge evaluation on extraction results with retry."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from judge.agent import JudgeAgent

OUTPUT_FORMATS = {"jsonl", "xlsx"}


@dataclass
class JudgeFailureRecord:
    doc_id: str
    success: bool = False
    duration_seconds: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    entity_recall: float | None = None
    relevance_precision: float = 0
    error: str = ""


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entities", required=True, help="extraction results CSV path")
    parser.add_argument("--data", default="data/test_news_0603.csv", help="original data CSV path")
    parser.add_argument("--output", "-o", default="output", help="output directory")
    parser.add_argument(
        "--output-formats",
        default="jsonl",
        help="comma-separated output formats: jsonl,xlsx (summary.json is always written)",
    )
    parser.add_argument("--workers", "-w", type=int, default=4, help="parallel worker count")
    parser.add_argument("--model", default=None, help="model name")
    parser.add_argument("--base-url", default=None, help="API base URL")
    parser.add_argument(
        "--eval-jsonl",
        default=None,
        help="optional extraction JSONL path; defaults to entities.jsonl next to --entities",
    )
    return parser.parse_args()


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _shorten(value: str, limit: int = 120) -> str:
    value = value or ""
    return value[:limit] + ("..." if len(value) > limit else "")


def _parse_output_formats(value: str, allowed: set[str]) -> set[str]:
    formats = {item.strip().lower() for item in value.split(",") if item.strip()}
    invalid = formats - allowed
    if invalid:
        raise ValueError(f"unsupported output format(s): {', '.join(sorted(invalid))}")
    if not formats:
        raise ValueError("at least one output format is required")
    return formats


def _write_xlsx(df: pd.DataFrame, path: str, sheet_name: str):
    try:
        import openpyxl  # noqa: F401
    except ImportError as e:
        raise RuntimeError("XLSX output requires openpyxl. Install it with: uv pip install openpyxl") from e

    df.to_excel(path, index=False, sheet_name=sheet_name, engine="openpyxl")

    from openpyxl import load_workbook

    workbook = load_workbook(path)
    worksheet = workbook[sheet_name]
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    for column_cells in worksheet.columns:
        header = str(column_cells[0].value or "")
        max_len = max(len(str(cell.value or "")) for cell in column_cells[:200])
        column_width = min(max(max_len + 2, len(header) + 2), 60)
        worksheet.column_dimensions[column_cells[0].column_letter].width = column_width

    workbook.save(path)


def _load_doc_ids(entities_df: pd.DataFrame, entities_path: str, eval_jsonl_path: str | None) -> list[str]:
    jsonl_path = eval_jsonl_path or os.path.join(os.path.dirname(entities_path), "entities.jsonl")
    doc_ids = []

    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                doc_id = item.get("doc_id")
                if doc_id:
                    doc_ids.append(doc_id)

    if not doc_ids and not entities_df.empty:
        doc_ids = entities_df["doc_id"].tolist()

    return _dedupe_keep_order(doc_ids)


def main(args):
    os.makedirs(args.output, exist_ok=True)
    output_formats = _parse_output_formats(args.output_formats, OUTPUT_FORMATS)

    entities_df = pd.read_csv(args.entities)
    source_df = pd.read_csv(args.data).set_index("doc_id")

    record_columns = [
        "entity",
        "mapped_from",
        "entity_sentiment",
        "sentiment_reason",
    ]
    if entities_df.empty:
        entity_records_by_doc = {}
    else:
        entity_records_by_doc = {
            doc_id: group[record_columns].fillna("").to_dict("records")
            for doc_id, group in entities_df.groupby("doc_id")
        }

    all_doc_ids = _load_doc_ids(entities_df, args.entities, args.eval_jsonl)
    headlines = {
        doc_id: source_df.loc[doc_id, "headline"]
        for doc_id in all_doc_ids
        if doc_id in source_df.index
    }

    print(f"Judging {len(all_doc_ids)} articles...")

    agent = JudgeAgent(model=args.model, base_url=args.base_url)

    def run_pass(doc_ids: list[str], pass_label: str, pool_size: int):
        done = 0
        total = len(doc_ids)
        pass_results: list[dict] = []
        pass_records = []
        failed_ids: list[str] = []

        with ThreadPoolExecutor(max_workers=pool_size) as pool:
            meta = {}
            for doc_id in doc_ids:
                row = source_df.loc[doc_id]
                entity_records = entity_records_by_doc.get(doc_id, [])
                future = pool.submit(
                    agent.evaluate, doc_id, row["headline"],
                    str(row["content"]), entity_records,
                    len(entity_records)
                )
                meta[future] = doc_id

            for future in as_completed(meta):
                doc_id = meta[future]
                done += 1
                headline = headlines.get(doc_id, "")
                snippet = headline[:50] + ("..." if len(headline) > 50 else "")
                try:
                    result, record = future.result()
                    pass_records.append(record)
                    pass_results.append({
                        "doc_id": doc_id,
                        "entity_recall": record.entity_recall,
                        "relevance_precision": record.relevance_precision,
                        "missed": result.missed,
                        "extra": result.extra,
                    })
                    er = record.entity_recall
                    recall_str = f"recall={er:.3f}" if er is not None else "recall=off"
                    print(f"[{pass_label}] [{done:>3}/{total}] [{record.duration_seconds:5.1f}s] "
                          f"{recall_str} "
                          f"prec={record.relevance_precision:.2f} | {snippet}")
                    if record.relevance_precision < 1 or (er is not None and er < 1):
                        extra_names = [e.get("entity", "") for e in result.extra]
                        missed_names = [m.get("entity", "") for m in result.missed]
                        print(f"  [detail] doc_id={doc_id}")
                        print(f"  [detail] extra={json.dumps(result.extra, ensure_ascii=False)}")
                        print(f"  [detail] missed={json.dumps(result.missed, ensure_ascii=False)}")
                        for m in result.missed:
                            ent = m.get("entity", "")
                            reason = _shorten(m.get("reason", ""), 100)
                            print(f"  [detail] JUDGE MISSED | entity={ent} | judge_reason={reason}")
                        for e in result.extra:
                            ent = e.get("entity", "")
                            reason = _shorten(e.get("reason", ""), 100)
                            print(f"  [detail] JUDGE EXTRA  | entity={ent} | judge_reason={reason}")
                        for item in entity_records_by_doc.get(doc_id, []):
                            marker = "EXTRA" if item.get("entity") in extra_names else "KEEP "
                            print(
                                "  [detail] "
                                f"{marker} | entity={item.get('entity', '')} "
                                f"| mapped_from={item.get('mapped_from', '')} "
                                f"| sentiment={item.get('entity_sentiment', '')} "
                                f"| reason={_shorten(item.get('sentiment_reason', ''))}"
                            )
                except Exception as e:
                    pass_records.append(JudgeFailureRecord(doc_id=doc_id, error=str(e)))
                    failed_ids.append(doc_id)
                    print(f"[{pass_label}] [{done:>3}/{total}] [  err  ] {e} | {snippet}")

        return pass_results, pass_records, failed_ids

    t_start = time.time()

    all_results, records, failed = run_pass(all_doc_ids, "1st", args.workers)

    retry_count = 0
    if failed:
        print(f"\n--- Retrying {len(failed)} failed samples ---")
        retry_results, retry_records, still_failed = run_pass(failed, "retry", 1)
        all_results.extend(retry_results)
        records.extend(retry_records)
        retry_count = len(failed) - len(still_failed)

    wall_clock = time.time() - t_start

    if "jsonl" in output_formats:
        with open(f"{args.output}/judge_results.jsonl", "w", encoding="utf-8") as f:
            for r in all_results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if "xlsx" in output_formats:
        results_df = pd.DataFrame(all_results)
        for column in ["missed", "extra"]:
            if column in results_df:
                results_df[column] = results_df[column].apply(
                    lambda value: json.dumps(value, ensure_ascii=False)
                )
        _write_xlsx(results_df, f"{args.output}/judge_results.xlsx", "judge_results")

    successful = [r for r in records if r.success]
    rp_scores = [r.relevance_precision for r in successful]
    er_scores = [r.entity_recall for r in successful if r.entity_recall is not None]

    summary = {
        "config": {
            "entities_file": args.entities,
            "model": agent.model,
            "workers": args.workers,
            "output_formats": sorted(output_formats),
        },
        "wall_clock_seconds": round(wall_clock, 1),
        "recall_evaluation_enabled": True,
        "total_articles": len(all_doc_ids),
        "judged": len(successful),
        "failed": len(records) - len(successful),
        "retry_saved": retry_count,
        "avg_entity_recall": round(sum(er_scores) / len(er_scores), 3) if er_scores else None,
        "avg_relevance_precision": round(sum(rp_scores) / len(rp_scores), 3) if rp_scores else 0,
        "per_call": [
            {
                "doc_id": r.doc_id,
                "success": r.success,
                "duration_seconds": r.duration_seconds,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "entity_recall": r.entity_recall,
                "relevance_precision": r.relevance_precision,
                "error": getattr(r, "error", ""),
            }
            for r in records
        ],
    }

    with open(f"{args.output}/judge_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"Judged {summary['judged']}/{summary['total_articles']} articles "
          f"(retry saved {summary['retry_saved']}).")
    er = summary["avg_entity_recall"]
    print(f"Entity recall:          {er:.3f}" if er is not None else "Entity recall:          disabled")
    print(f"Relevance precision:    {summary['avg_relevance_precision']:.3f}")
    print(f"Output formats: {', '.join(summary['config']['output_formats'])}")
    print(f"Wall clock: {wall_clock:.1f}s")
    print(f"Output: {args.output}/")


if __name__ == "__main__":
    main(parse_args())
