from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from assign_resources_from_intake import ensure_assigned_resources_table_integrity
from future_path_ai_intake import ensure_intake_tables

DEFAULT_DB_PATH = Path("database/future_path.db")
OVERVIEW_URL = "http://localhost:8501"
PROFILE_LOOKUP_URL = "http://localhost:8502"
AI_ASSISTANT_URL = "http://localhost:8503"
CASEWORKER_URL = "http://localhost:8504"
YOUTH_DASHBOARD_URL = "http://localhost:8505"


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_resources_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS resources (
            resource_id TEXT PRIMARY KEY,
            resource_name TEXT NOT NULL,
            category TEXT NOT NULL,
            need_tags TEXT NOT NULL,
            service_area TEXT NOT NULL,
            county TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            eligibility_age_min INTEGER NOT NULL CHECK (eligibility_age_min BETWEEN 0 AND 120),
            eligibility_age_max INTEGER NOT NULL CHECK (eligibility_age_max BETWEEN 0 AND 120),
            description TEXT NOT NULL,
            referral_method TEXT NOT NULL,
            contact_phone TEXT,
            website TEXT,
            ai_match_rules TEXT,
            default_priority TEXT NOT NULL CHECK (default_priority IN ('High', 'Medium', 'Low')),
            caseworker_notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (eligibility_age_min <= eligibility_age_max)
        )
        """
    )


def ensure_caseworker_tables(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    ensure_intake_tables(connection)
    ensure_resources_table(connection)
    ensure_assigned_resources_table_integrity(connection)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS caseworkers (
            caseworker_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL DEFAULT 'Caseworker',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS case_assignments (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL UNIQUE,
            caseworker_id TEXT NOT NULL,
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            case_status TEXT NOT NULL DEFAULT 'assigned' CHECK (
                case_status IN ('assigned', 'in_progress', 'on_hold', 'closed')
            ),
            priority_level TEXT NOT NULL DEFAULT 'Medium' CHECK (
                priority_level IN ('High', 'Medium', 'Low')
            ),
            next_follow_up_date TEXT,
            last_updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (caseworker_id) REFERENCES caseworkers(caseworker_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS case_notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            caseworker_id TEXT NOT NULL,
            note_text TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (caseworker_id) REFERENCES caseworkers(caseworker_id) ON DELETE RESTRICT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS follow_ups (
            follow_up_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            caseworker_id TEXT NOT NULL,
            follow_up_date TEXT NOT NULL,
            follow_up_status TEXT NOT NULL DEFAULT 'scheduled' CHECK (
                follow_up_status IN ('scheduled', 'completed', 'missed', 'rescheduled')
            ),
            details TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (caseworker_id) REFERENCES caseworkers(caseworker_id) ON DELETE RESTRICT
        )
        """
    )


def upsert_caseworker(
    connection: sqlite3.Connection,
    caseworker_id: str,
    full_name: str,
    email: str,
    is_active: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO caseworkers (caseworker_id, full_name, email, role, is_active)
        VALUES (?, ?, ?, 'Caseworker', ?)
        ON CONFLICT(caseworker_id) DO UPDATE SET
            full_name = excluded.full_name,
            email = excluded.email,
            is_active = excluded.is_active,
            updated_at = CURRENT_TIMESTAMP
        """,
        (caseworker_id, full_name, email.strip() or None, 1 if is_active else 0),
    )


def load_caseworkers(connection: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT caseworker_id, full_name, COALESCE(email, '') AS email, is_active
        FROM caseworkers
        ORDER BY full_name ASC, caseworker_id ASC
        """,
        connection,
    )


