from __future__ import annotations

import os
import sqlite3
import sys
import smtplib
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from assign_resources_from_intake import ensure_assigned_resources_table_integrity
from candidate_promotion import (
    COUNTY_OPTIONS,
    EDUCATION_OPTIONS,
    EMPLOYMENT_OPTIONS,
    HOUSING_OPTIONS,
    MENTOR_STATUS_OPTIONS,
    PRIOR_HOMELESSNESS_OPTIONS,
    build_profile_defaults_from_answers,
    generate_next_youth_id,
    load_candidate_intake_answers,
    load_promotable_candidate_intakes,
    promote_candidate_to_youth,
)
from dashboard_server_manager import ensure_single_dashboard, switch_dashboard
from dashboard_theme import current_theme_badge_html, render_theme_toggle, theme_component_styles, theme_css_variables, themed_url
from future_path_ai_intake import ensure_intake_tables

DEFAULT_DB_PATH = Path("database/future_path.db")
OVERVIEW_URL = "http://localhost:8601"
PROFILE_LOOKUP_URL = "http://localhost:8602"
AI_ASSISTANT_URL = "http://localhost:8603"
CASEWORKER_URL = "http://localhost:8604"
YOUTH_DASHBOARD_URL = "http://localhost:8605"


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


def ensure_candidate_profiles_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS candidate_profiles (
            candidate_profile_id TEXT PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def load_candidate_profile_names(connection: sqlite3.Connection, candidate_profile_id: str) -> tuple[str, str]:
    if not table_exists(connection, "candidate_profiles"):
        return "", ""
    row = connection.execute(
        """
        SELECT COALESCE(first_name, ''), COALESCE(last_name, '')
        FROM candidate_profiles
        WHERE candidate_profile_id = ?
        """,
        (candidate_profile_id,),
    ).fetchone()
    if row is None:
        return "", ""
    return str(row[0] or "").strip(), str(row[1] or "").strip()


def save_candidate_profile_name(
    connection: sqlite3.Connection,
    candidate_profile_id: str,
    first_name: str,
    last_name: str,
) -> None:
    normalized_candidate_id = candidate_profile_id.strip()
    if not normalized_candidate_id:
        return
    ensure_candidate_profiles_table(connection)
    connection.execute(
        """
        INSERT INTO candidate_profiles (candidate_profile_id, first_name, last_name)
        VALUES (?, ?, ?)
        ON CONFLICT(candidate_profile_id) DO UPDATE SET
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            normalized_candidate_id,
            first_name.strip() or None,
            last_name.strip() or None,
        ),
    )


def ensure_caseworker_tables(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    ensure_intake_tables(connection)
    ensure_resources_table(connection)
    ensure_candidate_profiles_table(connection)
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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS outreach_emails (
            email_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            resource_id TEXT,
            recipient_role TEXT NOT NULL CHECK (recipient_role IN ('youth', 'resource_center')),
            recipient_name TEXT,
            recipient_email TEXT,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            delivery_status TEXT NOT NULL DEFAULT 'draft' CHECK (delivery_status IN ('draft', 'queued', 'sent', 'failed')),
            created_by_caseworker_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT,
            last_error TEXT,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (resource_id) REFERENCES resources(resource_id) ON DELETE SET NULL,
            FOREIGN KEY (created_by_caseworker_id) REFERENCES caseworkers(caseworker_id) ON DELETE SET NULL
        )
        """
    )
    outreach_columns = {row[1] for row in connection.execute("PRAGMA table_info(outreach_emails)").fetchall()}
    if "sent_at" not in outreach_columns:
        connection.execute("ALTER TABLE outreach_emails ADD COLUMN sent_at TEXT")
    if "last_error" not in outreach_columns:
        connection.execute("ALTER TABLE outreach_emails ADD COLUMN last_error TEXT")


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


def reset_youth_intake(connection: sqlite3.Connection, youth_id: str) -> None:
    """Clear all intake sessions and answers for a youth so they can restart their intake."""
    # Delete intake answers first (due to foreign key constraint)
    connection.execute(
        """
        DELETE FROM intake_answers
        WHERE intake_session_id IN (
            SELECT intake_session_id FROM intake_sessions WHERE youth_id = ?
        )
        """,
        (youth_id,),
    )
    # Delete intake sessions
    connection.execute(
        "DELETE FROM intake_sessions WHERE youth_id = ?",
        (youth_id,),
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


def load_active_assigned_resources(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
            ar.assignment_id,
            ar.resource_id,
            COALESCE(res.resource_name, ar.resource_id) AS resource_name,
            COALESCE(ar.assignment_status, 'assigned') AS assignment_status,
            COALESCE(ar.assigned_at, '') AS assigned_at
        FROM assigned_resources ar
        LEFT JOIN resources res ON res.resource_id = ar.resource_id
        WHERE ar.youth_id = ?
          AND ar.profile_type = 'youth'
          AND ar.assignment_status IN ('assigned', 'in_progress')
        ORDER BY ar.assigned_at DESC, ar.assignment_id DESC
        """,
        connection,
        params=[youth_id],
    )


def load_youth_contact(connection: sqlite3.Connection, youth_id: str) -> tuple[str, str]:
    if not table_exists(connection, "caseworker_youth"):
        return "Youth Participant", ""

    column_names = {row[1] for row in connection.execute("PRAGMA table_info(caseworker_youth)").fetchall()}
    email_col = "email" if "email" in column_names else None
    email_select = "COALESCE(email, '')" if email_col else "''"

    row = connection.execute(
        f"""
        SELECT
            COALESCE(NULLIF(TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')), ''), youth_id) AS full_name,
            {email_select} AS email
        FROM caseworker_youth
        WHERE youth_id = ?
        LIMIT 1
        """,
        (youth_id,),
    ).fetchone()
    if row is None:
        return youth_id, ""

    return str(row[0]), str(row[1] or "")


def update_recommendation_statuses(
    connection: sqlite3.Connection,
    accepted_resource_ids: list[str],
    rejected_resource_ids: list[str],
    youth_id: str,
) -> None:
    for resource_id in accepted_resource_ids:
        connection.execute(
            """
            UPDATE recommendations
            SET recommendation_status = 'accepted'
            WHERE youth_id = ?
              AND resource_id = ?
            """,
            (youth_id, resource_id),
        )


def unassign_resources(connection: sqlite3.Connection, youth_id: str, resource_ids: list[str]) -> int:
    if not resource_ids:
        return 0

    unassigned = 0
    for resource_id in resource_ids:
        result = connection.execute(
            """
            DELETE FROM assigned_resources
            WHERE youth_id = ?
              AND profile_type = 'youth'
              AND resource_id = ?
              AND assignment_status IN ('assigned', 'in_progress')
            """,
            (youth_id, resource_id),
        )
        if result.rowcount and result.rowcount > 0:
            unassigned += int(result.rowcount)
            connection.execute(
                """
                UPDATE recommendations
                SET recommendation_status = 'proposed'
                WHERE youth_id = ?
                  AND resource_id = ?
                  AND COALESCE(recommendation_status, 'proposed') = 'accepted'
                """,
                (youth_id, resource_id),
            )

    return unassigned

    for resource_id in rejected_resource_ids:
        connection.execute(
            """
            UPDATE recommendations
            SET recommendation_status = 'rejected'
            WHERE youth_id = ?
              AND resource_id = ?
            """,
            (youth_id, resource_id),
        )


def save_outreach_email_draft(
    connection: sqlite3.Connection,
    youth_id: str,
    resource_id: str | None,
    recipient_role: str,
    recipient_name: str,
    recipient_email: str,
    subject: str,
    body: str,
    caseworker_id: str,
) -> None:
    connection.execute(
        """
        INSERT INTO outreach_emails (
            youth_id,
            resource_id,
            recipient_role,
            recipient_name,
            recipient_email,
            subject,
            body,
            delivery_status,
            created_by_caseworker_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)
        """,
        (
            youth_id,
            resource_id,
            recipient_role,
            recipient_name.strip() or None,
            recipient_email.strip() or None,
            subject.strip(),
            body.strip(),
            caseworker_id,
        ),
    )


def load_outreach_emails(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "outreach_emails"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT
            email_id,
            recipient_role,
            COALESCE(recipient_name, '') AS recipient_name,
            COALESCE(recipient_email, '') AS recipient_email,
            subject,
            body,
            delivery_status,
            created_at,
            COALESCE(sent_at, '') AS sent_at,
            COALESCE(last_error, '') AS last_error
        FROM outreach_emails
        WHERE youth_id = ?
        ORDER BY email_id DESC
        LIMIT 100
        """,
        connection,
        params=[youth_id],
    )


def update_outreach_email_status(
    connection: sqlite3.Connection,
    email_id: int,
    delivery_status: str,
    last_error: str = "",
) -> None:
    connection.execute(
        """
        UPDATE outreach_emails
        SET delivery_status = ?,
            sent_at = CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE sent_at END,
            last_error = CASE WHEN ? = 'failed' THEN ? ELSE '' END
        WHERE email_id = ?
        """,
        (delivery_status, delivery_status, delivery_status, last_error.strip(), email_id),
    )


def send_email_via_smtp(
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    smtp_use_tls: bool,
    sender_email: str,
    recipient_email: str,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = sender_email
    message["To"] = recipient_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
        if smtp_use_tls:
            smtp.starttls()
        if smtp_username.strip():
            smtp.login(smtp_username.strip(), smtp_password)
        smtp.send_message(message)


def build_outreach_email_drafts(
    youth_name: str,
    youth_id: str,
    caseworker_name: str,
    resources: list[dict[str, str]],
) -> tuple[str, str, list[tuple[str, str, str, str]]]:
    resource_lines = "\n".join(
        [f"- {item['resource_name']} ({item['category']}) | Next step: {item['next_step']}" for item in resources]
    )
    youth_subject = f"Future Path Support Plan Update for {youth_name}"
    youth_body = (
        f"Hi {youth_name},\n\n"
        f"Your caseworker, {caseworker_name}, reviewed your AI intake and approved the following support resources:\n"
        f"{resource_lines}\n\n"
        "Please review your Youth Dashboard for details and follow the next steps.\n\n"
        "If you need help, reply to your caseworker or submit a Help Request in Future Path.\n\n"
        "- Future Path Team"
    )

    resource_drafts: list[tuple[str, str, str, str]] = []
    for item in resources:
        subject = f"Action Needed: Referral for {youth_name} ({youth_id})"
        body = (
            f"Hello {item['resource_name']} Team,\n\n"
            f"A Future Path caseworker has approved a referral for {youth_name} ({youth_id}).\n"
            f"Resource category: {item['category']}\n"
            f"Requested next step: {item['next_step']}\n\n"
            "Please confirm intake availability and next appointment details.\n\n"
            f"Assigned by: {caseworker_name}\n"
            "- Future Path"
        )
        resource_drafts.append((item["resource_id"], item["resource_name"], subject, body))

    return youth_subject, youth_body, resource_drafts


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
        if recommendation_id is not None:
            connection.execute(
                """
                UPDATE recommendations
                SET recommendation_status = 'accepted'
                WHERE recommendation_id = ?
                """,
                (recommendation_id,),
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


def load_youth_profile_snapshot(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if table_exists(connection, "caseworker_youth"):
        query = """
        SELECT
            yp.youth_id,
            yp.age,
            yp.county,
            yp.education,
            yp.employment,
            yp.housing,
            yp.mentor_status,
            yp.placement_count,
            yp.prior_homelessness,
            COALESCE(cw.first_name, '') AS first_name,
            COALESCE(cw.last_name, '') AS last_name
        FROM youth_profiles yp
        LEFT JOIN caseworker_youth cw ON cw.youth_id = yp.youth_id
        WHERE yp.youth_id = ?
        LIMIT 1
        """
    else:
        query = """
        SELECT
            youth_id,
            age,
            county,
            education,
            employment,
            housing,
            mentor_status,
            placement_count,
            prior_homelessness,
            '' AS first_name,
            '' AS last_name
        FROM youth_profiles
        WHERE youth_id = ?
        LIMIT 1
        """

    return pd.read_sql_query(
        query,
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


def _option_index(options: list[str], value: str) -> int:
    return options.index(value) if value in options else 0


def inject_caseworker_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700&display=swap');
        """
        + theme_css_variables()
        + theme_component_styles()
        + """

        .stApp {
            background:
                radial-gradient(circle at 8% 4%, rgba(15, 91, 215, 0.10) 0%, rgba(15, 91, 215, 0.0) 35%),
                radial-gradient(circle at 92% 9%, rgba(239, 68, 68, 0.08) 0%, rgba(239, 68, 68, 0.0) 30%),
                linear-gradient(180deg, #f9fbff 0%, #f4f8ff 100%);
            font-family: 'Plus Jakarta Sans', sans-serif;
            color: #0b1b51;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #f8fbff 0%, #f1f6ff 100%) !important;
            color: #16356c !important;
            border-right: 1px solid #d9e5fb !important;
        }

        [data-testid="stSidebar"] * {
            color: #173775 !important;
        }

        [data-testid="stSidebar"] div[data-baseweb="input"] > div,
        [data-testid="stSidebar"] div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] div[data-baseweb="textarea"] > div {
            background: #ffffff !important;
            border: 1px solid #cfe0f5 !important;
            color: #173775 !important;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] span {
            color: #173775 !important;
            opacity: 1 !important;
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

        .cw-left-nav {
            position: sticky;
            top: 0.75rem;
            border: 1px solid #dee8ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 12px;
            margin-bottom: 0.9rem;
        }

        .cw-jump-btn {
            display: block;
            width: 100%;
            text-decoration: none !important;
            border: 1px solid #cbd8f8;
            border-radius: 10px;
            background: #f3f7ff;
            color: #173b80 !important;
            font-weight: 700;
            text-align: center;
            padding: 0.4rem 0.55rem;
            margin: 0.18rem 0;
        }

        .cw-jump-btn:hover {
            background: #e7efff;
            color: #102f84 !important;
        }

        .cw-anchor-target {
            position: relative;
            top: -70px;
            visibility: hidden;
            height: 0;
            display: block;
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

        .stApp {
            background:
                radial-gradient(circle at 8% 4%, var(--fp-app-overlay-primary) 0%, rgba(15, 91, 215, 0.0) 35%),
                radial-gradient(circle at 92% 9%, var(--fp-app-overlay-accent) 0%, rgba(239, 68, 68, 0.0) 30%),
                linear-gradient(180deg, var(--fp-app-background) 0%, var(--fp-app-background-alt) 100%);
            color: var(--fp-text-primary);
            font-size: 1.04rem;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, var(--fp-sidebar-background) 0%, var(--fp-sidebar-background-alt) 100%) !important;
            color: var(--fp-sidebar-text) !important;
            border-right: 1px solid var(--fp-sidebar-border) !important;
        }

        [data-testid="stSidebar"] *,
        .stSidebar,
        .stSidebar * {
            color: var(--fp-sidebar-text) !important;
        }

        [data-testid="stSidebar"] div[data-baseweb="input"] > div,
        [data-testid="stSidebar"] div[data-baseweb="select"] > div,
        [data-testid="stSidebar"] div[data-baseweb="textarea"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="textarea"] > div {
            background: var(--fp-input-background) !important;
            border: 1px solid var(--fp-input-border) !important;
            color: var(--fp-input-text) !important;
        }

        div[data-baseweb="select"] *,
        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea,
        .stSelectbox div[data-baseweb="select"] span,
        .stMultiSelect div[data-baseweb="select"] span {
            color: var(--fp-input-text) !important;
        }

        .cw-step-banner,
        .cw-shell,
        .cw-metric-card,
        .cw-alert-wrap,
        .cw-section-card,
        .cw-left-nav,
        .stDataFrame {
            border-color: var(--fp-border-primary) !important;
        }

        .cw-step-banner {
            background: linear-gradient(180deg, var(--fp-surface-tertiary) 0%, var(--fp-surface-secondary) 100%) !important;
        }

        .cw-shell {
            background: linear-gradient(180deg, var(--fp-surface-primary) 0%, var(--fp-surface-secondary) 100%) !important;
            box-shadow: 0 14px 38px var(--fp-shadow-soft) !important;
        }

        .cw-brandbar {
            border-bottom: 1px solid var(--fp-border-secondary) !important;
        }

        .cw-metric-card,
        .cw-section-card {
            background: var(--fp-surface-primary) !important;
        }

        .cw-jump-btn {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border-color: var(--fp-button-border) !important;
        }

        .cw-jump-btn:hover {
            background: var(--fp-button-hover) !important;
            color: var(--fp-button-text) !important;
        }

        .cw-alert-wrap {
            background: linear-gradient(180deg, var(--fp-danger-background) 0%, var(--fp-surface-primary) 100%) !important;
        }

        .cw-step-title,
        h1, h2, h3, h4,
        .cw-brand-left,
        .cw-metric-value,
        .cw-activity-title,
        [data-testid="stMetricValue"] *,
        [data-testid="stMetricLabel"] * {
            color: var(--fp-heading) !important;
        }

        .cw-brand-right,
        .cw-metric-label,
        .cw-metric-link,
        .cw-table-subtle,
        .cw-activity-meta,
        .stSelectbox label,
        .stMultiSelect label,
        .stDateInput label,
        .stTextInput label,
        .stTextArea label,
        .stCheckbox label,
        .stNumberInput label,
        .stRadio label,
        .stCaption,
        .stMarkdown p,
        .stMarkdown li,
        .stText {
            color: var(--fp-text-secondary) !important;
        }

        .stCaption,
        .stCaption p {
            font-size: 0.98rem !important;
        }

        .cw-alert-title,
        .cw-bullets {
            color: var(--fp-danger-text) !important;
        }

        .cw-activity-item {
            border-bottom: 1px solid var(--fp-border-secondary) !important;
        }

        .stButton > button {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border: 1px solid var(--fp-button-border) !important;
        }

        .stButton > button:hover {
            background: var(--fp-button-hover) !important;
            color: var(--fp-button-text) !important;
        }

        .stButton > button[kind="primary"] {
            background: var(--fp-button-primary-background) !important;
            color: var(--fp-button-primary-text) !important;
            border-color: var(--fp-button-primary-border) !important;
        }

        .stButton > button[kind="primary"]:hover {
            background: var(--fp-button-primary-hover) !important;
            color: var(--fp-button-primary-text) !important;
        }

        [data-testid="stDataFrame"] {
            --gdg-bg-cell: var(--fp-data-cell-bg);
            --gdg-bg-cell-medium: var(--fp-surface-secondary);
            --gdg-bg-header: var(--fp-data-header-bg);
            --gdg-bg-header-has-focus: var(--fp-data-header-focus-bg);
            --gdg-border-color: var(--fp-border-primary);
            --gdg-color: var(--fp-text-primary);
            --gdg-text-dark: var(--fp-text-primary);
            --gdg-text-medium: var(--fp-text-secondary);
            --gdg-text-light: var(--fp-text-muted);
            --gdg-accent-color: var(--fp-accent-blue);
        }

        .stDataFrame,
        .stDataFrame [role="grid"] {
            font-size: 1.04rem;
        }

        [data-testid="stDataFrame"] canvas,
        [data-testid="stDataFrame"] [role="grid"],
        .stDataFrame [role="gridcell"] {
            background: var(--fp-data-cell-bg) !important;
        }

        .stDataFrame [role="grid"],
        .stDataFrame [role="columnheader"],
        .stDataFrame [role="gridcell"] {
            color: var(--fp-text-primary) !important;
        }

        .stDataFrame [role="columnheader"] {
            background-color: var(--fp-data-header-bg) !important;
            font-size: 1.02rem !important;
            font-weight: 800 !important;
        }

        .stDataFrame [role="gridcell"] {
            font-size: 1.04rem !important;
            font-weight: 600 !important;
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
        ("Overview", "overview"),
        ("Youth Dashboard", "youth_dashboard"),
        ("AI Assistant", "ai_assistant"),
        ("Caseworker Dashboard", "caseworker_dashboard"),
    ]
    cols = st.columns(4)
    for idx, (label, page_key) in enumerate(buttons):
        with cols[idx]:
            if page_key == current_page:
                st.button(label, use_container_width=True, disabled=True, key=f"topnav_disabled_{current_page}_{page_key}")
            else:
                if st.button(label, use_container_width=True, key=f"topnav_switch_{current_page}_{page_key}"):
                    next_url = themed_url(switch_dashboard(page_key, current_key=current_page))
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()


def render_sidebar_quick_jump() -> None:
    st.sidebar.markdown("### Quick Jump")
    tabs = st.sidebar.tabs(["Top", "Workflow", "Records"])

    with tabs[0]:
        st.markdown('<a class="cw-jump-btn" href="#caseload-overview">High-Risk Alerts</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#assigned-youth">Assigned Youth</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#case-workspace">Case Workspace</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#follow-up-tracker">Follow-Up Tracker</a>', unsafe_allow_html=True)

    with tabs[1]:
        st.markdown('<a class="cw-jump-btn" href="#candidate-promotion">Candidate Promotion</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#ai-results">AI Results</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#resource-assignment">Resource Assignment</a>', unsafe_allow_html=True)

    with tabs[2]:
        st.markdown('<a class="cw-jump-btn" href="#email-login-settings">Email Login</a>', unsafe_allow_html=True)
        st.markdown('<a class="cw-jump-btn" href="#outreach-queue">Outreach Queue</a>', unsafe_allow_html=True)


def render() -> None:
    st.set_page_config(page_title="Future Path Caseworker Dashboard", page_icon="FP", layout="wide")
    ensure_single_dashboard("caseworker_dashboard")
    inject_caseworker_dashboard_styles()
    st.markdown(
        """
        <div class="cw-step-banner fp-brand-header">
            <span class="cw-step-title">Caseworker Dashboard</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Monitor youth caseload, respond to high-risk alerts, and manage daily follow-ups.")

    render_top_navigation("caseworker_dashboard")
    render_theme_toggle()

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()
    st.sidebar.divider()
    render_sidebar_quick_jump()

    smtp_host = ""
    smtp_port = int(os.getenv("FUTURE_PATH_SMTP_PORT", "587"))
    smtp_username = ""
    smtp_password = ""
    smtp_sender = os.getenv("FUTURE_PATH_SMTP_FROM", "no-reply@futurepath.local").strip()
    smtp_use_tls = True
    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the pipeline first, then reload this dashboard.")
        return

    with sqlite3.connect(db_path) as setup_connection:
        ensure_caseworker_tables(setup_connection)
        setup_connection.commit()

    with sqlite3.connect(db_path) as connection:
        caseworkers_df = load_caseworkers(connection)

    st.markdown('<span id="caseworker-login" class="cw-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Caseworker Login")
    active_caseworker_id = ""
    active_caseworker_name = ""
    active_caseworker_email = ""
    active_caseworker_is_active = False

    if caseworkers_df.empty:
        st.info("No caseworker profiles found yet. Create one below.")
    else:
        labels = []
        label_by_id: dict[str, str] = {}
        id_by_label: dict[str, str] = {}
        for _, row in caseworkers_df.iterrows():
            label = f"{row['full_name']} ({row['caseworker_id']})" + ("" if int(row["is_active"]) == 1 else " [inactive]")
            row_id = str(row["caseworker_id"])
            labels.append(label)
            label_by_id[row_id] = label
            id_by_label[label] = row_id

        selected_label = label_by_id.get(st.session_state.get("active_caseworker_id", ""), labels[0])
        selected_label = st.selectbox("Active Caseworker", options=labels, index=labels.index(selected_label))
        selected_id = id_by_label[selected_label]
        selected_row = caseworkers_df[caseworkers_df["caseworker_id"] == selected_id].iloc[0]
        active_caseworker_id = str(selected_row["caseworker_id"])
        active_caseworker_name = str(selected_row["full_name"])
        active_caseworker_email = str(selected_row["email"])
        active_caseworker_is_active = int(selected_row["is_active"]) == 1
        st.session_state["active_caseworker_id"] = active_caseworker_id
        if active_caseworker_is_active:
            st.success(f"Signed in as {active_caseworker_name}")
        else:
            st.warning(f"{active_caseworker_name} is inactive. Case close actions are disabled.")

    st.markdown("### Create / Update Profile")
    profile_col1, profile_col2, profile_col3 = st.columns(3)
    profile_id = profile_col1.text_input("Caseworker ID", value=active_caseworker_id or "cw-001").strip()
    profile_name = profile_col2.text_input("Full Name", value=active_caseworker_name).strip()
    profile_email = profile_col3.text_input("Email", value=active_caseworker_email).strip()

    existing_profile = caseworkers_df[caseworkers_df["caseworker_id"] == profile_id]
    if not existing_profile.empty:
        default_active = int(existing_profile.iloc[0]["is_active"]) == 1
    elif profile_id == active_caseworker_id:
        default_active = active_caseworker_is_active
    else:
        default_active = True
    profile_is_active = st.checkbox("Active Caseworker", value=default_active)

    if st.button("Save Caseworker Profile", width="stretch"):
        if not profile_id or not profile_name:
            st.error("Caseworker ID and Full Name are required.")
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
            st.success("Caseworker profile saved.")
            st.rerun()

    caseworker_id = st.session_state.get("active_caseworker_id", "")
    if not caseworker_id:
        st.warning("Select or create a caseworker profile to continue.")
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
            <div class="cw-brandbar fp-brand-header">
                <div class="cw-brand-left">Future Path</div>
                <div class="cw-brand-right">
                    <div><strong>{active_caseworker_name or caseworker_id}</strong></div>
                    <div>Caseworker</div>
                    <div style="margin-top:8px;">{current_theme_badge_html()}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<span id="email-login-settings" class="cw-anchor-target"></span>', unsafe_allow_html=True)
    with st.expander("Email Login and Delivery Settings", expanded=False):
        es1, es2, es3 = st.columns([1, 1, 1])
        smtp_host = es1.text_input("SMTP Host", value=os.getenv("FUTURE_PATH_SMTP_HOST", "")).strip()
        smtp_port = int(
            es1.number_input(
                "SMTP Port",
                min_value=1,
                max_value=65535,
                value=int(os.getenv("FUTURE_PATH_SMTP_PORT", "587")),
                step=1,
            )
        )
        smtp_use_tls = es1.checkbox("Use STARTTLS", value=True)
        smtp_username = es2.text_input("SMTP Username", value=os.getenv("FUTURE_PATH_SMTP_USERNAME", "")).strip()
        smtp_password = es2.text_input(
            "SMTP Password",
            type="password",
            value=os.getenv("FUTURE_PATH_SMTP_PASSWORD", ""),
        )
        smtp_sender = es3.text_input(
            "From Email",
            value=os.getenv("FUTURE_PATH_SMTP_FROM", "no-reply@futurepath.local"),
        ).strip()
        es3.caption("These settings are used for Send Now via SMTP in Outreach Email Queue.")

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
        st.markdown('<span id="caseload-overview" class="cw-anchor-target"></span>', unsafe_allow_html=True)
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
                    "intake_status": "AI Intake",
                    "county": "County",
                }
            )
            st.dataframe(
                available_preview[["Youth", "Risk", "Top Need", "AI Intake", "County"]].head(8),
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
    st.markdown('<span id="assigned-youth" class="cw-anchor-target"></span>', unsafe_allow_html=True)
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
    assigned_view["AI Intake"] = assigned_view["intake_status"].astype(str).str.replace("_", " ").str.title()
    assigned_view["Transition Date"] = assigned_view["age"].apply(estimate_transition_date)
    assigned_view["Next Follow-up"] = assigned_view["next_follow_up_date"].apply(format_date_label)
    st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
    st.dataframe(
        assigned_view[["Youth", "Age", "Risk Level", "AI Intake", "Transition Date", "Next Follow-up"]],
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
        if st.button("Start Candidate Intake", width="stretch"):
            next_url = themed_url(switch_dashboard("ai_assistant", current_key="caseworker_dashboard"))
            st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
            st.stop()
        st.caption("Open AI Assistant, choose candidate profile, and complete intake before promotion.")
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

    with sqlite3.connect(db_path) as connection:
        candidate_intakes_df = load_promotable_candidate_intakes(connection)

    st.markdown('<span id="candidate-promotion" class="cw-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Candidate Intake Promotion")
    st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
    st.write("Candidates are added to this queue as soon as an ID is generated. Promotion is enabled after intake status becomes completed.")

    if candidate_intakes_df.empty:
        st.info("No candidates are in queue yet.")
    else:
        candidate_intakes_df = candidate_intakes_df.copy()
        candidate_intakes_df["Session Status"] = candidate_intakes_df["session_status"].astype(str).str.replace("_", " ").str.title()
        candidate_intakes_df["Last Activity"] = (
            candidate_intakes_df["completed_at"].fillna(candidate_intakes_df["started_at"]).astype(str).str.slice(0, 10)
        )
        candidate_intakes_df["Assigned Resources"] = candidate_intakes_df["assignment_count"].fillna(0).astype(int)

        st.markdown("#### Candidate Queue")
        st.dataframe(
            candidate_intakes_df[["candidate_name", "candidate_profile_id", "Session Status", "top_need_category", "Last Activity", "Assigned Resources"]].rename(
                columns={
                    "candidate_name": "Candidate Name",
                    "candidate_profile_id": "Candidate ID",
                    "top_need_category": "Top Need",
                }
            ),
            hide_index=True,
            width="stretch",
        )

        st.markdown("#### Update Candidate Name")
        name_options = [
            f"{str(row['candidate_name'])} ({str(row['candidate_profile_id'])})"
            for _, row in candidate_intakes_df.iterrows()
        ]
        selected_name_option = st.selectbox(
            "Candidate",
            options=name_options,
            key="candidate_name_editor_select",
        )
        selected_name_candidate_id = selected_name_option.rsplit("(", 1)[-1].rstrip(")").strip()

        with sqlite3.connect(db_path) as connection:
            current_first_name, current_last_name = load_candidate_profile_names(connection, selected_name_candidate_id)

        name_col1, name_col2, name_col3 = st.columns([1, 1, 0.8])
        with name_col1:
            edited_first_name = st.text_input(
                "First Name",
                value=current_first_name,
                key=f"candidate_name_edit_first_{selected_name_candidate_id}",
            )
        with name_col2:
            edited_last_name = st.text_input(
                "Last Name",
                value=current_last_name,
                key=f"candidate_name_edit_last_{selected_name_candidate_id}",
            )
        with name_col3:
            save_name_clicked = st.button(
                "Save Name",
                key=f"candidate_name_save_btn_{selected_name_candidate_id}",
                use_container_width=True,
            )

        if save_name_clicked:
            with sqlite3.connect(db_path) as connection:
                save_candidate_profile_name(
                    connection,
                    selected_name_candidate_id,
                    edited_first_name,
                    edited_last_name,
                )
                connection.commit()
            st.success(f"Updated name for {selected_name_candidate_id}.")
            st.rerun()

        completed_candidate_df = candidate_intakes_df[
            candidate_intakes_df["session_status"].astype(str).str.lower() == "completed"
        ].copy()

        if completed_candidate_df.empty:
            st.info("No completed candidate intakes are ready for promotion yet.")
        else:
            completed_candidate_df["display_label"] = completed_candidate_df.apply(
                lambda row: (
                    f"{row['candidate_name']} ({row['candidate_profile_id']}) | Top Need: {row['top_need_category']} | "
                    f"Completed: {str(row['completed_at'] or row['started_at'])[:10]} | "
                    f"Assigned Resources: {int(row['assignment_count'])}"
                ),
                axis=1,
            )

            st.markdown("#### Promote Completed Candidate")

            selected_candidate_label = st.selectbox(
                "Completed Candidate Intake",
                options=completed_candidate_df["display_label"].tolist(),
                key="candidate_promotion_select",
            )
            selected_candidate_row = completed_candidate_df[
                completed_candidate_df["display_label"] == selected_candidate_label
            ].iloc[0]
            selected_candidate_id = str(selected_candidate_row["candidate_profile_id"])
            selected_intake_session_id = str(selected_candidate_row["intake_session_id"])

            with sqlite3.connect(db_path) as connection:
                candidate_answers = load_candidate_intake_answers(connection, selected_intake_session_id)
                suggested_youth_id = generate_next_youth_id(connection)

            inferred_defaults = build_profile_defaults_from_answers(candidate_answers)

            overview_col, answers_col = st.columns([1.1, 1])
            with overview_col:
                st.markdown("#### Promotion Details")
                st.write(f"Candidate Name: {selected_candidate_row['candidate_name']}")
                st.write(f"Candidate ID: {selected_candidate_id}")
                st.write(f"Intake Session: {selected_intake_session_id}")
                st.write(f"Top Need Category: {selected_candidate_row['top_need_category']}")
                st.write(f"Suggested Youth ID: {suggested_youth_id}")

            with answers_col:
                st.markdown("#### Intake Summary")
                if candidate_answers:
                    summary_rows = pd.DataFrame(
                        [
                            {
                                "Question": key.replace("_", " ").title(),
                                "Answer": value.replace("_", " ").title(),
                            }
                            for key, value in candidate_answers.items()
                        ]
                    )
                    st.dataframe(summary_rows, hide_index=True, width="stretch")
                else:
                    st.caption("No saved answers found for this candidate intake.")

            with st.form(f"promote_candidate_{selected_candidate_id}"):
                p1, p2, p3 = st.columns(3)
                with p1:
                    first_name = st.text_input("First Name", key=f"promote_first_name_{selected_candidate_id}")
                    age = int(
                        st.number_input(
                            "Age",
                            min_value=13,
                            max_value=24,
                            value=17,
                            step=1,
                            key=f"promote_age_{selected_candidate_id}",
                        )
                    )
                    county = st.selectbox(
                        "County",
                        options=COUNTY_OPTIONS,
                        index=0,
                        key=f"promote_county_{selected_candidate_id}",
                    )
                with p2:
                    last_name = st.text_input("Last Name", key=f"promote_last_name_{selected_candidate_id}")
                    youth_id = st.text_input(
                        "Youth ID",
                        value=suggested_youth_id,
                        key=f"promote_youth_id_{selected_candidate_id}",
                    )
                    education = st.selectbox(
                        "Education",
                        options=EDUCATION_OPTIONS,
                        index=_option_index(EDUCATION_OPTIONS, str(inferred_defaults["education"])),
                        key=f"promote_education_{selected_candidate_id}",
                    )
                with p3:
                    assign_to_me = st.checkbox("Assign case to me now", value=True, key=f"promote_assign_{selected_candidate_id}")
                    case_priority = st.selectbox(
                        "Case Priority",
                        options=["High", "Medium", "Low"],
                        index=1,
                        key=f"promote_priority_{selected_candidate_id}",
                    )
                    mentor_status = st.selectbox(
                        "Mentor Status",
                        options=MENTOR_STATUS_OPTIONS,
                        index=_option_index(MENTOR_STATUS_OPTIONS, str(inferred_defaults["mentor_status"])),
                        key=f"promote_mentor_{selected_candidate_id}",
                    )

                q1, q2, q3, q4 = st.columns(4)
                with q1:
                    employment = st.selectbox(
                        "Employment",
                        options=EMPLOYMENT_OPTIONS,
                        index=_option_index(EMPLOYMENT_OPTIONS, str(inferred_defaults["employment"])),
                        key=f"promote_employment_{selected_candidate_id}",
                    )
                with q2:
                    housing = st.selectbox(
                        "Housing",
                        options=HOUSING_OPTIONS,
                        index=_option_index(HOUSING_OPTIONS, str(inferred_defaults["housing"])),
                        key=f"promote_housing_{selected_candidate_id}",
                    )
                with q3:
                    placement_count = int(
                        st.number_input(
                            "Placement Count",
                            min_value=0,
                            max_value=20,
                            value=int(inferred_defaults["placement_count"]),
                            step=1,
                            key=f"promote_placement_{selected_candidate_id}",
                        )
                    )
                with q4:
                    prior_homelessness = st.selectbox(
                        "Prior Homelessness",
                        options=PRIOR_HOMELESSNESS_OPTIONS,
                        index=_option_index(PRIOR_HOMELESSNESS_OPTIONS, str(inferred_defaults["prior_homelessness"])),
                        key=f"promote_prior_homelessness_{selected_candidate_id}",
                    )

                submitted = st.form_submit_button("Promote Candidate To Teen", type="primary", use_container_width=True)

            if submitted:
                try:
                    with sqlite3.connect(db_path) as connection:
                        promote_candidate_to_youth(
                            connection,
                            candidate_profile_id=selected_candidate_id,
                            youth_id=youth_id,
                            age=age,
                            county=county,
                            education=education,
                            employment=employment,
                            housing=housing,
                            mentor_status=mentor_status,
                            placement_count=placement_count,
                            prior_homelessness=prior_homelessness,
                            first_name=first_name,
                            last_name=last_name,
                            caseworker_id=caseworker_id if assign_to_me else None,
                            case_priority=case_priority,
                        )
                        connection.commit()
                    st.session_state["selected_youth_id"] = youth_id.strip()
                    st.success(f"Candidate {selected_candidate_id} promoted to youth profile {youth_id.strip()}.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<span id="case-workspace" class="cw-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Case Management Workspace")

    selection_mode_col, selection_query_col = st.columns([1, 2])
    with selection_mode_col:
        selection_mode = st.radio(
            "Find Youth By",
            options=["Youth ID", "Name"],
            horizontal=True,
        )
    with selection_query_col:
        placeholder = "e.g., YP-0001" if selection_mode == "Youth ID" else "e.g., Aaliyah Smith"
        selection_query = st.text_input("Search My Assigned Youth", placeholder=placeholder)

    profile_picker_df = my_cases_df.copy()
    profile_picker_df["display_name"] = profile_picker_df["youth_id"].astype(str).map(youth_name_map)
    profile_picker_df["display_name"] = profile_picker_df["display_name"].fillna(profile_picker_df["youth_id"].astype(str))
    profile_picker_df["search_name"] = profile_picker_df["display_name"].str.lower()

    cleaned_selection_query = selection_query.strip().lower()
    if cleaned_selection_query:
        if selection_mode == "Youth ID":
            profile_picker_df = profile_picker_df[
                profile_picker_df["youth_id"].astype(str).str.lower().str.contains(cleaned_selection_query, na=False)
            ]
        else:
            profile_picker_df = profile_picker_df[
                profile_picker_df["search_name"].str.contains(cleaned_selection_query, na=False)
            ]

    if profile_picker_df.empty:
        st.info("No assigned youth matched your search.")
        return

    selected_case_label = st.selectbox(
        "Open Youth Profile",
        options=[
            f"{row['display_name']} ({row['youth_id']}) | {row['risk_level']} | {row['top_need_category']}"
            for _, row in profile_picker_df.iterrows()
        ],
        index=0,
    )
    selected_youth_id = selected_case_label.split("(", 1)[1].split(")", 1)[0]
    if st.session_state.get("selected_youth_id") in set(my_cases_df["youth_id"].astype(str)):
        selected_youth_id = str(st.session_state["selected_youth_id"])
        st.session_state.pop("selected_youth_id", None)
    selected_case_row = my_cases_df[my_cases_df["youth_id"] == selected_youth_id].iloc[0]

    with sqlite3.connect(db_path) as connection:
        profile_snapshot_df = load_youth_profile_snapshot(connection, selected_youth_id)

    st.markdown('<div class="cw-section-card">', unsafe_allow_html=True)
    st.markdown("#### Youth Profile Snapshot")
    if profile_snapshot_df.empty:
        st.info("No profile details found for this youth.")
    else:
        profile = profile_snapshot_df.iloc[0]
        full_name = f"{str(profile.get('first_name', '')).strip()} {str(profile.get('last_name', '')).strip()}".strip()
        c1, c2, c3 = st.columns(3)
        c1.write(f"Name: {full_name or youth_name_map.get(str(selected_youth_id), str(selected_youth_id))}")
        c1.write(f"Youth ID: {profile['youth_id']}")
        c1.write(f"Age: {int(profile['age']) if pd.notna(profile.get('age')) else '-'}")
        c2.write(f"County: {profile['county']}")
        c2.write(f"Education: {profile['education']}")
        c2.write(f"Employment: {profile['employment']}")
        c3.write(f"Housing: {profile['housing']}")
        c3.write(f"Mentor Status: {profile['mentor_status']}")
        c3.write(
            f"Placement Count: {int(profile['placement_count']) if pd.notna(profile.get('placement_count')) else '-'}"
        )
        c3.write(f"Prior Homelessness: {profile['prior_homelessness']}")
    st.markdown('</div>', unsafe_allow_html=True)
    st.caption("Profile details are now available directly in the caseworker workspace.")

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

    s4, s5 = st.columns([1, 2])
    confirm_reset = s4.checkbox("Confirm Reset", key=f"confirm_reset_intake_{selected_youth_id}")
    if s4.button("Reset Youth Intake", width="stretch"):
        if not confirm_reset:
            st.error("Check 'Confirm Reset' before clearing the intake.")
        else:
            with sqlite3.connect(db_path) as connection:
                reset_youth_intake(connection, selected_youth_id)
                connection.commit()
            st.success(f"Intake cleared for {selected_youth_id}. They can now retake the AI Assistant intake.")
            st.rerun()

    st.divider()
    insights_col, actions_col = st.columns([1.05, 1])

    with insights_col:
        st.markdown('<span id="ai-results" class="cw-anchor-target"></span>', unsafe_allow_html=True)
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
        st.markdown('<span id="resource-assignment" class="cw-anchor-target"></span>', unsafe_allow_html=True)
        st.subheader("Resource Assignment")
        approved_ids: list[str] = []
        rejected_ids: list[str] = []
        unassign_ids: list[str] = []
        overlap: list[str] = []
        with sqlite3.connect(db_path) as connection:
            recommendations_df = load_recommendations(connection, selected_youth_id)
            catalog_df = load_resource_catalog(connection)
            assigned_df = load_active_assigned_resources(connection, selected_youth_id)
            youth_name_for_email, youth_email = load_youth_contact(connection, selected_youth_id)

        if not recommendations_df.empty:
            st.caption("Recommended resources")
            st.dataframe(
                recommendations_df[
                    [
                        "resource_name",
                        "priority_rank",
                        "match_score",
                        "recommendation_status",
                        "recommendation_reason",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )

            rec_map = {
                f"{row['resource_name']} ({row['resource_id']})": str(row["resource_id"]) for _, row in recommendations_df.iterrows()
            }
            approved_labels = st.multiselect(
                "Approve Recommendations",
                options=list(rec_map.keys()),
                key=f"approve_recs_{selected_youth_id}",
            )
            rejected_labels = st.multiselect(
                "Reject Recommendations",
                options=list(rec_map.keys()),
                key=f"reject_recs_{selected_youth_id}",
            )
            approved_ids = [rec_map[label] for label in approved_labels]
            rejected_ids = [rec_map[label] for label in rejected_labels]

            overlap = sorted(set(approved_ids) & set(rejected_ids))
            if overlap:
                st.error("A resource cannot be both approved and rejected. Remove duplicates before applying.")

        if not assigned_df.empty:
            st.caption("Currently assigned resources")
            st.dataframe(
                assigned_df[["resource_name", "assignment_status", "assigned_at"]],
                hide_index=True,
                width="stretch",
            )

            assigned_map = {
                f"{row['resource_name']} ({row['resource_id']})": str(row["resource_id"])
                for _, row in assigned_df.iterrows()
            }
            unassign_labels = st.multiselect(
                "Unassign Resources",
                options=list(assigned_map.keys()),
                key=f"unassign_resources_{selected_youth_id}",
            )
            unassign_ids = [assigned_map[label] for label in unassign_labels]

        options_map = {f"{row['resource_name']} ({row['resource_id']})": str(row['resource_id']) for _, row in catalog_df.iterrows()}
        selected_labels = st.multiselect(
            "Replace / Add Resources (not in recommendations)",
            options=list(options_map.keys()),
        )
        selected_resource_ids = [options_map[label] for label in selected_labels]
        assign_priority = st.selectbox("Assignment Priority", ["High", "Medium", "Low"], index=1)
        assign_follow_up = st.date_input("Resource Follow-Up Date", value=date.today(), key="resource_follow_up")
        assign_note = st.text_area("Assignment Note", placeholder="Why this resource was selected")

        if st.button("Apply Recommendation Decisions", type="primary", width="stretch"):
            if recommendations_df.empty and not selected_resource_ids and not unassign_ids:
                st.warning("No recommendations, replacement resources, or unassignments were selected.")
            elif not recommendations_df.empty and overlap:
                st.error("Fix approval/rejection overlap before applying decisions.")
            else:
                combined_assignments = list(dict.fromkeys(approved_ids + selected_resource_ids))
                approved_set = set(approved_ids)
                replaced_set = set(selected_resource_ids)

                with sqlite3.connect(db_path) as connection:
                    unassigned_count = unassign_resources(
                        connection,
                        youth_id=selected_youth_id,
                        resource_ids=unassign_ids,
                    )
                    inserted, skipped = assign_resources(
                        connection,
                        youth_id=selected_youth_id,
                        caseworker_id=caseworker_id,
                        resource_ids=combined_assignments,
                        priority_level=assign_priority,
                        follow_up_date=assign_follow_up,
                        assignment_note=assign_note,
                    )

                    update_recommendation_statuses(
                        connection,
                        accepted_resource_ids=list(approved_set),
                        rejected_resource_ids=rejected_ids,
                        youth_id=selected_youth_id,
                    )

                    email_resource_rows: list[dict[str, str]] = []
                    for resource_id in combined_assignments:
                        resource_row = catalog_df[catalog_df["resource_id"] == resource_id]
                        if resource_row.empty:
                            continue
                        row = resource_row.iloc[0]
                        action_source = "approved recommendation" if resource_id in approved_set else "caseworker replacement"
                        email_resource_rows.append(
                            {
                                "resource_id": str(resource_id),
                                "resource_name": str(row["resource_name"]),
                                "category": str(row["category"]),
                                "next_step": f"Contact via referral method ({action_source}) within 3 business days",
                            }
                        )

                    if email_resource_rows:
                        youth_subject, youth_body, resource_drafts = build_outreach_email_drafts(
                            youth_name=youth_name_for_email,
                            youth_id=selected_youth_id,
                            caseworker_name=active_caseworker_name or caseworker_id,
                            resources=email_resource_rows,
                        )
                        save_outreach_email_draft(
                            connection,
                            youth_id=selected_youth_id,
                            resource_id=None,
                            recipient_role="youth",
                            recipient_name=youth_name_for_email,
                            recipient_email=youth_email,
                            subject=youth_subject,
                            body=youth_body,
                            caseworker_id=caseworker_id,
                        )

                        for resource_id, resource_name, subject, body in resource_drafts:
                            save_outreach_email_draft(
                                connection,
                                youth_id=selected_youth_id,
                                resource_id=resource_id,
                                recipient_role="resource_center",
                                recipient_name=resource_name,
                                recipient_email="",
                                subject=subject,
                                body=body,
                                caseworker_id=caseworker_id,
                            )

                    connection.commit()

                summary = [f"Assigned {inserted} resource(s)", f"skipped {skipped}", f"unassigned {unassigned_count}"]
                if combined_assignments:
                    summary.append("generated email drafts")
                st.success("Decisions applied: " + ", ".join(summary) + ".")
                st.rerun()

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
    st.markdown('<span id="outreach-queue" class="cw-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Outreach Email Queue")
    with sqlite3.connect(db_path) as connection:
        outreach_df = load_outreach_emails(connection, selected_youth_id)

    if outreach_df.empty:
        st.info("No outreach drafts found yet. Apply recommendation decisions to generate drafts.")
    else:
        st.dataframe(
            outreach_df[["email_id", "recipient_role", "recipient_name", "recipient_email", "subject", "delivery_status", "created_at", "sent_at"]],
            hide_index=True,
            width="stretch",
        )

        draft_ids = outreach_df[outreach_df["delivery_status"].isin(["draft", "queued", "failed"])]["email_id"].astype(int).tolist()
        if draft_ids:
            selected_email_id = st.selectbox("Select Email Draft", options=draft_ids, index=0)
            selected_email = outreach_df[outreach_df["email_id"] == selected_email_id].iloc[0]
            st.caption(f"Preview: {selected_email['subject']}")
            st.text_area("Email Body", value=str(selected_email["body"]), height=170, disabled=True)

            target_email = st.text_input(
                "Recipient Email Override",
                value=str(selected_email["recipient_email"]),
                key=f"recipient_override_{selected_email_id}",
                help="Use this when draft recipient email is blank or needs correction.",
            ).strip()

            em1, em2 = st.columns(2)
            if em1.button("Mark Draft as Sent", width="stretch"):
                with sqlite3.connect(db_path) as connection:
                    update_outreach_email_status(connection, int(selected_email_id), "sent")
                    connection.commit()
                st.success(f"Email draft {selected_email_id} marked as sent.")
                st.rerun()

            if em2.button("Send Now via SMTP", type="primary", width="stretch"):
                if not smtp_host or not smtp_sender:
                    st.error("SMTP Host and From Email are required to send email.")
                elif not target_email:
                    st.error("Recipient email is required. Enter or override recipient email.")
                else:
                    try:
                        send_email_via_smtp(
                            smtp_host=smtp_host,
                            smtp_port=smtp_port,
                            smtp_username=smtp_username,
                            smtp_password=smtp_password,
                            smtp_use_tls=smtp_use_tls,
                            sender_email=smtp_sender,
                            recipient_email=target_email,
                            subject=str(selected_email["subject"]),
                            body=str(selected_email["body"]),
                        )
                        with sqlite3.connect(db_path) as connection:
                            connection.execute(
                                """
                                UPDATE outreach_emails
                                SET recipient_email = ?
                                WHERE email_id = ?
                                """,
                                (target_email, int(selected_email_id)),
                            )
                            update_outreach_email_status(connection, int(selected_email_id), "sent")
                            connection.commit()
                        st.success(f"Email draft {selected_email_id} sent and marked as sent.")
                        st.rerun()
                    except Exception as error:
                        with sqlite3.connect(db_path) as connection:
                            update_outreach_email_status(connection, int(selected_email_id), "failed", last_error=str(error))
                            connection.commit()
                        st.error(f"SMTP send failed: {error}")
        else:
            st.caption("No pending drafts. All outreach emails are already marked sent.")

    st.divider()
    st.markdown('<span id="follow-up-tracker" class="cw-anchor-target"></span>', unsafe_allow_html=True)
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
