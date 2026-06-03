from __future__ import annotations

import sqlite3
from contextlib import redirect_stdout
from io import StringIO

from future_path_ai_intake import QUESTIONS, print_intake_notice, run_intake, save_answer


def _create_youth_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE youth_profiles (
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
        """
        INSERT INTO youth_profiles (
            youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
        ) VALUES ('YP-0001', 18, 'Kent', 'Not enrolled', 'Unemployed', 'Couch surfing', 'Not assigned', 6, 'Yes')
        """
    )


def test_intake_has_expected_question_count() -> None:
    assert 7 <= len(QUESTIONS) <= 10
    keys = [question["key"] for question in QUESTIONS]
    assert len(keys) == len(set(keys))


def test_run_intake_saves_answers_and_returns_summary() -> None:
    answers = {
        "housing_status": "couch_surfing",
        "employment_status": "unemployed",
        "education_status": "no_diploma_or_ged",
        "transportation_access": "limited",
        "food_access": "no",
        "health_wellness_need": "yes",
        "documents_status": "none",
        "support_system": "none",
        "safety_concern": "yes",
        "primary_need": "housing",
    }

    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_youth_table(connection)

        collected, summary = run_intake(
            connection,
            youth_id="YP-0001",
            session_id="intake-test-001",
            answers=answers,
        )
        connection.commit()

        sessions_count = connection.execute("SELECT COUNT(*) FROM intake_sessions").fetchone()[0]
        answers_count = connection.execute("SELECT COUNT(*) FROM intake_answers").fetchone()[0]
        completed_status = connection.execute(
            """
            SELECT session_status, top_need_category, completed_at
            FROM intake_sessions
            WHERE intake_session_id = 'intake-test-001'
            """
        ).fetchone()[0]
        top_need = connection.execute(
            "SELECT top_need_category FROM intake_sessions WHERE intake_session_id = 'intake-test-001'"
        ).fetchone()[0]
        completed_at = connection.execute(
            "SELECT completed_at FROM intake_sessions WHERE intake_session_id = 'intake-test-001'"
        ).fetchone()[0]
        saved_answer = connection.execute(
            """
            SELECT question_text, answer_value
            FROM intake_answers
            WHERE intake_session_id = 'intake-test-001' AND question_key = 'primary_need'
            """
        ).fetchone()

    assert sessions_count == 1
    assert answers_count == len(QUESTIONS)
    assert completed_status == "completed"
    assert top_need == "housing"
    assert completed_at is not None
    assert saved_answer[0] == "What is your primary need right now?"
    assert saved_answer[1] == "housing"
    assert collected["primary_need"] == "housing"
    assert "Housing stabilization" in summary
    assert "Employment and job training" in summary
    assert "Education / GED / tutoring" in summary
    assert "Health and wellness / counseling" in summary


def test_privacy_notice_and_emergency_warning_are_printed() -> None:
    answers = {
        "housing_status": "stable",
        "employment_status": "part_time",
        "education_status": "in_school",
        "transportation_access": "reliable",
        "food_access": "yes",
        "health_wellness_need": "no",
        "documents_status": "all",
        "support_system": "strong",
        "safety_concern": "yes",
        "primary_need": "safety",
    }

    buffer = StringIO()
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_youth_table(connection)

        with redirect_stdout(buffer):
            print_intake_notice()
            run_intake(
                connection,
                youth_id="YP-0001",
                session_id="intake-test-privacy-001",
                answers=answers,
            )

    output = buffer.getvalue()
    assert "decision-support tool only" in output
    assert "not a replacement for emergency services" in output
    assert "Do not enter SSNs" in output
    assert "Emergency support warning" in output
    assert "call or text 988" in output


def test_run_intake_supports_candidate_profile_linking() -> None:
    answers = {
        "housing_status": "temporary",
        "employment_status": "part_time",
        "education_status": "in_school",
        "transportation_access": "reliable",
        "food_access": "yes",
        "health_wellness_need": "no",
        "documents_status": "all",
        "support_system": "strong",
        "safety_concern": "no",
        "primary_need": "employment",
    }

    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_youth_table(connection)

        run_intake(
            connection,
            youth_id=None,
            candidate_profile_id="CP-9001",
            session_id="intake-test-candidate-001",
            answers=answers,
        )
        connection.commit()

        row = connection.execute(
            """
            SELECT profile_type, youth_id, candidate_profile_id, top_need_category
            FROM intake_sessions
            WHERE intake_session_id = 'intake-test-candidate-001'
            """
        ).fetchone()

    assert row[0] == "candidate"
    assert row[1] is None
    assert row[2] == "CP-9001"
    assert row[3] == "employment"


def test_save_answer_upserts_database_row() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            """
            CREATE TABLE intake_answers (
                intake_answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                intake_session_id TEXT NOT NULL,
                question_key TEXT NOT NULL,
                question_text TEXT,
                answer_value TEXT,
                answer_type TEXT,
                answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (intake_session_id, question_key)
            )
            """
        )

        save_answer(
            connection,
            session_id="intake-test-upsert-001",
            question_key="primary_need",
            question_text="What is your primary need right now?",
            answer_value="housing",
        )
        save_answer(
            connection,
            session_id="intake-test-upsert-001",
            question_key="primary_need",
            question_text="What is your primary need right now?",
            answer_value="employment",
        )
        connection.commit()

        row_count = connection.execute(
            "SELECT COUNT(*) FROM intake_answers WHERE intake_session_id = 'intake-test-upsert-001'"
        ).fetchone()[0]
        value = connection.execute(
            """
            SELECT answer_value
            FROM intake_answers
            WHERE intake_session_id = 'intake-test-upsert-001' AND question_key = 'primary_need'
            """
        ).fetchone()[0]

    assert row_count == 1
    assert value == "employment"