from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from clean_synthetic_youth_data import load_data, standardize_columns, write_data


STRING_COLUMNS = [
    "resource_id",
    "resource_name",
    "category",
    "need_tags",
    "service_area",
    "county",
    "city",
    "state",
    "description",
    "referral_method",
    "contact_phone",
    "website",
    "ai_match_rules",
    "default_priority",
    "caseworker_notes",
]

AGE_MIN_COLUMN = "eligibility_age_min"
AGE_MAX_COLUMN = "eligibility_age_max"
DEFAULT_PRIORITY = "Medium"
DEFAULT_STATE = "DE"
MAX_REASONABLE_AGE = 99


def remove_duplicates(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop_duplicates(subset=["resource_id"], keep="first")


def handle_missing_values(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = frame.copy()

    for column in STRING_COLUMNS:
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].fillna("")
            cleaned[column] = cleaned[column].astype(str).str.strip()

    if "state" in cleaned.columns:
        cleaned.loc[cleaned["state"] == "", "state"] = DEFAULT_STATE

    if "default_priority" in cleaned.columns:
        cleaned.loc[cleaned["default_priority"] == "", "default_priority"] = DEFAULT_PRIORITY

    if "contact_phone" in cleaned.columns:
        cleaned.loc[cleaned["contact_phone"] == "", "contact_phone"] = "Not listed"

    for column in [AGE_MIN_COLUMN, AGE_MAX_COLUMN]:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    cleaned[AGE_MIN_COLUMN] = cleaned[AGE_MIN_COLUMN].fillna(0).astype(int)
    cleaned[AGE_MAX_COLUMN] = cleaned[AGE_MAX_COLUMN].fillna(MAX_REASONABLE_AGE).astype(int)

    return cleaned


def validate_rows(frame: pd.DataFrame) -> pd.DataFrame:
    valid_ids = frame["resource_id"].ne("")
    valid_names = frame["resource_name"].ne("")
    valid_ages = (
        frame[AGE_MIN_COLUMN].between(0, MAX_REASONABLE_AGE)
        & frame[AGE_MAX_COLUMN].between(0, MAX_REASONABLE_AGE)
        & frame[AGE_MIN_COLUMN].le(frame[AGE_MAX_COLUMN])
    )
    return frame.loc[valid_ids & valid_names & valid_ages].copy()


def clean_data(frame: pd.DataFrame) -> pd.DataFrame:
    cleaned = standardize_columns(frame)
    cleaned = remove_duplicates(cleaned)
    cleaned = handle_missing_values(cleaned)
    cleaned = validate_rows(cleaned)
    return cleaned.sort_values(["county", "resource_id"]).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean Delaware youth resource catalog data.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw/future_path_delaware_youth_resources.csv"),
        help="Path to the raw youth resource catalog CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/clean/future_path_delaware_youth_resources_clean.csv"),
        help="Path to the cleaned youth resource catalog CSV.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = load_data(args.input)
    cleaned = clean_data(frame)
    write_data(cleaned, args.output)
    print(f"Loaded {len(frame)} raw resource rows from {args.input}")
    print(f"Saved {len(cleaned)} cleaned resource rows to {args.output}")


if __name__ == "__main__":
    main()