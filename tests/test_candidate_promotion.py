from __future__ import annotations

import sqlite3

from candidate_promotion import (
    build_profile_defaults_from_answers,
    generate_next_youth_id,
    load_promotable_candidate_intakes,
    promote_candidate_to_youth,
)


def _create_schema(connection: sqlite3.Connection) -> None:
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

        CREATE TABLE caseworker_youth (
            youth_id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id)
        );

        CREATE TABLE caseworkers (
            caseworker_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'Caseworker',
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE case_assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL UNIQUE,
            caseworker_id TEXT NOT NULL,
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            case_status TEXT NOT NULL DEFAULT 'assigned',
            priority_level TEXT NOT NULL DEFAULT 'Medium',
            next_follow_up_date TEXT,
            last_updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

        CREATE TABLE assigned_resources (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT,
            candidate_profile_id TEXT,
            profile_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            intake_session_id TEXT,
            recommendation_id INTEGER,
            assigned_by TEXT,
            priority_level TEXT NOT NULL,
            match_score REAL,
            match_reason TEXT,
            assignment_status TEXT NOT NULL DEFAULT 'assigned',
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            follow_up_date TEXT,
            notes TEXT
        );
        """
    )


def test_generate_next_youth_id_increments_existing_ids() -> None:
    with sqlite3.connect(":memory:") as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO youth_profiles (
                youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
            ) VALUES
                ('YP-0001', 16, 'Kent', 'Not enrolled', 'Unemployed', 'Stable housing', 'Assigned', 1, 'No'),
                ('YP-0007', 17, 'Sussex', 'GED/HiSET', 'Part-time', 'Couch surfing', 'Not assigned', 2, 'Yes')
            """
        )

        assert generate_next_youth_id(connection) == 'YP-0008'


def test_build_profile_defaults_from_answers_maps_candidate_answers() -> None:
    defaults = build_profile_defaults_from_answers(
        {
            'housing_status': 'couch_surfing',
            'employment_status': 'training',
            'education_status': 'diploma_or_ged',
        }
    )

    assert defaults['housing'] == 'Couch surfing'
    assert defaults['employment'] == 'Training / internship'
    assert defaults['education'] == 'GED/HiSET'
    assert defaults['prior_homelessness'] == 'Yes'


def test_load_promotable_candidate_intakes_returns_latest_completed_candidates() -> None:
    with sqlite3.connect(":memory:") as connection:
        _create_schema(connection)
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
            ) VALUES
                (
                    'INTAKE-CAND-OLD',
                    NULL,
                    'CP-3001',
                    'candidate',
                    '2026-06-01T09:00:00+00:00',
                    '2026-06-01T09:10:00+00:00',
                    'completed',
                    'future_path_ai_assistant_v1',
                    'streamlit',
                    'employment'
                ),
                (
                    'INTAKE-CAND-NEW',
                    NULL,
                    'CP-3001',
                    'candidate',
                    '2026-06-01T11:00:00+00:00',
                    '2026-06-01T11:15:00+00:00',
                    'completed',
                    'future_path_ai_assistant_v1',
                    'streamlit',
                    'housing'
                ),
                (
                    'INTAKE-CAND-IP',
                    NULL,
                    'CP-3002',
                    'candidate',
                    '2026-06-01T12:00:00+00:00',
                    NULL,
                    'in_progress',
                    'future_path_ai_assistant_v1',
                    'streamlit',
                    'food'
                ),
                (
                    'INTAKE-YOUTH-IGNORE',
                    'YP-0999',
                    NULL,
                    'youth',
                    '2026-06-01T13:00:00+00:00',
                    '2026-06-01T13:10:00+00:00',
                    'completed',
                    'future_path_ai_assistant_v1',
                    'streamlit',
                    'education'
                )
            """
        )
        connection.execute(
            """
            INSERT INTO assigned_resources (
                youth_id,
                candidate_profile_id,
                profile_type,
                resource_id,
                intake_session_id,
                assigned_by,
                priority_level,
                match_reason,
                assignment_status
            ) VALUES
                (NULL, 'CP-3001', 'candidate', 'R-1', 'INTAKE-CAND-NEW', 'test_ai', 'High', 'Matched housing need', 'assigned'),
                (NULL, 'CP-3001', 'candidate', 'R-2', 'INTAKE-CAND-NEW', 'test_ai', 'Medium', 'Matched employment need', 'assigned')
            """
        )

        frame = load_promotable_candidate_intakes(connection)

        assert list(frame["candidate_profile_id"]) == ["CP-3001"]
        assert frame.iloc[0]["intake_session_id"] == "INTAKE-CAND-NEW"
        assert int(frame.iloc[0]["assignment_count"]) == 2
        assert frame.iloc[0]["top_need_category"] == "housing"


def test_promote_candidate_to_youth_migrates_linked_records() -> None:
    with sqlite3.connect(":memory:") as connection:
        _create_schema(connection)
        connection.execute(
            """
            INSERT INTO caseworkers (caseworker_id, full_name, email)
            VALUES ('CW-001', 'Case Worker', 'cw@example.org')
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
                'INTAKE-CAND-001',
                NULL,
                'CP-2001',
                'candidate',
                '2026-06-01T10:00:00+00:00',
                '2026-06-01T10:15:00+00:00',
                'completed',
                'future_path_ai_assistant_v1',
                'streamlit',
                'housing'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO assigned_resources (
                youth_id,
                candidate_profile_id,
                profile_type,
                resource_id,
                intake_session_id,
                assigned_by,
                priority_level,
                match_reason,
                assignment_status
            ) VALUES (
                NULL,
                'CP-2001',
                'candidate',
                'R-HOUSE',
                'INTAKE-CAND-001',
                'test_ai',
                'High',
                'Matched housing need',
                'assigned'
            )
            """
        )

        session_id = promote_candidate_to_youth(
            connection,
            candidate_profile_id='CP-2001',
            youth_id='YP-0201',
            age=17,
            county='Kent',
            education='GED/HiSET',
            employment='Unemployed',
            housing='Couch surfing',
            mentor_status='Not assigned',
            placement_count=0,
            prior_homelessness='Yes',
            first_name='Aaliyah',
            last_name='Carter',
            caseworker_id='CW-001',
            case_priority='High',
        )
        connection.commit()

        assert session_id == 'INTAKE-CAND-001'

        youth_row = connection.execute(
            "SELECT youth_id, county, employment, housing FROM youth_profiles WHERE youth_id = 'YP-0201'"
        ).fetchone()
        assert youth_row == ('YP-0201', 'Kent', 'Unemployed', 'Couch surfing')

        intake_row = connection.execute(
            "SELECT youth_id, candidate_profile_id, profile_type FROM intake_sessions WHERE intake_session_id = 'INTAKE-CAND-001'"
        ).fetchone()
        assert intake_row == ('YP-0201', None, 'youth')

        assignment_row = connection.execute(
            "SELECT youth_id, candidate_profile_id, profile_type FROM assigned_resources WHERE intake_session_id = 'INTAKE-CAND-001'"
        ).fetchone()
        assert assignment_row == ('YP-0201', None, 'youth')

        case_row = connection.execute(
            "SELECT youth_id, caseworker_id, priority_level FROM case_assignments WHERE youth_id = 'YP-0201'"
        ).fetchone()
        assert case_row == ('YP-0201', 'CW-001', 'High')

        name_row = connection.execute(
            "SELECT first_name, last_name FROM caseworker_youth WHERE youth_id = 'YP-0201'"
        ).fetchone()
        assert name_row == ('Aaliyah', 'Carter')