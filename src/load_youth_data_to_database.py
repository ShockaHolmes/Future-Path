from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd


PUBLIC_TABLE = "youth_transition"
CASEWORKER_TABLE = "caseworker_youth"
RESOURCE_TABLE = "youth_resources"

EXPECTED_AGE_MIN = 13
EXPECTED_AGE_MAX = 19
REQUIRED_PUBLIC_COLUMNS = {
    "youth_id",
    "age",
    "county",
    "education",
    "employment",
    "housing",
    "mentor_status",
    "placement_count",
    "prior_homelessness",
}
ALLOWED_HOUSING_STATUSES = {
    "Stable housing",
    "Couch surfing",
    "Temporary shelter",
    "Transitional housing",
    "At risk of homelessness",
    "Unknown",
}
ALLOWED_EMPLOYMENT_STATUSES = {
    "Unemployed",
    "Part-time",
    "Full-time",
    "Seasonal",
    "Training / internship",
    "Unknown",
}


def load_csv(input_path: Path) -> pd.DataFrame:
    return pd.read_csv(input_path)


def prepare_frame_for_sql(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    object_columns = prepared.select_dtypes(include=["object"]).columns
    if len(object_columns) > 0:
        prepared[object_columns] = prepared[object_columns].fillna("")
    return prepared


def validate_relationships(public_frame: pd.DataFrame, caseworker_frame: pd.DataFrame) -> None:
    public_ids = set(public_frame["youth_id"])
    caseworker_ids = set(caseworker_frame["youth_id"])

    missing_from_caseworker = public_ids - caseworker_ids
    missing_from_public = caseworker_ids - public_ids
    if missing_from_caseworker or missing_from_public:
        raise ValueError(
            "Public and caseworker datasets must contain the same youth_id values "
            f"(missing_from_caseworker={len(missing_from_caseworker)}, "
            f"missing_from_public={len(missing_from_public)})"
        )


def _print_validation_result(name: str, passed: bool, details: str) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}: {details}")


def validate_public_data_for_load(public_frame: pd.DataFrame) -> None:
    failures: list[str] = []

    missing_columns = sorted(REQUIRED_PUBLIC_COLUMNS - set(public_frame.columns))
    missing_columns_check = len(missing_columns) == 0
    _print_validation_result(
        "required_columns",
        missing_columns_check,
        "all required columns present" if missing_columns_check else f"missing columns: {missing_columns}",
    )
    if not missing_columns_check:
        failures.append(f"missing required columns: {missing_columns}")
        raise ValueError("Validation failed. Cannot continue to database load: " + "; ".join(failures))

    youth_id_series = public_frame["youth_id"].fillna("").astype(str).str.strip()
    youth_id_missing_count = int((youth_id_series == "").sum())
    youth_id_check = youth_id_missing_count == 0
    _print_validation_result(
        "youth_id_not_missing",
        youth_id_check,
        "all rows include youth_id" if youth_id_check else f"rows with missing youth_id: {youth_id_missing_count}",
    )
    if not youth_id_check:
        failures.append(f"rows with missing youth_id: {youth_id_missing_count}")

    age_numeric = pd.to_numeric(public_frame["age"], errors="coerce")
    age_valid_mask = age_numeric.between(EXPECTED_AGE_MIN, EXPECTED_AGE_MAX)
    age_invalid_count = int((~age_valid_mask).sum())
    age_check = age_invalid_count == 0
    _print_validation_result(
        "age_range",
        age_check,
        (
            f"all ages are between {EXPECTED_AGE_MIN} and {EXPECTED_AGE_MAX}"
            if age_check
            else f"rows with out-of-range or invalid age: {age_invalid_count}"
        ),
    )
    if not age_check:
        failures.append(f"rows with out-of-range/invalid age: {age_invalid_count}")

    housing_series = public_frame["housing"].fillna("").astype(str).str.strip()
    invalid_housing_values = sorted(set(housing_series) - ALLOWED_HOUSING_STATUSES)
    housing_check = len(invalid_housing_values) == 0
    _print_validation_result(
        "housing_values",
        housing_check,
        "all housing values are allowed"
        if housing_check
        else f"unexpected housing values: {invalid_housing_values}",
    )
    if not housing_check:
        failures.append(f"unexpected housing values: {invalid_housing_values}")

    employment_series = public_frame["employment"].fillna("").astype(str).str.strip()
    invalid_employment_values = sorted(set(employment_series) - ALLOWED_EMPLOYMENT_STATUSES)
    employment_check = len(invalid_employment_values) == 0
    _print_validation_result(
        "employment_values",
        employment_check,
        "all employment values are allowed"
        if employment_check
        else f"unexpected employment values: {invalid_employment_values}",
    )
    if not employment_check:
        failures.append(f"unexpected employment values: {invalid_employment_values}")

    if failures:
        raise ValueError("Validation failed. Cannot continue to database load: " + "; ".join(failures))

    print("Validation completed successfully. Data is usable for database load.")


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"DROP TABLE IF EXISTS {RESOURCE_TABLE}")
    connection.execute(f"DROP TABLE IF EXISTS {CASEWORKER_TABLE}")
    connection.execute(f"DROP TABLE IF EXISTS {PUBLIC_TABLE}")
    connection.execute(
        f"""
        CREATE TABLE {PUBLIC_TABLE} (
            youth_id TEXT PRIMARY KEY,
            age INTEGER NOT NULL,
            county TEXT NOT NULL,
            education TEXT NOT NULL,
            employment TEXT NOT NULL,
            housing TEXT NOT NULL,
            mentor_status TEXT NOT NULL,
            placement_count INTEGER NOT NULL,
            prior_homelessness TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE {CASEWORKER_TABLE} (
            youth_id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            FOREIGN KEY (youth_id) REFERENCES {PUBLIC_TABLE}(youth_id)
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE {RESOURCE_TABLE} (
            resource_id TEXT PRIMARY KEY,
            resource_name TEXT NOT NULL,
            category TEXT NOT NULL,
            need_tags TEXT NOT NULL,
            service_area TEXT NOT NULL,
            county TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            eligibility_age_min INTEGER NOT NULL,
            eligibility_age_max INTEGER NOT NULL,
            description TEXT NOT NULL,
            referral_method TEXT NOT NULL,
            contact_phone TEXT NOT NULL,
            contact_email TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL,
            ai_match_rules TEXT NOT NULL,
            default_priority TEXT NOT NULL,
            caseworker_notes TEXT NOT NULL
        )
        """
    )


