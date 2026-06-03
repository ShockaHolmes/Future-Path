from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from assign_resources_from_intake import ensure_assigned_resources_table_integrity
from calculate_risk_scores import ensure_risk_scores_table
from future_path_ai_intake import ensure_intake_tables
from generate_recommendations import ensure_recommendations_table_integrity


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Future Path SQLite schema to the latest version without losing data."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Path to SQLite database.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path("database/relational_schema.sql"),
        help="Optional schema file path (kept for compatibility; targeted migrations are used by default).",
    )
    return parser.parse_args()


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    if not table_exists(connection, table_name):
        return []
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [str(row[1]) for row in rows]


def migrate_database(connection: sqlite3.Connection) -> dict[str, str]:
    ensure_intake_tables(connection)
    ensure_assigned_resources_table_integrity(connection)
    ensure_risk_scores_table(connection)
    if table_exists(connection, "recommendations"):
        ensure_recommendations_table_integrity(connection)

    intake_columns = table_columns(connection, "intake_sessions")
    assigned_columns = table_columns(connection, "assigned_resources")

    return {
        "intake_sessions": ", ".join(intake_columns),
        "assigned_resources": ", ".join(assigned_columns),
    }


def main() -> None:
    args = parse_args()
    if not args.database.exists():
        raise SystemExit(f"Migration failed: Database not found: {args.database}")

    try:
        with sqlite3.connect(args.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            summary = migrate_database(connection)
            connection.commit()

        print(f"Migration completed: {args.database}")
        print("intake_sessions columns:")
        print(summary["intake_sessions"])
        print("assigned_resources columns:")
        print(summary["assigned_resources"])

    except (sqlite3.Error, FileNotFoundError, ValueError) as error:
        raise SystemExit(f"Migration failed: {error}") from error


if __name__ == "__main__":
    main()
