from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


EXPECTED_AGE_MIN = 13
EXPECTED_AGE_MAX = 19
STRING_COLUMNS = [
    "youth_id",
    "county",
    "education",
    "employment",
    "housing",
    "mentor_status",
    "prior_homelessness",
]
NUMERIC_COLUMNS = ["age", "placement_count"]


def standardize_column_name(column_name: str) -> str:
    normalized = column_name.strip().lower().replace("/", "_")
    normalized = "_".join(normalized.split())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def load_data(input_path: Path) -> pd.DataFrame:
    return pd.read_csv(input_path)


def standardize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    standardized = frame.copy()
    standardized.columns = [standardize_column_name(column) for column in standardized.columns]
    return standardized


def remove_duplicates(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates(subset=["youth_id"], keep="first")


def handle_missing_values(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()

    for column in STRING_COLUMNS:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].fillna("Unknown")

    for column in NUMERIC_COLUMNS:
        if column in cleaned.columns:
            cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    if "age" in cleaned.columns:
        cleaned["age"] = cleaned["age"].fillna(EXPECTED_AGE_MIN).astype(int)

    if "placement_count" in cleaned.columns:
        cleaned["placement_count"] = cleaned["placement_count"].fillna(0).astype(int)

    return cleaned


def validate_age_range(frame: pd.DataFrame) -> pd.DataFrame:
    valid_age_mask = frame["age"].between(EXPECTED_AGE_MIN, EXPECTED_AGE_MAX)
    return frame.loc[valid_age_mask].copy()


def clean_data(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = standardize_columns(frame)
    cleaned = remove_duplicates(cleaned)
    cleaned = handle_missing_values(cleaned)
    cleaned = validate_age_range(cleaned)
    return cleaned.sort_values("youth_id").reset_index(drop=True)


def write_data(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean synthetic youth data for analysis and database loading.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/synthetic_youth_transition_data.csv"),
        help="Path to the raw synthetic youth CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clean/synthetic_youth_transition_data_clean.csv"),
        help="Path to the cleaned synthetic youth CSV.",
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