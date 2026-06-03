from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = [
    "youth_id",
    "age",
    "county",
    "education",
    "employment",
    "housing",
    "mentor_status",
    "placement_count",
    "prior_homelessness",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load cleaned youth records into SQLite youth_profiles table."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/clean/synthetic_youth_transition_data_clean.csv"),
        help="Path to cleaned youth CSV.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Destination SQLite database path.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("database/relational_schema.sql"),
        help="Path to SQL schema used when the database is created.",
    )
    return parser.parse_args()


def ensure_required_columns(frame: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns in cleaned CSV: {missing}")


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def run_schema_if_needed(connection: sqlite3.Connection, schema_path: Path, database_was_created: bool) -> bool:
    needs_schema = database_was_created or not _table_exists(connection, "youth_profiles")
    if not needs_schema:
        return False
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    schema_sql = schema_path.read_text(encoding="utf-8")
    connection.executescript(schema_sql)
    return True


def load_youth_profiles(frame: pd.DataFrame, connection: sqlite3.Connection) -> int:
    statement = """
        INSERT INTO youth_profiles (
            youth_id,
            age,
            county,
            education,
            employment,
            housing,
            mentor_status,
            placement_count,
            prior_homelessness
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(youth_id) DO UPDATE SET
            age = excluded.age,
            county = excluded.county,
            education = excluded.education,
            employment = excluded.employment,
            housing = excluded.housing,
            mentor_status = excluded.mentor_status,
            placement_count = excluded.placement_count,
            prior_homelessness = excluded.prior_homelessness,
            updated_at = CURRENT_TIMESTAMP
    """

    normalized = frame.copy()
    normalized["youth_id"] = normalized["youth_id"].astype(str).str.strip()
    normalized["county"] = normalized["county"].astype(str).str.strip()
    normalized["education"] = normalized["education"].astype(str).str.strip()
    normalized["employment"] = normalized["employment"].astype(str).str.strip()
    normalized["housing"] = normalized["housing"].astype(str).str.strip()
    normalized["mentor_status"] = normalized["mentor_status"].astype(str).str.strip()
    normalized["prior_homelessness"] = normalized["prior_homelessness"].astype(str).str.strip()
    normalized["age"] = pd.to_numeric(normalized["age"], errors="raise").astype(int)
    normalized["placement_count"] = pd.to_numeric(normalized["placement_count"], errors="raise").astype(int)

    rows = [
        (
            str(normalized.at[i, "youth_id"]),
            int(normalized.at[i, "age"]),
            str(normalized.at[i, "county"]),
            str(normalized.at[i, "education"]),
            str(normalized.at[i, "employment"]),
            str(normalized.at[i, "housing"]),
            str(normalized.at[i, "mentor_status"]),
            int(normalized.at[i, "placement_count"]),
            str(normalized.at[i, "prior_homelessness"]),
        )
        for i in normalized.index
    ]
    connection.executemany(statement, rows)
    return len(rows)


def get_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    result = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(result[0]) if result else 0


def main() -> None:
    args = parse_args()
    database_was_created = not args.database.exists()

    try:
        if not args.input.exists():
            raise FileNotFoundError(f"Cleaned CSV not found: {args.input}")

        frame = pd.read_csv(args.input)
        ensure_required_columns(frame)
        frame = frame[REQUIRED_COLUMNS].copy()

        args.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(args.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            schema_applied = run_schema_if_needed(connection, args.schema, database_was_created)
            loaded_rows = load_youth_profiles(frame, connection)
            table_count = get_row_count(connection, "youth_profiles")
            connection.commit()

        print(f"Connected to SQLite database: {args.database}")
        if schema_applied:
            print(f"Applied schema from: {args.schema}")
        print(f"Loaded rows from CSV: {loaded_rows}")
        print(f"Current youth_profiles row count: {table_count}")
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        raise SystemExit(f"ETL load failed: {error}") from error


if __name__ == "__main__":
    main()