def load_available_cases(connection: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(connection, "youth_profiles"):
        return pd.DataFrame()
    return pd.read_sql_query(
        """
        WITH latest_risk AS (
            SELECT youth_id, risk_level, overall_risk_score
            FROM (
                SELECT
                    youth_id,
                    risk_level,
                    overall_risk_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
                    ) AS rn
                FROM risk_scores
            ) ranked
            WHERE rn = 1
        ),
        latest_intake AS (
            SELECT youth_id, top_need_category, session_status
            FROM (
                SELECT
                    youth_id,
                    top_need_category,
                    session_status,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(completed_at, started_at, '') DESC
                    ) AS rn
                FROM intake_sessions
                WHERE profile_type = 'youth'
            ) ranked
            WHERE rn = 1
        )
        SELECT
            yp.youth_id,
            yp.county,
            yp.age,
            COALESCE(lr.risk_level, 'Unknown') AS risk_level,
            COALESCE(lr.overall_risk_score, 0.0) AS overall_risk_score,
            COALESCE(li.top_need_category, 'not_set') AS top_need_category,
            COALESCE(li.session_status, 'no_session') AS intake_status
        FROM youth_profiles yp
        LEFT JOIN case_assignments ca ON ca.youth_id = yp.youth_id
            AND ca.case_status IN ('assigned', 'in_progress', 'on_hold')
        LEFT JOIN latest_risk lr ON lr.youth_id = yp.youth_id
        LEFT JOIN latest_intake li ON li.youth_id = yp.youth_id
        WHERE ca.youth_id IS NULL
        ORDER BY
            CASE COALESCE(lr.risk_level, 'Unknown')
                WHEN 'High' THEN 1
                WHEN 'Medium' THEN 2
                WHEN 'Low' THEN 3
                ELSE 4
            END,
            yp.youth_id ASC
        """,
        connection,
    )


def accept_case(connection: sqlite3.Connection, youth_id: str, caseworker_id: str, priority_level: str) -> None:
    connection.execute(
        """
        INSERT INTO case_assignments (youth_id, caseworker_id, case_status, priority_level)
        VALUES (?, ?, 'assigned', ?)
        ON CONFLICT(youth_id) DO UPDATE SET
            caseworker_id = excluded.caseworker_id,
            case_status = 'assigned',
            priority_level = excluded.priority_level,
            last_updated_at = CURRENT_TIMESTAMP
        """,
        (youth_id, caseworker_id, priority_level),
    )


def unassign_case(connection: sqlite3.Connection, youth_id: str, caseworker_id: str) -> None:
    connection.execute(
        """
        DELETE FROM case_assignments
        WHERE youth_id = ?
          AND caseworker_id = ?
        """,
        (youth_id, caseworker_id),
    )


def sync_case_statuses_after_intake_completion(connection: sqlite3.Connection, caseworker_id: str) -> int:
    connection.execute(
        """
        WITH latest_intake AS (
            SELECT youth_id, LOWER(COALESCE(session_status, '')) AS session_status
            FROM (
                SELECT
                    youth_id,
                    session_status,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(completed_at, started_at, '') DESC, intake_session_id DESC
                    ) AS rn
                FROM intake_sessions
                WHERE profile_type = 'youth'
            ) ranked
            WHERE rn = 1
        )
        UPDATE case_assignments
        SET case_status = 'in_progress',
            last_updated_at = CURRENT_TIMESTAMP
        WHERE caseworker_id = ?
          AND case_status = 'assigned'
          AND youth_id IN (
              SELECT youth_id
              FROM latest_intake
              WHERE session_status = 'completed'
          )
        """,
        (caseworker_id,),
    )
    row = connection.execute("SELECT changes() AS changed_count").fetchone()
    return int(row[0]) if row else 0


def load_my_assigned_cases(connection: sqlite3.Connection, caseworker_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        WITH latest_risk AS (
            SELECT youth_id, risk_level, overall_risk_score
            FROM (
                SELECT
                    youth_id,
                    risk_level,
                    overall_risk_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
                    ) AS rn
                FROM risk_scores
            ) ranked
            WHERE rn = 1
        ),
        latest_intake AS (
            SELECT youth_id, top_need_category, session_status
            FROM (
                SELECT
                    youth_id,
                    top_need_category,
                    session_status,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(completed_at, started_at, '') DESC
                    ) AS rn
                FROM intake_sessions
                WHERE profile_type = 'youth'
            ) ranked
            WHERE rn = 1
        )
        SELECT
            ca.assignment_id,
            ca.youth_id,
            yp.county,
            yp.age,
            ca.case_status,
            ca.priority_level,
            ca.assigned_at,
            ca.next_follow_up_date,
            COALESCE(lr.risk_level, 'Unknown') AS risk_level,
            COALESCE(lr.overall_risk_score, 0.0) AS overall_risk_score,
            COALESCE(li.top_need_category, 'not_set') AS top_need_category,
            COALESCE(li.session_status, 'no_session') AS intake_status
        FROM case_assignments ca
        LEFT JOIN youth_profiles yp ON yp.youth_id = ca.youth_id
        LEFT JOIN latest_risk lr ON lr.youth_id = ca.youth_id
        LEFT JOIN latest_intake li ON li.youth_id = ca.youth_id
        WHERE ca.caseworker_id = ?
          AND ca.case_status IN ('assigned', 'in_progress', 'on_hold')
        ORDER BY
            CASE COALESCE(lr.risk_level, 'Unknown')
                WHEN 'High' THEN 1
                WHEN 'Medium' THEN 2
                WHEN 'Low' THEN 3
                ELSE 4
            END,
            ca.assigned_at DESC
        """,
        connection,
        params=[caseworker_id],
    )


def load_latest_risk(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            risk_level,
            overall_risk_score,
            housing_risk_score,
            employment_risk_score,
            education_risk_score,
            model_name,
            model_version,
            calculated_at
        FROM risk_scores
        WHERE youth_id = ?
        ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )


def load_latest_intake(connection: sqlite3.Connection, youth_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    session_df = pd.read_sql_query(
        """
        SELECT
            intake_session_id,
            session_status,
            started_at,
            completed_at,
            top_need_category,
            assistant_version,
            channel
        FROM intake_sessions
        WHERE youth_id = ?
          AND profile_type = 'youth'
        ORDER BY COALESCE(completed_at, started_at, '') DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )

    if session_df.empty:
        return session_df, pd.DataFrame()

    session_id = str(session_df.iloc[0]["intake_session_id"])
    answers_df = pd.read_sql_query(
        """
        SELECT question_key, question_text, answer_value, answered_at
        FROM intake_answers
        WHERE intake_session_id = ?
        ORDER BY intake_answer_id ASC
        """,
        connection,
        params=[session_id],
    )
    return session_df, answers_df


def load_recommendations(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            r.recommendation_id,
            r.resource_id,
            COALESCE(res.resource_name, r.resource_id) AS resource_name,
            COALESCE(r.priority_rank, 99) AS priority_rank,
            COALESCE(r.match_score, 0.0) AS match_score,
            COALESCE(r.recommendation_reason, '') AS recommendation_reason,
            COALESCE(r.recommendation_status, 'proposed') AS recommendation_status,
            COALESCE(r.created_at, '') AS created_at
        FROM recommendations r
        LEFT JOIN resources res ON res.resource_id = r.resource_id
        WHERE r.youth_id = ?
        ORDER BY priority_rank ASC, match_score DESC, created_at DESC
        LIMIT 20
        """,
        connection,
        params=[youth_id],
    )


def load_resource_catalog(connection: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            resource_id,
            resource_name,
            category,
            county,
            default_priority,
            COALESCE(website, '') AS website
        FROM resources
        ORDER BY resource_name ASC
        """,
        connection,
    )


def assign_resources(
    connection: sqlite3.Connection,
    youth_id: str,
    caseworker_id: str,
    resource_ids: list[str],
    priority_level: str,
    follow_up_date: date | None,
    assignment_note: str,
) -> tuple[int, int]:
    if not resource_ids:
        return 0, 0

    intake_row = connection.execute(
        """
        SELECT intake_session_id
        FROM intake_sessions
        WHERE youth_id = ?
          AND profile_type = 'youth'
        ORDER BY COALESCE(completed_at, started_at, '') DESC
        LIMIT 1
        """,
        (youth_id,),
    ).fetchone()
    intake_session_id = str(intake_row[0]) if intake_row else None

    recommendation_rows = connection.execute(
        """
        SELECT recommendation_id, resource_id, match_score, recommendation_reason
        FROM recommendations
        WHERE youth_id = ?
        """,
        (youth_id,),
    ).fetchall()
    recommendation_map = {
        str(row[1]): {
            "recommendation_id": int(row[0]),
            "match_score": float(row[2]) if row[2] is not None else None,
            "reason": str(row[3]) if row[3] is not None else "",
        }
        for row in recommendation_rows
    }

    inserted = 0
    skipped = 0
    for resource_id in resource_ids:
        exists_row = connection.execute(
            """
            SELECT 1
            FROM assigned_resources
            WHERE youth_id = ?
              AND resource_id = ?
              AND profile_type = 'youth'
              AND assignment_status IN ('assigned', 'in_progress')
            LIMIT 1
            """,
            (youth_id, resource_id),
        ).fetchone()
        if exists_row is not None:
            skipped += 1
            continue

        rec = recommendation_map.get(resource_id)
        reason = assignment_note.strip() or (rec["reason"] if rec else "Caseworker selected resource")
        recommendation_id = rec["recommendation_id"] if rec else None
        match_score = rec["match_score"] if rec else None

        connection.execute(
            """
            INSERT INTO assigned_resources (
                youth_id,
                candidate_profile_id,
                profile_type,
                resource_id,
                intake_session_id,
                recommendation_id,
                assigned_by,
                priority_level,
                match_score,
                match_reason,
                assignment_status,
                follow_up_date,
                notes
            ) VALUES (?, NULL, 'youth', ?, ?, ?, ?, ?, ?, ?, 'assigned', ?, ?)
            """,
            (
                youth_id,
                resource_id,
                intake_session_id,
                recommendation_id,
                caseworker_id,
                priority_level,
                match_score,
                reason,
                follow_up_date.isoformat() if follow_up_date else None,
                assignment_note.strip() or None,
            ),
        )
        inserted += 1

    return inserted, skipped


