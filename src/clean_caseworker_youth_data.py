from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from clean_synthetic_youth_data import load_data, remove_duplicates, standardize_columns, write_data


STRING_COLUMNS = ["youth_id", "first_name", "last_name"]


def handle_missing_values(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()

    for column in STRING_COLUMNS:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].fillna("Unknown")
            cleaned[column] = cleaned[column].astype(str).str.strip()
            cleaned.loc[cleaned[column] == "", column] = "Unknown"

    return cleaned


def validate_ids(frame: pd.DataFrame) -> pd.DataFrame:
    valid_id_mask = frame["youth_id"].ne("Unknown")
    return frame.loc[valid_id_mask].copy()


def clean_data(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = standardize_columns(frame)
    cleaned = remove_duplicates(cleaned)
    cleaned = handle_missing_values(cleaned)
    cleaned = validate_ids(cleaned)
    return cleaned.sort_values("youth_id").reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean synthetic caseworker youth data for secure operational use.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/synthetic_youth_caseworker_data.csv"),
        help="Path to the raw synthetic caseworker CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clean/synthetic_youth_caseworker_data_clean.csv"),
        help="Path to the cleaned synthetic caseworker CSV.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = load_data(args.input)
    cleaned = clean_data(frame)
    write_data(cleaned, args.output)
    print(f"Loaded {len(frame)} raw rows from {args.input}")
    print(f"Saved {len(cleaned)} cleaned rows to {args.output}")


if __name__ == "__main__":
    main()