"""Print selected columns from a CSV/XLSX file for manual inspection."""

import argparse
from pathlib import Path

import pandas as pd


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print selected CSV/XLSX columns as copy-friendly text."
    )
    parser.add_argument("file", help="input .csv, .xls, or .xlsx file")
    parser.add_argument(
        "-c",
        "--columns",
        nargs="+",
        required=True,
        help="columns to print; accepts space-separated names or comma-separated groups",
    )
    parser.add_argument("--sheet", default=0, help="Excel sheet name or index, default: 0")
    parser.add_argument("--sep", default="\t", help="output separator, default: tab")
    parser.add_argument("--header", action="store_true", help="print column names as the first row")
    parser.add_argument("--no-dedupe", action="store_true", help="keep duplicate rows")
    parser.add_argument("--keep-empty", action="store_true", help="keep rows where all selected columns are empty")
    parser.add_argument("--limit", type=int, default=None, help="maximum rows to print")
    return parser.parse_args()


def normalize_columns(raw_columns: list[str]) -> list[str]:
    columns = []
    for item in raw_columns:
        columns.extend(part.strip() for part in item.split(","))
    return [column for column in columns if column]


def read_table(path: Path, sheet: str):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".xls", ".xlsx"}:
        sheet_name = int(sheet) if str(sheet).isdigit() else sheet
        return pd.read_excel(path, sheet_name=sheet_name)
    raise ValueError(f"unsupported file type: {suffix}")


def stringify(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def main(args):
    path = Path(args.file)
    columns = normalize_columns(args.columns)
    if not columns:
        raise ValueError("at least one column is required")

    df = read_table(path, args.sheet)
    missing = [column for column in columns if column not in df.columns]
    if missing:
        available = ", ".join(str(column) for column in df.columns)
        raise ValueError(f"missing column(s): {', '.join(missing)}\navailable columns: {available}")

    selected = df[columns].fillna("")
    if not args.keep_empty:
        selected = selected[selected.astype(str).agg("".join, axis=1).str.strip() != ""]
    if not args.no_dedupe:
        selected = selected.drop_duplicates()
    if args.limit is not None:
        selected = selected.head(args.limit)

    if args.header:
        print(args.sep.join(columns))

    for _, row in selected.iterrows():
        print(args.sep.join(stringify(row[column]) for column in columns))


if __name__ == "__main__":
    main(parse_args())