def add_resource(
    connection: sqlite3.Connection,
    resource_name: str,
    category: str,
    need_tags: str,
    service_area: str,
    county: str,
    city: str,
    state: str,
    age_min: int,
    age_max: int,
    description: str,
    referral_method: str,
    contact_phone: str,
    website: str,
    default_priority: str,
    caseworker_notes: str,
) -> str:
    if age_min > age_max:
        raise ValueError("Minimum age cannot exceed maximum age.")

    resource_id = f"res-{uuid4().hex[:10]}"
    connection.execute(
        """
        INSERT INTO resources (
            resource_id,
            resource_name,
            category,
            need_tags,
            service_area,
            county,
            city,
            state,
            eligibility_age_min,
            eligibility_age_max,
            description,
            referral_method,
            contact_phone,
            website,
            ai_match_rules,
            default_priority,
            caseworker_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            resource_id,
            resource_name.strip(),
            category.strip(),
            need_tags.strip(),
            service_area.strip(),
            county.strip(),
            city.strip(),
            state.strip() or "DE",
            age_min,
            age_max,
            description.strip(),
            referral_method.strip(),
            contact_phone.strip() or None,
            website.strip() or None,
            "manual_caseworker_entry",
            default_priority,
            caseworker_notes.strip() or None,
        ),
    )
    return resource_id


def add_case_note(connection: sqlite3.Connection, youth_id: str, caseworker_id: str, note_text: str) -> None:
    connection.execute(
        """
        INSERT INTO case_notes (youth_id, caseworker_id, note_text)
        VALUES (?, ?, ?)
        """,
        (youth_id, caseworker_id, note_text.strip()),
    )


def load_case_notes(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT note_id, caseworker_id, note_text, created_at
        FROM case_notes
        WHERE youth_id = ?
        ORDER BY created_at DESC, note_id DESC
        LIMIT 50
        """,
        connection,
        params=[youth_id],
    )


def save_follow_up(
    connection: sqlite3.Connection,
    youth_id: str,
    caseworker_id: str,
    follow_up_date: date,
    follow_up_status: str,
    details: str,
) -> None:
    connection.execute(
        """
        INSERT INTO follow_ups (youth_id, caseworker_id, follow_up_date, follow_up_status, details)
        VALUES (?, ?, ?, ?, ?)
        """,
        (youth_id, caseworker_id, follow_up_date.isoformat(), follow_up_status, details.strip() or None),
    )
    connection.execute(
        """
        UPDATE case_assignments
        SET next_follow_up_date = ?,
            last_updated_at = CURRENT_TIMESTAMP
        WHERE youth_id = ?
        """,
        (follow_up_date.isoformat(), youth_id),
    )


def load_follow_ups(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT follow_up_id, follow_up_date, follow_up_status, details, caseworker_id, created_at
        FROM follow_ups
        WHERE youth_id = ?
        ORDER BY follow_up_date DESC, follow_up_id DESC
        LIMIT 50
        """,
        connection,
        params=[youth_id],
    )


def update_case_status(connection: sqlite3.Connection, youth_id: str, case_status: str) -> None:
    connection.execute(
        """
        UPDATE case_assignments
        SET case_status = ?,
            last_updated_at = CURRENT_TIMESTAMP
        WHERE youth_id = ?
        """,
        (case_status, youth_id),
    )


def load_high_risk_alerts(connection: sqlite3.Connection, caseworker_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        WITH latest_risk AS (
            SELECT youth_id, risk_level, overall_risk_score
            FROM (
                SELECT
                    youth_id,
                    risk_level,
                    overall_risk_score,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
                    ) AS rn
                FROM risk_scores
            ) ranked
            WHERE rn = 1
        ),
        latest_intake AS (
            SELECT youth_id, top_need_category
            FROM (
                SELECT
                    youth_id,
                    top_need_category,
                    ROW_NUMBER() OVER (
                        PARTITION BY youth_id
                        ORDER BY COALESCE(completed_at, started_at, '') DESC, intake_session_id DESC
                    ) AS rn
                FROM intake_sessions
                WHERE profile_type = 'youth'
            ) ranked
            WHERE rn = 1
        )
        SELECT
            ca.youth_id,
            yp.age,
            yp.housing,
            yp.employment,
            lr.risk_level,
            lr.overall_risk_score,
            ca.case_status,
            COALESCE(ca.next_follow_up_date, '') AS next_follow_up_date,
            COALESCE(li.top_need_category, 'not_set') AS top_need_category
        FROM case_assignments ca
        JOIN latest_risk lr ON lr.youth_id = ca.youth_id
        LEFT JOIN youth_profiles yp ON yp.youth_id = ca.youth_id
        LEFT JOIN latest_intake li ON li.youth_id = ca.youth_id
        WHERE ca.caseworker_id = ?
          AND ca.case_status IN ('assigned', 'in_progress', 'on_hold')
          AND lr.risk_level = 'High'
        ORDER BY lr.overall_risk_score DESC, ca.next_follow_up_date ASC
        """,
        connection,
        params=[caseworker_id],
    )


