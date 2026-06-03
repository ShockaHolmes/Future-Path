from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import uuid4


class IntakeQuestion(TypedDict):
    key: str
    prompt: str
    options: list[str]


QUESTIONS: list[IntakeQuestion] = [
    {
        "key": "housing_status",
        "prompt": "What is your current housing situation?",
        "options": ["stable", "temporary", "couch_surfing", "shelter", "at_risk"],
    },
    {
        "key": "employment_status",
        "prompt": "What best describes your current employment status?",
        "options": ["full_time", "part_time", "unemployed", "training", "seasonal"],
    },
    {
        "key": "education_status",
        "prompt": "What is your current education status?",
        "options": ["in_school", "diploma_or_ged", "no_diploma_or_ged", "postsecondary", "not_enrolled"],
    },
    {
        "key": "transportation_access",
        "prompt": "How reliable is your transportation right now?",
        "options": ["reliable", "limited", "none"],
    },
    {
        "key": "food_access",
        "prompt": "Do you have consistent access to food?",
        "options": ["yes", "sometimes", "no"],
    },
    {
        "key": "health_wellness_need",
        "prompt": "Do you currently need health or wellness support (physical, mental, or counseling)?",
        "options": ["yes", "no"],
    },
    {
        "key": "documents_status",
        "prompt": "Do you currently have access to your key documents?",
        "options": ["all", "some", "none"],
    },
    {
        "key": "support_system",
        "prompt": "How strong is your support system right now?",
        "options": ["strong", "limited", "none"],
    },
    {
        "key": "safety_concern",
        "prompt": "Do you have any immediate safety concerns?",
        "options": ["yes", "no"],
    },
    {
        "key": "primary_need",
        "prompt": "What is your primary need right now?",
        "options": [
            "housing",
            "employment",
            "education",
            "transportation",
            "food",
            "health_wellness",
            "documents",
            "support_system",
            "safety",
        ],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Future Path AI Assistant intake questionnaire.")
    parser.add_argument("--database", type=Path, default=Path("database/future_path.db"), help="SQLite database path.")
    profile_group = parser.add_mutually_exclusive_group(required=True)
    profile_group.add_argument(
        "--youth-id",
        help="Youth profile ID for this intake session (e.g., YP-0001).",
    )
    profile_group.add_argument(
        "--candidate-id",
        help="Candidate profile ID for pre-enrollment intake sessions.",
    )
    parser.add_argument("--session-id", help="Optional intake session ID. If omitted, one is auto-generated.")
    return parser.parse_args()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _create_intake_sessions_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_sessions (
            intake_session_id TEXT PRIMARY KEY,
            youth_id TEXT,
            candidate_profile_id TEXT,
            profile_type TEXT NOT NULL CHECK (profile_type IN ('youth', 'candidate')),
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            session_status TEXT NOT NULL CHECK (session_status IN ('in_progress', 'completed', 'abandoned')),
            assistant_version TEXT,
            channel TEXT,
            top_need_category TEXT,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            CHECK (
                (profile_type = 'youth' AND youth_id IS NOT NULL AND candidate_profile_id IS NULL)
                OR
                (profile_type = 'candidate' AND candidate_profile_id IS NOT NULL AND youth_id IS NULL)
            )
        )
        """
    )


def _create_intake_answers_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS intake_answers (
            intake_answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            intake_session_id TEXT NOT NULL,
            question_key TEXT NOT NULL,
            question_text TEXT,
            answer_value TEXT,
            answer_type TEXT,
            answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE CASCADE,
            UNIQUE (intake_session_id, question_key)
        )
        """
    )


def print_intake_notice() -> None:
    print(
        "\nPrivacy and safety notice:\n"
        "- This assistant uses synthetic or demo data and is a decision-support tool only.\n"
        "- It is not a replacement for emergency services, crisis support, or professional case management.\n"
        "- Do not enter SSNs, exact addresses, medical records, or other unnecessary sensitive details.\n"
        "- If you or someone else is in immediate danger, call local emergency services right away.\n"
        "- In the U.S., you can also call or text 988 for mental health crisis support.\n"
    )


def ensure_intake_tables(connection: sqlite3.Connection) -> None:
    intake_columns = _table_columns(connection, "intake_sessions")
    needs_migration = _table_exists(connection, "intake_sessions") and {
        "candidate_profile_id",
        "profile_type",
        "top_need_category",
    } - intake_columns

    if needs_migration:
        fk_enabled = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        connection.execute("PRAGMA foreign_keys = OFF")

        has_answers = _table_exists(connection, "intake_answers")
        if has_answers:
            connection.execute("ALTER TABLE intake_answers RENAME TO intake_answers_legacy")
        connection.execute("ALTER TABLE intake_sessions RENAME TO intake_sessions_legacy")

        _create_intake_sessions_table(connection)
        _create_intake_answers_table(connection)

        connection.execute(
            """
            INSERT INTO intake_sessions (
                intake_session_id,
                youth_id,
                candidate_profile_id,
                profile_type,
                started_at,
                completed_at,
                session_status,
                assistant_version,
                channel,
                top_need_category
            )
            SELECT
                intake_session_id,
                youth_id,
                NULL,
                'youth',
                started_at,
                completed_at,
                session_status,
                assistant_version,
                channel,
                NULL
            FROM intake_sessions_legacy
            """
        )
        connection.execute("DROP TABLE intake_sessions_legacy")

        if has_answers:
            connection.execute(
                """
                INSERT INTO intake_answers (
                    intake_answer_id,
                    intake_session_id,
                    question_key,
                    question_text,
                    answer_value,
                    answer_type,
                    answered_at
                )
                SELECT
                    intake_answer_id,
                    intake_session_id,
                    question_key,
                    question_text,
                    answer_value,
                    answer_type,
                    answered_at
                FROM intake_answers_legacy
                """
            )
            connection.execute("DROP TABLE intake_answers_legacy")

        connection.execute(f"PRAGMA foreign_keys = {'ON' if fk_enabled else 'OFF'}")
    else:
        _create_intake_sessions_table(connection)
        _create_intake_answers_table(connection)

    connection.execute("CREATE INDEX IF NOT EXISTS idx_intake_sessions_youth_id ON intake_sessions (youth_id)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_intake_sessions_candidate_profile_id ON intake_sessions (candidate_profile_id)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_intake_answers_session_id ON intake_answers (intake_session_id)")


def ensure_youth_exists(connection: sqlite3.Connection, youth_id: str) -> None:
    row = connection.execute("SELECT 1 FROM youth_profiles WHERE youth_id = ?", (youth_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown youth_id: {youth_id}. Load youth profiles before starting intake.")


def resolve_profile_link(
    connection: sqlite3.Connection,
    youth_id: str | None,
    candidate_profile_id: str | None,
) -> tuple[str, str | None, str | None]:
    normalized_youth_id = (youth_id or "").strip()
    normalized_candidate_id = (candidate_profile_id or "").strip()

    if bool(normalized_youth_id) == bool(normalized_candidate_id):
        raise ValueError("Provide exactly one of youth_id or candidate_profile_id")

    if normalized_youth_id:
        ensure_youth_exists(connection, normalized_youth_id)
        return "youth", normalized_youth_id, None
    return "candidate", None, normalized_candidate_id


def prompt_for_answer(index: int, total: int, question: IntakeQuestion) -> str:
    key = question["key"]
    prompt = question["prompt"]
    options = question["options"]

    print(f"\nQuestion {index}/{total}: {prompt}")
    print(f"Options: {', '.join(options)}")

    while True:
        value = input(f"{key}> ").strip().lower()
        if value in options:
            return value
        print("Invalid answer. Please enter one of the listed options.")


def print_emergency_warning() -> None:
    print(
        "\nEmergency support warning:\n"
        "- This assistant cannot provide crisis intervention or urgent safety response.\n"
        "- If there is immediate danger, contact emergency services now.\n"
        "- If this is a mental health crisis in the U.S., call or text 988.\n"
    )


def infer_summary_needs(answers: dict[str, str]) -> list[str]:
    needs: list[str] = []

    if answers.get("housing_status") in {"temporary", "couch_surfing", "shelter", "at_risk"}:
        needs.append("Housing stabilization")
    if answers.get("employment_status") == "unemployed":
        needs.append("Employment and job training")
    if answers.get("education_status") in {"no_diploma_or_ged", "not_enrolled"}:
        needs.append("Education / GED / tutoring")
    if answers.get("transportation_access") in {"limited", "none"}:
        needs.append("Transportation assistance")
    if answers.get("food_access") in {"sometimes", "no"}:
        needs.append("Food access support")
    if answers.get("health_wellness_need") == "yes":
        needs.append("Health and wellness / counseling")
    if answers.get("documents_status") in {"some", "none"}:
        needs.append("Document readiness support")
    if answers.get("support_system") in {"limited", "none"}:
        needs.append("Mentorship and support system")
    if answers.get("safety_concern") == "yes":
        needs.append("Immediate safety planning")

    primary_map = {
        "housing": "Housing stabilization",
        "employment": "Employment and job training",
        "education": "Education / GED / tutoring",
        "transportation": "Transportation assistance",
        "food": "Food access support",
        "health_wellness": "Health and wellness / counseling",
        "documents": "Document readiness support",
        "support_system": "Mentorship and support system",
        "safety": "Immediate safety planning",
    }
    primary_need = answers.get("primary_need")
    if primary_need in primary_map:
        needs.insert(0, primary_map[primary_need])

    deduped: list[str] = []
    for need in needs:
        if need not in deduped:
            deduped.append(need)
    return deduped


def save_answer(
    connection: sqlite3.Connection,
    session_id: str,
    question_key: str,
    question_text: str,
    answer_value: str,
) -> None:
    connection.execute(
        """
        INSERT INTO intake_answers (
            intake_session_id,
            question_key,
            question_text,
            answer_value,
            answer_type
        ) VALUES (?, ?, ?, ?, 'single_select')
        ON CONFLICT(intake_session_id, question_key) DO UPDATE SET
            question_text = excluded.question_text,
            answer_value = excluded.answer_value,
            answer_type = excluded.answer_type,
            answered_at = CURRENT_TIMESTAMP
        """,
        (session_id, question_key, question_text, answer_value),
    )


def run_intake(
    connection: sqlite3.Connection,
    youth_id: str | None,
    session_id: str,
    candidate_profile_id: str | None = None,
    answers: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    ensure_intake_tables(connection)
    profile_type, linked_youth_id, linked_candidate_id = resolve_profile_link(connection, youth_id, candidate_profile_id)

    connection.execute(
        """
        INSERT INTO intake_sessions (
            intake_session_id,
            youth_id,
            candidate_profile_id,
            profile_type,
            session_status,
            assistant_version,
            channel,
            top_need_category
        ) VALUES (?, ?, ?, ?, 'in_progress', 'future_path_ai_assistant_v1', 'cli', NULL)
        ON CONFLICT(intake_session_id) DO UPDATE SET
            youth_id = excluded.youth_id,
            candidate_profile_id = excluded.candidate_profile_id,
            profile_type = excluded.profile_type,
            session_status = 'in_progress',
            assistant_version = excluded.assistant_version,
            channel = excluded.channel,
            top_need_category = excluded.top_need_category
        """,
        (session_id, linked_youth_id, linked_candidate_id, profile_type),
    )

    collected: dict[str, str] = {}
    total = len(QUESTIONS)
    for idx, question in enumerate(QUESTIONS, start=1):
        key = question["key"]
        prompt = question["prompt"]
        if answers is None:
            value = prompt_for_answer(idx, total, question)
        else:
            value = str(answers[key]).strip().lower()
            if value not in set(question["options"]):
                raise ValueError(f"Invalid test answer for {key}: {value}")
        collected[key] = value
        save_answer(connection, session_id, key, prompt, value)

        if key == "safety_concern" and value == "yes":
            print_emergency_warning()

    summary_needs = infer_summary_needs(collected)
    top_need_category = collected.get("primary_need") or "general_support"
    connection.execute(
        """
        UPDATE intake_sessions
        SET session_status = 'completed',
            completed_at = ?,
            top_need_category = ?
        WHERE intake_session_id = ?
        """,
        (datetime.now(UTC).isoformat(timespec="seconds"), top_need_category, session_id),
    )

    return collected, summary_needs


def main() -> None:
    args = parse_args()
    session_id = args.session_id or f"intake-{uuid4()}"

    try:
        if not args.database.exists():
            raise FileNotFoundError(f"Database not found: {args.database}")

        print_intake_notice()
        with sqlite3.connect(args.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            answers, summary_needs = run_intake(
                connection,
                youth_id=args.youth_id,
                candidate_profile_id=args.candidate_id,
                session_id=session_id,
            )
            connection.commit()

        print("\nIntake completed successfully.")
        print(f"Session ID: {session_id}")
        print("\nSummary of needs:")
        if summary_needs:
            for need in summary_needs:
                print(f"- {need}")
        else:
            print("- General support")
        print("\nCaptured answers:")
        for question in QUESTIONS:
            key = question["key"]
            print(f"- {key}: {answers[key]}")

    except KeyboardInterrupt:
        with sqlite3.connect(args.database) as connection:
            connection.execute(
                """
                UPDATE intake_sessions
                SET session_status = 'abandoned',
                    completed_at = ?
                WHERE intake_session_id = ?
                """,
                (datetime.now(UTC).isoformat(timespec="seconds"), session_id),
            )
            connection.commit()
        raise SystemExit("\nIntake stopped by user. Session marked as abandoned.")
    except (sqlite3.Error, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"Intake failed: {error}") from error


if __name__ == "__main__":
    main()