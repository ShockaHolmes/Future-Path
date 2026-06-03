from __future__ import annotations

import sqlite3
from pathlib import Path

from generate_recommendations import save_recommendations


def _create_test_schema(connection: sqlite3.Connection) -> None:
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

        CREATE TABLE recommendations (
            recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            risk_score_id INTEGER,
            intake_session_id TEXT,
            match_score REAL,
            priority_rank INTEGER,
            recommendation_reason TEXT,
            recommendation_source TEXT NOT NULL,
            recommendation_status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE intake_sessions (
            intake_session_id TEXT PRIMARY KEY,
            youth_id TEXT,
            candidate_profile_id TEXT,
            profile_type TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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


def test_save_recommendations_assigns_relevant_resources_and_priority() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        _create_test_schema(connection)

        connection.execute(
            """
            INSERT INTO youth_profiles (
                youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
            ) VALUES
                ('YP-0001', 18, 'New Castle', 'Not enrolled', 'Unemployed', 'Couch surfing', 'Not assigned', 6, 'Yes'),
                ('YP-0002', 16, 'Kent', 'High school', 'Part-time', 'Stable housing', 'Assigned', 1, 'No')
            """
        )

        connection.execute(
            """
            INSERT INTO resources (
                resource_id, resource_name, category, need_tags, service_area, county, city, state,
                eligibility_age_min, eligibility_age_max, description, referral_method, contact_phone,
                website, ai_match_rules, default_priority, caseworker_notes
            ) VALUES
                (
                    'R-HOUSE', 'Housing Support', 'Housing', 'housing;case_management;homelessness',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 13, 24,
                    'Housing support', 'Referral', '', 'https://example.org/house', '', 'High', ''
                ),
                (
                    'R-JOB', 'Job Training', 'Employment', 'employment;job_training;workforce_development',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 14, 24,
                    'Job support', 'Referral', '', 'https://example.org/job', '', 'High', ''
                ),
                (
                    'R-EDU', 'GED Program', 'Education', 'GED;education;academic_support',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 14, 24,
                    'GED support', 'Referral', '', 'https://example.org/edu', '', 'Medium', ''
                ),
                (
                    'R-MENTOR', 'Mentor Match', 'Mentorship', 'mentorship;community_support;positive_adult',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 13, 24,
                    'Mentor support', 'Referral', '', 'https://example.org/mentor', '', 'Medium', ''
                ),
                (
                    'R-WELL', 'Counseling Services', 'Wellness', 'mental_health;counseling;health',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 13, 24,
                    'Counseling support', 'Referral', '', 'https://example.org/well', '', 'High', ''
                ),
                (
                    'R-211', 'General Support 211', 'Navigation', 'general_support;housing',
                    'Statewide', 'Statewide', 'Statewide', 'DE', 0, 99,
                    'Fallback', 'Call 211', '211', 'https://example.org/211', '', 'High', ''
                )
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
                'INTAKE-YP-0001',
                'YP-0001',
                NULL,
                'youth',
                '2026-01-01T10:00:00+00:00',
                '2026-01-01T10:05:00+00:00',
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
                ('INTAKE-YP-0001', 'health_wellness_need', 'Need health support?', 'yes', 'single_select'),
                ('INTAKE-YP-0001', 'primary_need', 'Primary need?', 'housing', 'single_select')
            """
        )

        youth_count, inserted_count = save_recommendations(connection, source="test_source", top_n=5)
        connection.commit()

        assert youth_count == 2
        assert inserted_count >= 2

        rows = connection.execute(
            """
            SELECT youth_id, resource_id, intake_session_id, priority_rank, recommendation_reason, recommendation_source
            FROM recommendations
            WHERE recommendation_source = 'test_source'
            ORDER BY youth_id, recommendation_id
            """
        ).fetchall()

        assert rows
        by_youth = {}
        for row in rows:
            by_youth.setdefault(row[0], []).append(row)

        assert "YP-0001" in by_youth
        assert "YP-0002" in by_youth
        for youth_rows in by_youth.values():
            assert len(youth_rows) >= 1
            for rec in youth_rows:
                assert rec[3] in {1, 2, 3}
                assert "Priority level:" in rec[4]
                assert rec[5] == "test_source"

        assert all(row[2] == "INTAKE-YP-0001" for row in by_youth["YP-0001"])
        assert all(row[2] is None for row in by_youth["YP-0002"])

        first_youth_resource_ids = {row[1] for row in by_youth["YP-0001"]}
        assert "R-HOUSE" in first_youth_resource_ids
        assert "R-JOB" in first_youth_resource_ids
        assert "R-EDU" in first_youth_resource_ids
        assert "R-MENTOR" in first_youth_resource_ids