def inject_caseworker_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700&display=swap');

        .stApp {
            background:
                radial-gradient(circle at 8% 4%, rgba(15, 91, 215, 0.10) 0%, rgba(15, 91, 215, 0.0) 35%),
                radial-gradient(circle at 92% 9%, rgba(239, 68, 68, 0.08) 0%, rgba(239, 68, 68, 0.0) 30%),
                linear-gradient(180deg, #f9fbff 0%, #f4f8ff 100%);
            font-family: 'Plus Jakarta Sans', sans-serif;
            color: #0b1b51;
        }

        .main .block-container {
            padding-top: 1.4rem;
            max-width: 1220px;
        }

        .cw-step-banner {
            border: 1px solid #cbdcff;
            border-radius: 18px;
            background: linear-gradient(180deg, #f6faff 0%, #f3f8ff 100%);
            padding: 10px 16px;
            margin-bottom: 0.7rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .cw-step-title {
            font-family: 'Manrope', sans-serif;
            color: #122f82;
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
        }

        h1, h2, h3, h4 {
            font-family: 'Manrope', sans-serif !important;
            color: #102a78;
        }

        .cw-shell {
            border: 1px solid #d7e4ff;
            border-radius: 22px;
            background: linear-gradient(180deg, #ffffff 0%, #fdfefe 100%);
            box-shadow: 0 14px 38px rgba(17, 61, 156, 0.07);
            margin-bottom: 1rem;
            overflow: hidden;
        }

        .cw-brandbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid #e2ebff;
        }

        .cw-brand-left {
            font-family: 'Manrope', sans-serif;
            font-weight: 800;
            letter-spacing: 0.2px;
            color: #12389f;
            font-size: 1.45rem;
        }

        .cw-brand-right {
            text-align: right;
            color: #16336f;
            font-size: 0.92rem;
            line-height: 1.3;
        }

        .cw-metric-card {
            border: 1px solid #e1eaff;
            border-radius: 14px;
            background: #fbfdff;
            padding: 14px 14px 12px 14px;
            min-height: 110px;
        }

        .cw-metric-label {
            color: #1f3f7e;
            font-weight: 700;
            font-size: 0.86rem;
            line-height: 1.3;
        }

        .cw-metric-value {
            color: #0f1f62;
            font-family: 'Manrope', sans-serif;
            font-size: 2rem;
            font-weight: 800;
            margin-top: 7px;
        }

        .cw-metric-link {
            margin-top: 8px;
            color: #1d4f91;
            font-size: 0.84rem;
            font-weight: 700;
        }

        .cw-alert-wrap {
            border: 1px solid #ffd3d3;
            background: linear-gradient(180deg, #fff9f9 0%, #fffefe 100%);
            border-radius: 16px;
            padding: 14px;
            margin-bottom: 0.8rem;
        }

        .cw-alert-title {
            color: #a91f2f;
            font-family: 'Manrope', sans-serif;
            font-weight: 800;
            margin-bottom: 8px;
        }

        .cw-section-card {
            border: 1px solid #dee8ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 12px;
        }

        .cw-table-subtle {
            color: #244780;
            font-size: 0.86rem;
            font-weight: 700;
            margin-top: 6px;
        }

        .cw-bullets {
            margin: 6px 0 0 0;
            padding-left: 18px;
            color: #7b2230;
            font-size: 0.92rem;
        }

        .cw-activity-item {
            border-bottom: 1px solid #edf2ff;
            padding: 8px 2px;
        }

        .cw-activity-item:last-child {
            border-bottom: 0;
        }

        .cw-activity-title {
            color: #152f6a;
            font-size: 0.93rem;
            font-weight: 600;
        }

        .cw-activity-meta {
            color: #425b8f;
            font-size: 0.81rem;
        }

        .stDataFrame {
            border: 1px solid #e5edff;
            border-radius: 14px;
        }

        [data-testid="stDataFrame"] {
            --gdg-bg-cell: #ffffff;
            --gdg-bg-cell-medium: #f7faff;
            --gdg-bg-header: #edf3ff;
            --gdg-bg-header-has-focus: #e6eeff;
            --gdg-border-color: #dce7ff;
            --gdg-color: #102a78;
            --gdg-text-dark: #102a78;
            --gdg-text-medium: #274594;
            --gdg-text-light: #5d75b1;
            --gdg-accent-color: #2f5dc8;
        }

        [data-testid="stDataFrame"] canvas {
            background: #ffffff !important;
        }

        [data-testid="stDataFrame"] [role="grid"] {
            background: #ffffff !important;
        }

        .stDataFrame [role="grid"],
        .stDataFrame [role="columnheader"],
        .stDataFrame [role="gridcell"] {
            color: #0f225f !important;
        }

        .stDataFrame [role="columnheader"] {
            background-color: #edf3ff !important;
            font-weight: 700 !important;
        }

        .stDataFrame [role="gridcell"] {
            background-color: #ffffff !important;
        }

        .stSelectbox label,
        .stMultiSelect label,
        .stDateInput label,
        .stTextInput label,
        .stTextArea label,
        .stCheckbox label,
        .stNumberInput label,
        .stRadio label {
            color: #1d3773 !important;
            font-weight: 700 !important;
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="textarea"] > div {
            background: #ffffff !important;
            border: 1px solid #d4e1ff !important;
            color: #122d79 !important;
        }

        div[data-baseweb="select"] *,
        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea {
            color: #122d79 !important;
            opacity: 1 !important;
        }

        .stButton > button {
            background: #f3f7ff !important;
            color: #173b80 !important;
            border: 1px solid #cbd8f8 !important;
            font-weight: 700 !important;
        }

        .stButton > button:hover {
            background: #e7efff !important;
            color: #102f84 !important;
        }

        .stButton > button[kind="primary"] {
            background: #ff4a53 !important;
            color: #ffffff !important;
            border-color: #ff4a53 !important;
        }

        .stButton > button[kind="primary"]:hover {
            background: #ef3b45 !important;
            color: #ffffff !important;
        }

        .stCaption,
        .stMarkdown p,
        .stMarkdown li,
        .stText {
            color: #18356f !important;
        }

        [data-testid="stAlert"] {
            border-radius: 14px;
        }

        [data-testid="stAlert"] *,
        [data-testid="stAlert"] p,
        [data-testid="stAlert"] div {
            color: inherit !important;
            opacity: 1 !important;
        }

        [data-testid="stAlert"] *,
        [data-testid="stMetricValue"] *,
        [data-testid="stMetricLabel"] * {
            color: #143681 !important;
        }

        .stSelectbox div[data-baseweb="select"] span,
        .stMultiSelect div[data-baseweb="select"] span {
            color: #122d79 !important;
            opacity: 1 !important;
        }

        .stSidebar,
        .stSidebar * {
            color: #173775 !important;
        }

        @media (max-width: 920px) {
            .cw-brandbar {
                flex-direction: column;
                align-items: flex-start;
                gap: 4px;
            }

            .cw-brand-right {
                text-align: left;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_youth_names(connection: sqlite3.Connection, youth_ids: list[str]) -> dict[str, str]:
    if not youth_ids or not table_exists(connection, "caseworker_youth"):
        return {}

    placeholders = ",".join("?" for _ in youth_ids)
    names_df = pd.read_sql_query(
        f"""
        SELECT youth_id, first_name, last_name
        FROM caseworker_youth
        WHERE youth_id IN ({placeholders})
        """,
        connection,
        params=youth_ids,
    )

    if names_df.empty:
        return {}

    names_df["display_name"] = (
        names_df["first_name"].fillna("").astype(str).str.strip()
        + " "
        + names_df["last_name"].fillna("").astype(str).str.strip()
    ).str.strip()
    names_df["display_name"] = names_df["display_name"].replace("", pd.NA).fillna(names_df["youth_id"].astype(str))
    return dict(zip(names_df["youth_id"].astype(str), names_df["display_name"]))


def load_recent_case_activity(connection: sqlite3.Connection, caseworker_id: str, limit: int = 8) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT youth_id, event_type, details, event_time
        FROM (
            SELECT
                youth_id,
                'Case note added' AS event_type,
                COALESCE(note_text, '') AS details,
                created_at AS event_time
            FROM case_notes
            WHERE caseworker_id = ?

            UNION ALL

            SELECT
                youth_id,
                'Follow-up ' || COALESCE(follow_up_status, 'scheduled') AS event_type,
                COALESCE(details, '') AS details,
                created_at AS event_time
            FROM follow_ups
            WHERE caseworker_id = ?

            UNION ALL

            SELECT
                youth_id,
                'Case assigned' AS event_type,
                'Priority: ' || COALESCE(priority_level, 'Medium') AS details,
                assigned_at AS event_time
            FROM case_assignments
            WHERE caseworker_id = ?
        ) events
        ORDER BY COALESCE(event_time, '') DESC
        LIMIT ?
        """,
        connection,
        params=[caseworker_id, caseworker_id, caseworker_id, limit],
    )


def format_date_label(value: object) -> str:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return "-"
    return timestamp.strftime("%b %-d, %Y")


def estimate_transition_date(age: object) -> str:
    age_number = pd.to_numeric(age, errors="coerce")
    if pd.isna(age_number):
        return "-"

    years_to_18 = max(0.0, 18.0 - float(age_number))
    estimated = pd.Timestamp.today() + pd.to_timedelta(int(round(years_to_18 * 365.25)), unit="D")
    return estimated.strftime("%b %-d, %Y")


def render_top_navigation(current_page: str) -> None:
    buttons = [
        ("Overview", OVERVIEW_URL, "overview"),
        ("Youth Dashboard", YOUTH_DASHBOARD_URL, "youth_dashboard"),
        ("Youth Profiles", PROFILE_LOOKUP_URL, "profile_lookup"),
        ("AI Assistant", AI_ASSISTANT_URL, "ai_assistant"),
        ("Caseworker Dashboard", CASEWORKER_URL, "caseworker_dashboard"),
    ]
    cols = st.columns(5)
    for idx, (label, url, page_key) in enumerate(buttons):
        with cols[idx]:
            if page_key == current_page:
                st.link_button(label, url=url, use_container_width=True, disabled=True)
            else:
                st.link_button(label, url=url, use_container_width=True)


def render() -> None:
    st.set_page_config(page_title="Future Path Caseworker Dashboard", page_icon="FP", layout="wide")
    inject_caseworker_dashboard_styles()
    st.markdown(
        """
        <div class="cw-step-banner">
            <span class="cw-step-title">Caseworker Dashboard</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Monitor youth caseload, respond to high-risk alerts, and manage daily follow-ups.")

    render_top_navigation("caseworker_dashboard")

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()
    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the pipeline first, then reload this dashboard.")
        return

    with sqlite3.connect(db_path) as setup_connection:
        ensure_caseworker_tables(setup_connection)
        setup_connection.commit()

    with sqlite3.connect(db_path) as connection:
        caseworkers_df = load_caseworkers(connection)

    st.sidebar.subheader("Caseworker Login")
    active_caseworker_id = ""
    active_caseworker_name = ""
    active_caseworker_email = ""
    active_caseworker_is_active = False

    if caseworkers_df.empty:
        st.sidebar.info("No caseworker profiles found yet. Create one below.")
    else:
        labels = caseworkers_df.apply(
            lambda row: f"{row['full_name']} ({row['caseworker_id']})"
            + ("" if int(row["is_active"]) == 1 else " [inactive]"),
            axis=1,
        ).tolist()
        active_by_id = {
            str(row["caseworker_id"]): f"{row['full_name']} ({row['caseworker_id']})"
            + ("" if int(row["is_active"]) == 1 else " [inactive]")
            for _, row in caseworkers_df.iterrows()
        }
        selected_label = active_by_id.get(st.session_state.get("active_caseworker_id", ""), labels[0])
        selected_label = st.sidebar.selectbox("Active Caseworker", options=labels, index=labels.index(selected_label))
        selected_id = selected_label.rsplit("(", 1)[1].rstrip(")")
        selected_row = caseworkers_df[caseworkers_df["caseworker_id"] == selected_id].iloc[0]
        active_caseworker_id = str(selected_row["caseworker_id"])
        active_caseworker_name = str(selected_row["full_name"])
        active_caseworker_email = str(selected_row["email"])
        active_caseworker_is_active = int(selected_row["is_active"]) == 1
        st.session_state["active_caseworker_id"] = active_caseworker_id
        if active_caseworker_is_active:
            st.sidebar.success(f"Signed in as {active_caseworker_name}")
        else:
            st.sidebar.warning(f"{active_caseworker_name} is inactive. Case close actions are disabled.")

    st.sidebar.divider()
    st.sidebar.subheader("Create / Update Profile")
    profile_id = st.sidebar.text_input("Caseworker ID", value=active_caseworker_id or "cw-001").strip()
    profile_name = st.sidebar.text_input("Full Name", value=active_caseworker_name).strip()
    profile_email = st.sidebar.text_input("Email", value=active_caseworker_email).strip()

    existing_profile = caseworkers_df[caseworkers_df["caseworker_id"] == profile_id]
    if not existing_profile.empty:
        default_active = int(existing_profile.iloc[0]["is_active"]) == 1
    elif profile_id == active_caseworker_id:
        default_active = active_caseworker_is_active
    else:
        default_active = True
    profile_is_active = st.sidebar.checkbox("Active Caseworker", value=default_active)

    if st.sidebar.button("Save Caseworker Profile", width="stretch"):
        if not profile_id or not profile_name:
            st.sidebar.error("Caseworker ID and Full Name are required.")
        else:
            with sqlite3.connect(db_path) as connection:
                upsert_caseworker(
                    connection,
                    profile_id,
                    profile_name,
                    profile_email,
                    profile_is_active,
                )
                connection.commit()
            st.session_state["active_caseworker_id"] = profile_id
            st.sidebar.success("Caseworker profile saved.")
            st.rerun()

    caseworker_id = st.session_state.get("active_caseworker_id", "")
    if not caseworker_id:
        st.warning("Select or create a caseworker profile in the sidebar to continue.")
        return

    if active_caseworker_id and active_caseworker_id == caseworker_id:
        can_close_case = active_caseworker_is_active
    else:
        with sqlite3.connect(db_path) as connection:
            active_row = connection.execute(
                "SELECT is_active FROM caseworkers WHERE caseworker_id = ?",
                (caseworker_id,),
            ).fetchone()
        can_close_case = bool(active_row and int(active_row[0]) == 1)

    with sqlite3.connect(db_path) as connection:
        synced_cases = sync_case_statuses_after_intake_completion(connection, caseworker_id)
        if synced_cases > 0:
            connection.commit()
        available_df = load_available_cases(connection)
        my_cases_df = load_my_assigned_cases(connection, caseworker_id)
        alerts_df = load_high_risk_alerts(connection, caseworker_id)
        all_youth_ids = sorted(
            set(available_df.get("youth_id", pd.Series(dtype=str)).astype(str).tolist())
            | set(my_cases_df.get("youth_id", pd.Series(dtype=str)).astype(str).tolist())
            | set(alerts_df.get("youth_id", pd.Series(dtype=str)).astype(str).tolist())
        )
        youth_name_map = load_youth_names(connection, all_youth_ids)
        recent_activity_df = load_recent_case_activity(connection, caseworker_id)

    if synced_cases > 0:
        st.info(f"{synced_cases} case(s) moved to in_progress because AI Intake was completed.")

    due_followups_this_week = 0
    aging_out_count = 0
    if not my_cases_df.empty and "next_follow_up_date" in my_cases_df.columns:
        follow_up_dates = pd.to_datetime(my_cases_df["next_follow_up_date"], errors="coerce")
        today_ts = pd.Timestamp.today().normalize()
        week_end = today_ts + pd.Timedelta(days=7)
        due_followups_this_week = int(
            (follow_up_dates.dt.normalize().between(today_ts, week_end, inclusive="both")).fillna(False).sum()
        )

    if not my_cases_df.empty and "age" in my_cases_df.columns:
        aging_out_count = int((pd.to_numeric(my_cases_df["age"], errors="coerce") >= 17).fillna(False).sum())

    st.markdown(
        f"""
        <div class="cw-shell">
            <div class="cw-brandbar">
                <div class="cw-brand-left">Future Path</div>
                <div class="cw-brand-right">
                    <div><strong>{active_caseworker_name or caseworker_id}</strong></div>
                    <div>Caseworker</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(
            f"""
            <div class="cw-metric-card">
                <div class="cw-metric-label">Assigned Youth</div>
                <div class="cw-metric-value">{len(my_cases_df):,}</div>
                <div class="cw-metric-link">View all</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"""
            <div class="cw-metric-card">
                <div class="cw-metric-label">High-Risk Cases</div>
                <div class="cw-metric-value">{len(alerts_df):,}</div>
                <div class="cw-metric-link">View alerts</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"""
            <div class="cw-metric-card">
                <div class="cw-metric-label">Aging Out in 6 Months</div>
                <div class="cw-metric-value">{aging_out_count:,}</div>
                <div class="cw-metric-link">View list</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            f"""
            <div class="cw-metric-card">
                <div class="cw-metric-label">Follow-ups Due This Week</div>
                <div class="cw-metric-value">{due_followups_this_week:,}</div>
                <div class="cw-metric-link">View tasks</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    left, right = st.columns([1.55, 1])

    with left:
        st.markdown("### High-Risk Alerts")
        if alerts_df.empty:
            st.success("No high-risk alerts in your active caseload.")
        else:
            top_alert = alerts_df.iloc[0]
            alert_name = youth_name_map.get(str(top_alert["youth_id"]), str(top_alert["youth_id"]))
            alert_items: list[str] = []

            housing_value = str(top_alert.get("housing", ""))
            employment_value = str(top_alert.get("employment", ""))
            if housing_value and housing_value != "Stable housing":
                alert_items.append(f"Housing status: {housing_value}")
            if employment_value == "Unemployed":
                alert_items.append("Needs employment planning support")
            if str(top_alert.get("top_need_category", "not_set")) not in {"", "not_set"}:
                alert_items.append(f"Top need category: {top_alert['top_need_category']}")
            if str(top_alert.get("next_follow_up_date", "")).strip() == "":
                alert_items.append("No follow-up date scheduled")

            if not alert_items:
                alert_items.append("Priority review recommended based on latest risk score")

            st.markdown(
                f"""
                <div class="cw-alert-wrap">
                    <div class="cw-alert-title">{alert_name} ({top_alert['youth_id']})</div>
                    <div>Age: {int(top_alert['age']) if pd.notna(top_alert.get('age')) else '-'} | Risk score: {float(top_alert['overall_risk_score']) * 100:.1f}%</div>
                    <ul class="cw-bullets">
                        {''.join(f'<li>{item}</li>' for item in alert_items[:4])}
                    </ul>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if st.button("Open Top Alert Case", width="stretch", key="open_top_alert_case"):
                st.session_state["selected_youth_id"] = str(top_alert["youth_id"])
                st.rerun()

    with right:
        st.markdown("### Available Cases")
        if available_df.empty:
            st.info("No unassigned cases at this time.")
        else:
            available_preview = available_df.copy()
            available_preview["youth_name"] = available_preview["youth_id"].astype(str).map(youth_name_map)
            available_preview["youth_name"] = available_preview["youth_name"].fillna(available_preview["youth_id"])
            available_preview = available_preview.rename(
                columns={
                    "youth_name": "Youth",
                    "risk_level": "Risk",
                    "top_need_category": "Top Need",
                    "county": "County",
                }
            )
            st.dataframe(
                available_preview[["Youth", "Risk", "Top Need", "County"]].head(8),
                hide_index=True,
                width="stretch",
            )
            youth_choices = available_df["youth_id"].astype(str).tolist()
            ac1, ac2, ac3 = st.columns([2, 1, 1])
            youth_to_accept = ac1.selectbox("Select Case To Accept", youth_choices)
            accept_priority = ac2.selectbox("Priority", ["High", "Medium", "Low"], index=1)
            accept_clicked = ac3.button("Accept Case", type="primary", width="stretch")
            if accept_clicked:
                with sqlite3.connect(db_path) as connection:
                    accept_case(connection, youth_to_accept, caseworker_id, accept_priority)
                    connection.commit()
                st.success(f"Case accepted: {youth_to_accept}")
                st.rerun()

    st.divider()
    st.subheader("Assigned Youth")
    if my_cases_df.empty:
        st.info("You do not have any active assigned cases yet.")
        return

    assigned_view = my_cases_df.copy()
    assigned_view["Youth"] = assigned_view["youth_id"].astype(str).map(youth_name_map).fillna(assigned_view["youth_id"])
    assigned_view["Age"] = pd.to_numeric(assigned_view["age"], errors="coerce").apply(
        lambda value: f"{int(value)}y" if pd.notna(value) else "-"
    )
    assigned_view["Risk Level"] = assigned_view["risk_level"].astype(str)
    assigned_view["Transition Date"] = assigned_view["age"].apply(estimate_transition_date)
    assigned_view["Next Follow-up"] = assigned_view["next_follow_up_date"].apply(format_date_label)
    st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
    st.dataframe(
        assigned_view[["Youth", "Age", "Risk Level", "Transition Date", "Next Follow-up"]],
        hide_index=True,
        width="stretch",
    )
    st.markdown('<div class="cw-table-subtle">View all assigned youth</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    activity_col, quick_actions_col = st.columns([1.1, 0.9])

    with activity_col:
        st.subheader("Recent Case Activity")
        st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
        if recent_activity_df.empty:
            st.info("No recent case activity recorded yet.")
        else:
            for _, activity in recent_activity_df.iterrows():
                activity_name = youth_name_map.get(str(activity["youth_id"]), str(activity["youth_id"]))
                details = str(activity["details"] or "").strip()
                details_short = details[:90] + "..." if len(details) > 90 else details
                st.markdown(
                    f"""
                    <div class="cw-activity-item">
                        <div class="cw-activity-title">{activity_name} - {activity['event_type']}</div>
                        <div class="cw-activity-meta">{format_date_label(activity['event_time'])} {('- ' + details_short) if details_short else ''}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        st.markdown('</div>', unsafe_allow_html=True)

    with quick_actions_col:
        st.subheader("Quick Actions")
        st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
        if st.button("Add New Youth", width="stretch"):
            st.info("Use the AI Assistant page to start a youth intake session for a new profile.")
        if st.button("Schedule Appointment", width="stretch"):
            st.info("Select a youth below, then use Quick Follow-Up Date in the case management section.")
        if st.button("Create Follow-up Task", width="stretch"):
            st.info("Use Follow-Up Tracker below to create a structured follow-up entry.")
        if st.button("Send Message", width="stretch"):
            st.info("Messaging workflow can be captured as a case note until direct messaging is connected.")
        if st.button("Upload Document", width="stretch"):
            st.info("Document upload can be tracked in Case Notes for now.")
        if st.button("Run Risk Assessment", width="stretch"):
            st.info("Run src/calculate_risk_scores.py and refresh this dashboard to update risk indicators.")
        st.markdown('<div class="cw-table-subtle">View all tools</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.subheader("Case Management Workspace")

    selected_case_label = st.selectbox(
        "Open Youth Profile",
        options=[
            f"{youth_name_map.get(str(row['youth_id']), str(row['youth_id']))} ({row['youth_id']}) | {row['risk_level']} | {row['top_need_category']}"
            for _, row in my_cases_df.iterrows()
        ],
        index=0,
    )
    selected_youth_id = selected_case_label.split("(", 1)[1].split(")", 1)[0]
    if st.session_state.get("selected_youth_id") in set(my_cases_df["youth_id"].astype(str)):
        selected_youth_id = str(st.session_state["selected_youth_id"])
        st.session_state.pop("selected_youth_id", None)
    selected_case_row = my_cases_df[my_cases_df["youth_id"] == selected_youth_id].iloc[0]

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Youth", youth_name_map.get(str(selected_youth_id), str(selected_youth_id)))
    p2.metric("Risk Level", str(selected_case_row["risk_level"]))
    p3.metric("Top Need", str(selected_case_row["top_need_category"]))
    p4.metric("Case Status", str(selected_case_row["case_status"]))

    s1, s2, s3 = st.columns(3)
    status_options = ["assigned", "in_progress", "on_hold"]
    if can_close_case or str(selected_case_row["case_status"]) == "closed":
        status_options.append("closed")

    current_status = str(selected_case_row["case_status"])
    selected_index = status_options.index(current_status) if current_status in status_options else 0
    case_status = s1.selectbox(
        "Update Case Status",
        options=status_options,
        index=selected_index,
    )
    if not can_close_case:
        s1.caption("Only active caseworkers can set a case to closed.")
    if s1.button("Save Case Status", width="stretch"):
        if case_status == "closed" and not can_close_case:
            st.error("This caseworker is not allowed to close cases.")
            return
        with sqlite3.connect(db_path) as connection:
            update_case_status(connection, selected_youth_id, case_status)
            connection.commit()
        st.success("Case status updated.")
        st.rerun()

    quick_followup = s2.date_input("Quick Follow-Up Date", value=date.today())
    if s2.button("Set Follow-Up Date", width="stretch"):
        with sqlite3.connect(db_path) as connection:
            save_follow_up(connection, selected_youth_id, caseworker_id, quick_followup, "scheduled", "Set from quick action")
            connection.commit()
        st.success("Follow-up scheduled.")
        st.rerun()

    confirm_unassign = s3.checkbox("Confirm Unassign", key=f"confirm_unassign_{selected_youth_id}")
    if s3.button("Unassign Case", width="stretch"):
        if not confirm_unassign:
            st.error("Check 'Confirm Unassign' before removing this case assignment.")
        else:
            with sqlite3.connect(db_path) as connection:
                unassign_case(connection, selected_youth_id, caseworker_id)
                connection.commit()
            st.success(f"Case unassigned: {selected_youth_id}")
            st.rerun()

    st.divider()
    insights_col, actions_col = st.columns([1.05, 1])

    with insights_col:
        st.subheader("AI Assistant Results")
        with sqlite3.connect(db_path) as connection:
            risk_df = load_latest_risk(connection, selected_youth_id)
            intake_session_df, intake_answers_df = load_latest_intake(connection, selected_youth_id)

        if risk_df.empty:
            st.warning("No risk score found for this youth yet.")
        else:
            risk = risk_df.iloc[0]
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Overall Risk", f"{float(risk['overall_risk_score']) * 100:.1f}%")
            r2.metric("Housing Risk", f"{float(risk['housing_risk_score'] or 0.0) * 100:.1f}%")
            r3.metric("Employment Risk", f"{float(risk['employment_risk_score'] or 0.0) * 100:.1f}%")
            r4.metric("Education Risk", f"{float(risk['education_risk_score'] or 0.0) * 100:.1f}%")
            st.caption(f"Model: {risk['model_name']} {risk['model_version'] or ''} | Calculated: {risk['calculated_at']}")

        if intake_session_df.empty:
            st.info("No AI intake session found for this youth.")
        else:
            session = intake_session_df.iloc[0]
            st.write(f"Session ID: {session['intake_session_id']}")
            st.write(f"Session Status: {session['session_status']}")
            st.write(f"Top Need Category: {session['top_need_category'] or 'Not set'}")
            st.write(f"Completed At: {session['completed_at'] or 'In progress'}")
            st.dataframe(intake_answers_df[["question_text", "answer_value", "answered_at"]], hide_index=True, width="stretch")

    with actions_col:
        st.subheader("Resource Assignment")
        with sqlite3.connect(db_path) as connection:
            recommendations_df = load_recommendations(connection, selected_youth_id)
            catalog_df = load_resource_catalog(connection)

        if not recommendations_df.empty:
            st.caption("Recommended resources")
            st.dataframe(
                recommendations_df[["resource_name", "priority_rank", "match_score", "recommendation_reason"]],
                hide_index=True,
                width="stretch",
            )

        options_map = {f"{row['resource_name']} ({row['resource_id']})": str(row['resource_id']) for _, row in catalog_df.iterrows()}
        selected_labels = st.multiselect("Assign one or more resources", options=list(options_map.keys()))
        selected_resource_ids = [options_map[label] for label in selected_labels]
        assign_priority = st.selectbox("Assignment Priority", ["High", "Medium", "Low"], index=1)
        assign_follow_up = st.date_input("Resource Follow-Up Date", value=date.today(), key="resource_follow_up")
        assign_note = st.text_area("Assignment Note", placeholder="Why this resource was selected")

        if st.button("Assign Selected Resources", type="primary", width="stretch"):
            with sqlite3.connect(db_path) as connection:
                inserted, skipped = assign_resources(
                    connection,
                    youth_id=selected_youth_id,
                    caseworker_id=caseworker_id,
                    resource_ids=selected_resource_ids,
                    priority_level=assign_priority,
                    follow_up_date=assign_follow_up,
                    assignment_note=assign_note,
                )
                connection.commit()
            st.success(f"Assigned {inserted} resource(s). Skipped {skipped} existing assignment(s).")

    st.divider()
    form_col, notes_col = st.columns([1, 1])

    with form_col:
        st.subheader("Add New Resource")
        with st.form("add_resource_form"):
            resource_name = st.text_input("Resource Name")
            category = st.text_input("Category")
            need_tags = st.text_input("Need Tags (semicolon-separated)")
            service_area = st.text_input("Service Area", value="Statewide")
            c1, c2, c3 = st.columns(3)
            county = c1.text_input("County", value="Statewide")
            city = c2.text_input("City", value="Various")
            state = c3.text_input("State", value="DE")
            a1, a2 = st.columns(2)
            age_min = int(a1.number_input("Eligibility Age Min", min_value=0, max_value=120, value=14, step=1))
            age_max = int(a2.number_input("Eligibility Age Max", min_value=0, max_value=120, value=24, step=1))
            description = st.text_area("Description")
            referral_method = st.text_input("Referral Method", value="Caseworker referral")
            p1, p2 = st.columns(2)
            contact_phone = p1.text_input("Contact Phone")
            website = p2.text_input("Website")
            default_priority = st.selectbox("Default Priority", ["High", "Medium", "Low"], index=1)
            resource_notes = st.text_area("Caseworker Notes")
            add_resource_submit = st.form_submit_button("Save Resource")

        if add_resource_submit:
            if not resource_name.strip() or not category.strip() or not need_tags.strip() or not description.strip():
                st.error("Resource Name, Category, Need Tags, and Description are required.")
            else:
                try:
                    with sqlite3.connect(db_path) as connection:
                        resource_id = add_resource(
                            connection,
                            resource_name=resource_name,
                            category=category,
                            need_tags=need_tags,
                            service_area=service_area,
                            county=county,
                            city=city,
                            state=state,
                            age_min=age_min,
                            age_max=age_max,
                            description=description,
                            referral_method=referral_method,
                            contact_phone=contact_phone,
                            website=website,
                            default_priority=default_priority,
                            caseworker_notes=resource_notes,
                        )
                        connection.commit()
                    st.success(f"Resource added successfully: {resource_id}")
                except ValueError as error:
                    st.error(str(error))
                except sqlite3.Error as error:
                    st.error(f"Failed to save resource: {error}")

    with notes_col:
        st.subheader("Case Notes")
        note_text = st.text_area("Add Case Note", placeholder="Add updates, barriers, and plan details")
        if st.button("Save Case Note", width="stretch"):
            if not note_text.strip():
                st.error("Note text is required.")
            else:
                with sqlite3.connect(db_path) as connection:
                    add_case_note(connection, selected_youth_id, caseworker_id, note_text)
                    connection.commit()
                st.success("Case note saved.")
                st.rerun()

        with sqlite3.connect(db_path) as connection:
            notes_df = load_case_notes(connection, selected_youth_id)
        if notes_df.empty:
            st.info("No notes for this case yet.")
        else:
            st.dataframe(notes_df, hide_index=True, width="stretch")

    st.divider()
    st.subheader("Follow-Up Tracker")
    fu1, fu2 = st.columns([1, 1])
    follow_up_date = fu1.date_input("Follow-Up Date", value=date.today(), key="followup_tracker_date")
    follow_up_status = fu1.selectbox("Follow-Up Status", ["scheduled", "completed", "missed", "rescheduled"])
    follow_up_details = fu2.text_area("Follow-Up Details", placeholder="Agenda, outreach notes, outcome")

    if st.button("Save Follow-Up", width="stretch"):
        with sqlite3.connect(db_path) as connection:
            save_follow_up(connection, selected_youth_id, caseworker_id, follow_up_date, follow_up_status, follow_up_details)
            connection.commit()
        st.success("Follow-up saved.")
        st.rerun()

    with sqlite3.connect(db_path) as connection:
        follow_ups_df = load_follow_ups(connection, selected_youth_id)
    if follow_ups_df.empty:
        st.info("No follow-up records for this youth yet.")
    else:
        st.dataframe(follow_ups_df, hide_index=True, width="stretch")


if __name__ == "__main__":
    render()