def write_tables(
    public_frame: pd.DataFrame,
    caseworker_frame: pd.DataFrame,
    resource_frame: pd.DataFrame,
    database_path: Path,
) -> None:
    public_frame = prepare_frame_for_sql(public_frame)
    caseworker_frame = prepare_frame_for_sql(caseworker_frame)
    resource_frame = prepare_frame_for_sql(resource_frame)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        initialize_schema(connection)
        public_frame.to_sql(PUBLIC_TABLE, connection, if_exists="append", index=False)
        caseworker_frame.to_sql(CASEWORKER_TABLE, connection, if_exists="append", index=False)
        resource_frame.to_sql(RESOURCE_TABLE, connection, if_exists="append", index=False)
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{PUBLIC_TABLE}_county ON {PUBLIC_TABLE}(county)"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{CASEWORKER_TABLE}_last_name ON {CASEWORKER_TABLE}(last_name)"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{RESOURCE_TABLE}_county ON {RESOURCE_TABLE}(county)"
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{RESOURCE_TABLE}_category ON {RESOURCE_TABLE}(category)"
        )
        connection.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load cleaned youth CSV data into a SQLite database.")
    parser.add_argument(
        "--public-input",
        type=Path,
        default=Path("data/clean/synthetic_youth_transition_data_clean.csv"),
        help="Path to the cleaned public youth CSV.",
    )
    parser.add_argument(
        "--caseworker-input",
        type=Path,
        default=Path("data/clean/synthetic_youth_caseworker_data_clean.csv"),
        help="Path to the cleaned caseworker youth CSV.",
    )
    parser.add_argument(
        "--resource-input",
        type=Path,
        default=Path("data/clean/future_path_delaware_youth_resources_clean.csv"),
        help="Path to the cleaned youth resource catalog CSV.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Destination SQLite database path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    public_frame = load_csv(args.public_input)
    caseworker_frame = load_csv(args.caseworker_input)
    resource_frame = load_csv(args.resource_input)
    validate_public_data_for_load(public_frame)
    validate_relationships(public_frame, caseworker_frame)
    write_tables(public_frame, caseworker_frame, resource_frame, args.database)
    print(f"Loaded {len(public_frame)} public youth rows into {args.database}:{PUBLIC_TABLE}")
    print(f"Loaded {len(caseworker_frame)} caseworker youth rows into {args.database}:{CASEWORKER_TABLE}")
    print(f"Loaded {len(resource_frame)} youth resource rows into {args.database}:{RESOURCE_TABLE}")


if __name__ == "__main__":
    main()