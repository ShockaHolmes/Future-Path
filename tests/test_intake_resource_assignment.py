from __future__ import annotations

import sqlite3

from assign_resources_from_intake import assign_resources_from_intake, map_answers_to_needs


def _create_base_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
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
        );

        CREATE TABLE resources (
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
            contact_phone TEXT,
            website TEXT,
            ai_match_rules TEXT,
            default_priority TEXT NOT NULL,
            caseworker_notes TEXT
        );

        CREATE TABLE intake_sessions (
            intake_session_id TEXT PRIMARY KEY,
            youth_id TEXT,
            candidate_profile_id TEXT,
            profile_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            session_status TEXT NOT NULL,
            assistant_version TEXT,
            channel TEXT,
            top_need_category TEXT
        );

        CREATE TABLE intake_answers (
            intake_answer_id INTEGER PRIMARY KEY AUTOINCREMENT,
            intake_session_id TEXT NOT NULL,
            question_key TEXT NOT NULL,
            question_text TEXT,
            answer_value TEXT,
            answer_type TEXT,
            answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def _insert_resources(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO resources (
            resource_id, resource_name, category, need_tags, service_area, county, city, state,
            eligibility_age_min, eligibility_age_max, description, referral_method,
            contact_phone, website, ai_match_rules, default_priority, caseworker_notes
        ) VALUES
            (
                'R-HOUSE', 'Housing Support', 'Housing', 'housing;homelessness;housing_navigation',
                'Statewide', 'Statewide', 'Statewide', 'DE',
                13, 24, 'Housing help', 'Referral', '', 'https://example.org/house', '', 'High', ''
            ),
            (
                'R-JOB', 'Job Training', 'Employment', 'employment;job_training;workforce_development',
                'Statewide', 'Statewide', 'Statewide', 'DE',
                14, 24, 'Workforce support', 'Referral', '', 'https://example.org/job', '', 'High', ''
            ),
            (
                'R-SAFE', 'Safety Services', 'Safety', 'safety;crisis;emergency_support',
                'Statewide', 'Statewide', 'Statewide', 'DE',
                12, 24, 'Safety planning', 'Referral', '', 'https://example.org/safe', '', 'High', ''
            )
        """
    )


def _insert_completed_candidate_intake(connection: sqlite3.Connection) -> None:
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
        ) VALUES (
            'INTAKE-CANDIDATE-001',
            NULL,
            'CP-1001',
            'candidate',
            '2026-06-01T10:00:00+00:00',
            '2026-06-01T10:15:00+00:00',
            'completed',
            'future_path_ai_assistant_v1',
            'cli',
            'housing'
        )
        """
    )

    connection.execute(
        """
        INSERT INTO intake_answers (
            intake_session_id,
            question_key,
            question_text,
            answer_value,
            answer_type
        ) VALUES
            ('INTAKE-CANDIDATE-001', 'housing_status', 'Housing?', 'couch_surfing', 'single_select'),
            ('INTAKE-CANDIDATE-001', 'employment_status', 'Employment?', 'unemployed', 'single_select'),
            ('INTAKE-CANDIDATE-001', 'safety_concern', 'Safety concern?', 'yes', 'single_select'),
            ('INTAKE-CANDIDATE-001', 'primary_need', 'Primary need?', 'housing', 'single_select')
        """
    )


def test_assign_resources_from_candidate_intake_persists_assignments() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_base_schema(connection)
        _insert_resources(connection)
        _insert_completed_candidate_intake(connection)

        result = assign_resources_from_intake(
            connection,
            intake_session_id="INTAKE-CANDIDATE-001",
            top_n=3,
            assigned_by="test_ai",
        )
        connection.commit()

        assert result["session_id"] == "INTAKE-CANDIDATE-001"
        assert result["profile_type"] == "candidate"
        assert result["candidate_profile_id"] == "CP-1001"
        assert result["assigned_rows"] >= 1

        rows = connection.execute(
            """
            SELECT candidate_profile_id, profile_type, intake_session_id, resource_id, priority_level, match_reason
            FROM assigned_resources
            WHERE intake_session_id = 'INTAKE-CANDIDATE-001'
            ORDER BY assignment_id
            """
        ).fetchall()

        assert rows
        for row in rows:
            assert row[0] == "CP-1001"
            assert row[1] == "candidate"
            assert row[2] == "INTAKE-CANDIDATE-001"
            assert row[4] in {"High", "Medium", "Low"}
            assert "Matched" in row[5]

        resource_ids = {row[3] for row in rows}
        assert "R-HOUSE" in resource_ids


def test_assign_resources_from_youth_intake_links_youth_profile() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_base_schema(connection)
        _insert_resources(connection)

        connection.execute(
            """
            INSERT INTO youth_profiles (
                youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
            ) VALUES ('YP-0001', 18, 'Kent', 'Not enrolled', 'Unemployed', 'Couch surfing', 'Not assigned', 5, 'Yes')
            """
        )

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
            ) VALUES (
                'INTAKE-YOUTH-001',
                'YP-0001',
                NULL,
                'youth',
                '2026-06-01T11:00:00+00:00',
                '2026-06-01T11:10:00+00:00',
                'completed',
                'future_path_ai_assistant_v1',
                'cli',
                'employment'
            )
            """
        )

        connection.execute(
            """
            INSERT INTO intake_answers (intake_session_id, question_key, question_text, answer_value, answer_type)
            VALUES
                ('INTAKE-YOUTH-001', 'employment_status', 'Employment?', 'unemployed', 'single_select'),
                ('INTAKE-YOUTH-001', 'primary_need', 'Primary need?', 'employment', 'single_select')
            """
        )

        result = assign_resources_from_intake(
            connection,
            intake_session_id="INTAKE-YOUTH-001",
            top_n=2,
            assigned_by="test_ai",
        )
        connection.commit()

        assert result["profile_type"] == "youth"
        assert result["youth_id"] == "YP-0001"

        row = connection.execute(
            """
            SELECT youth_id, candidate_profile_id, profile_type, intake_session_id
            FROM assigned_resources
            WHERE intake_session_id = 'INTAKE-YOUTH-001'
            LIMIT 1
            """
        ).fetchone()

        assert row is not None
        assert row[0] == "YP-0001"
        assert row[1] is None
        assert row[2] == "youth"
        assert row[3] == "INTAKE-YOUTH-001"


def test_map_answers_to_needs_scores_expected_categories() -> None:
    answers = {
        "housing_status": "couch_surfing",
        "employment_status": "unemployed",
        "education_status": "no_diploma_or_ged",
        "transportation_access": "none",
        "food_access": "no",
        "health_wellness_need": "yes",
        "documents_status": "none",
        "support_system": "limited",
        "safety_concern": "yes",
        "primary_need": "housing",
    }

    needs, total_points = map_answers_to_needs(answers, top_need_category="housing")

    assert "unstable_housing" in needs
    assert "unemployment" in needs
    assert "education_need" in needs
    assert "transportation_need" in needs
    assert "food_support" in needs
    assert "wellness_need" in needs
    assert "documents_need" in needs
    assert "support_need" in needs
    assert "safety_need" in needs
    assert total_points > 0
    assert needs["unstable_housing"]["risk_points"] >= 45
