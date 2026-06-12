#!/usr/bin/env python3
"""Utility to clear intake data for a youth, allowing them to restart their intake."""

import argparse
import sqlite3
from pathlib import Path


def clear_youth_intake(database_path: Path, youth_id: str, dry_run: bool = False) -> None:
    """
    Clear all intake sessions and answers for a youth.
    
    Args:
        database_path: Path to the SQLite database
        youth_id: The youth ID to clear intake for (e.g., 'YP-0001')
        dry_run: If True, show what would be deleted without actually deleting
    """
    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")
    
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        
        # Check if tables exist
        tables_exist = all(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()
            for table in ["intake_sessions", "intake_answers"]
        )
        
        if not tables_exist:
            print("⚠️  Intake tables do not exist yet.")
            return
        
        # Find intake sessions for this youth
        sessions = connection.execute(
            """
            SELECT intake_session_id, session_status, completed_at
            FROM intake_sessions
            WHERE youth_id = ?
            ORDER BY completed_at DESC NULLS LAST
            """,
            (youth_id,)
        ).fetchall()
        
        if not sessions:
            print(f"✓ No intake sessions found for {youth_id}")
            return
        
        print(f"\nFound {len(sessions)} intake session(s) for {youth_id}:")
        for session in sessions:
            print(f"  - {session['intake_session_id']} (status: {session['session_status']})")
        
        if dry_run:
            print("\n[DRY RUN] The following would be deleted:")
            print(f"  - {len(sessions)} intake session(s)")
            answers_count = connection.execute(
                """
                SELECT COUNT(*) as count FROM intake_answers
                WHERE intake_session_id IN (
                    SELECT intake_session_id FROM intake_sessions WHERE youth_id = ?
                )
                """,
                (youth_id,)
            ).fetchone()["count"]
            print(f"  - {answers_count} intake answer(s)")
            return
        
        # Delete intake answers first (due to foreign key)
        connection.execute(
            """
            DELETE FROM intake_answers
            WHERE intake_session_id IN (
                SELECT intake_session_id FROM intake_sessions WHERE youth_id = ?
            )
            """,
            (youth_id,)
        )
        
        # Delete intake sessions
        connection.execute(
            "DELETE FROM intake_sessions WHERE youth_id = ?",
            (youth_id,)
        )
        
        connection.commit()
        print(f"\n✓ Cleared all intake data for {youth_id}")
        print(f"  The youth can now restart their intake from scratch.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clear intake data for a youth so they can restart their intake."
    )
    parser.add_argument(
        "youth_id",
        help="Youth ID to clear intake for (e.g., YP-0001)"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Path to SQLite database (default: database/future_path.db)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    
    args = parser.parse_args()
    
    try:
        clear_youth_intake(args.database, args.youth_id, args.dry_run)
    except FileNotFoundError as e:
        print(f"✗ Error: {e}")
        exit(1)


if __name__ == "__main__":
    main()
