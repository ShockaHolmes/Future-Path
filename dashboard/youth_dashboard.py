from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from assign_resources_from_intake import assign_resources_from_intake, ensure_assigned_resources_table_integrity
from dashboard_server_manager import ensure_single_dashboard, switch_dashboard
from future_path_ai_intake import QUESTIONS, infer_summary_needs, resolve_profile_link, save_answer
from future_path_ai_intake import ensure_intake_tables as ensure_intake_tables_base


DEFAULT_DB_PATH = Path("database/future_path.db")
OVERVIEW_URL = "http://localhost:8501"
PROFILE_LOOKUP_URL = "http://localhost:8502"
AI_ASSISTANT_URL = "http://localhost:8503"
CASEWORKER_URL = "http://localhost:8504"
YOUTH_DASHBOARD_URL = "http://localhost:8505"


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_youth_portal_tables(connection: sqlite3.Connection) -> None:
    ensure_intake_tables_base(connection)
    ensure_assigned_resources_table_integrity(connection)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS help_requests (
            help_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            caseworker_id TEXT,
            request_text TEXT NOT NULL,
            urgency TEXT NOT NULL CHECK (urgency IN ('Low', 'Medium', 'High', 'Urgent')),
            preferred_contact TEXT NOT NULL CHECK (preferred_contact IN ('Phone', 'Text', 'Email', 'In person')),
            request_status TEXT NOT NULL DEFAULT 'submitted' CHECK (
                request_status IN ('submitted', 'in_review', 'responded', 'closed')
            ),
            requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            responded_at TEXT,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (caseworker_id) REFERENCES caseworkers(caseworker_id) ON DELETE SET NULL
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_help_requests_youth_id ON help_requests(youth_id)")


def initialize_state() -> None:
    defaults = {
        "youth_selected_id": "",
        "youth_intake_started": False,
        "youth_intake_completed": False,
        "youth_session_id": "",
        "youth_current_index": 0,
        "youth_answers": {},
        "youth_selected_choice": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_intake_state() -> None:
    st.session_state["youth_intake_started"] = False
    st.session_state["youth_intake_completed"] = False
    st.session_state["youth_session_id"] = ""
    st.session_state["youth_current_index"] = 0
    st.session_state["youth_answers"] = {}
    st.session_state["youth_selected_choice"] = ""


def render_top_navigation(current_page: str) -> None:
    buttons = [
        ("Overview", "overview"),
        ("Youth Dashboard", "youth_dashboard"),
        ("Youth Profiles", "profile_lookup"),
        ("AI Assistant", "ai_assistant"),
        ("Caseworker Dashboard", "caseworker_dashboard"),
    ]
    cols = st.columns(5)
    for idx, (label, page_key) in enumerate(buttons):
        with cols[idx]:
            if page_key == current_page:
                st.button(label, use_container_width=True, disabled=True, key=f"topnav_disabled_{current_page}_{page_key}")
            else:
                if st.button(label, use_container_width=True, key=f"topnav_switch_{current_page}_{page_key}"):
                    next_url = switch_dashboard(page_key, current_key=current_page)
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800&family=Plus+Jakarta+Sans:wght@500;600;700&display=swap');

        .stApp {
            background: linear-gradient(180deg, #f8fbff 0%, #f3f7ff 100%);
            color: #102a78;
            font-family: 'Plus Jakarta Sans', sans-serif;
        }

        [data-testid='stSidebar'] {
            background: linear-gradient(180deg, #f8fbff 0%, #f1f6ff 100%) !important;
            color: #16356c !important;
            border-right: 1px solid #d9e5fb !important;
        }

        [data-testid='stSidebar'] * {
            color: #173775 !important;
        }

        [data-testid='stSidebar'] div[data-baseweb='input'] > div,
        [data-testid='stSidebar'] div[data-baseweb='select'] > div,
        [data-testid='stSidebar'] div[data-baseweb='textarea'] > div {
            background: #ffffff !important;
            border: 1px solid #cfe0f5 !important;
            color: #173775 !important;
        }

        [data-testid='stSidebar'] input,
        [data-testid='stSidebar'] textarea,
        [data-testid='stSidebar'] span {
            color: #173775 !important;
            opacity: 1 !important;
        }

        .main .block-container {
            max-width: 1180px;
            padding-top: 1.1rem;
            padding-bottom: 2rem;
        }

        .youth-header {
            border: 1px solid #d9e6ff;
            border-radius: 18px;
            background: #ffffff;
            padding: 14px 16px;
            margin: 0.8rem 0;
        }

        .youth-title {
            color: #12327f;
            font-family: 'Manrope', sans-serif;
            font-size: 1.8rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .youth-subtitle {
            color: #244780;
            font-size: 0.96rem;
            margin-top: 6px;
        }

        .youth-section-card {
            border: 1px solid #dce7ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 14px;
            margin-bottom: 0.85rem;
        }

        .youth-kpi {
            border: 1px solid #e2ebff;
            border-radius: 14px;
            background: #fbfdff;
            padding: 12px;
            min-height: 86px;
        }

        .youth-kpi-label {
            color: #2c4d90;
            font-size: 0.86rem;
            font-weight: 700;
        }

        .youth-kpi-value {
            color: #0f2f74;
            font-family: 'Manrope', sans-serif;
            font-size: 1.55rem;
            font-weight: 800;
            margin-top: 4px;
        }

        .youth-resource-chip {
            display: inline-block;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 0.78rem;
            font-weight: 800;
        }

        .status-recommended { background: #eef4ff; color: #20438a; border: 1px solid #cfdbf7; }
        .status-assigned { background: #ecf8f0; color: #1f6d46; border: 1px solid #ccead8; }
        .status-contacted { background: #fff7ea; color: #a36510; border: 1px solid #f0d6ab; }
        .status-completed { background: #eefaf4; color: #1f7b4f; border: 1px solid #ccead8; }

        .stSelectbox label,
        .stTextInput label,
        .stTextArea label,
        .stRadio label,
        .stCaption,
        .stMarkdown p,
        .stMarkdown li {
            color: #1f3f7e !important;
            opacity: 1 !important;
        }

        [data-testid='stRadio'] [role='radiogroup'] label,
        [data-testid='stRadio'] [role='radiogroup'] label p,
        [data-testid='stRadio'] [role='radiogroup'] label span,
        [data-baseweb='radio'] label,
        [data-baseweb='radio'] label p,
        [data-baseweb='radio'] label span {
            color: #133a84 !important;
            opacity: 1 !important;
            font-weight: 600 !important;
        }

        [data-testid='stRadio'] [role='radiogroup'] label:hover,
        [data-baseweb='radio'] label:hover {
            background: #f2f7ff !important;
            border-radius: 10px;
        }

        [data-baseweb='radio'] input:checked + div,
        [data-testid='stRadio'] input:checked + div {
            border-color: #0f8293 !important;
            box-shadow: inset 0 0 0 4px #0f8293 !important;
        }

        .stButton > button {
            border: 1px solid #ccd9f6 !important;
            border-radius: 12px !important;
            background: #eef4ff !important;
            color: #173b80 !important;
            font-weight: 700 !important;
        }

        .stButton > button[kind='primary'] {
            background: #0f8293 !important;
            color: #ffffff !important;
            border-color: #0f8293 !important;
        }

        [data-testid='stDataFrame'] [role='columnheader'],
        [data-testid='stDataFrame'] [role='gridcell'] {
            color: #102a78 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def load_youth_options(connection: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(connection, "youth_profiles"):
        return pd.DataFrame(columns=["youth_id", "display_name"])

    if table_exists(connection, "caseworker_youth"):
        frame = pd.read_sql_query(
            """
            SELECT
                yp.youth_id,
                COALESCE(NULLIF(TRIM(cy.first_name || ' ' || cy.last_name), ''), yp.youth_id) AS display_name
            FROM youth_profiles yp
            LEFT JOIN caseworker_youth cy ON cy.youth_id = yp.youth_id
            ORDER BY yp.youth_id
            """,
            connection,
        )
    else:
        frame = pd.read_sql_query(
            """
            SELECT youth_id, youth_id AS display_name
            FROM youth_profiles
            ORDER BY youth_id
            """,
            connection,
        )

    frame["youth_id"] = frame["youth_id"].astype(str)

    if table_exists(connection, "intake_sessions"):
        intake_status = pd.read_sql_query(
            """
            SELECT latest.youth_id, latest.session_status
            FROM intake_sessions latest
            INNER JOIN (
                SELECT youth_id, MAX(intake_session_id) AS latest_session_id
                FROM intake_sessions
                WHERE profile_type = 'youth'
                GROUP BY youth_id
            ) recent
                ON recent.youth_id = latest.youth_id
               AND recent.latest_session_id = latest.intake_session_id
            WHERE latest.youth_id IS NOT NULL
            """,
            connection,
        )
        if not intake_status.empty:
            intake_status["youth_id"] = intake_status["youth_id"].astype(str)
            frame = frame.merge(intake_status, on="youth_id", how="left")

    if "session_status" not in frame.columns:
        frame["session_status"] = ""

    def build_display_option(row: pd.Series) -> str:
        intake_badge = " [AI Intake Complete]" if str(row.get("session_status") or "").lower() == "completed" else ""
        return f"{row['display_name']}{intake_badge} ({row['youth_id']})"

    frame["display_option"] = frame.apply(build_display_option, axis=1)
    return frame


def profile_exists(connection: sqlite3.Connection, youth_id: str) -> tuple[bool, str]:
    if not youth_id.strip():
        return False, "Please choose a youth profile first."
    row = connection.execute("SELECT 1 FROM youth_profiles WHERE youth_id = ?", (youth_id.strip(),)).fetchone()
    if row is None:
        return False, f"Youth profile not found: {youth_id.strip()}"
    return True, ""


def start_intake_session(connection: sqlite3.Connection, session_id: str, youth_id: str) -> None:
    ensure_intake_tables_base(connection)
    resolved_type, resolved_youth_id, resolved_candidate_id = resolve_profile_link(connection, youth_id.strip(), None)
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
        ) VALUES (?, ?, ?, ?, 'in_progress', 'future_path_youth_dashboard_v1', 'youth_portal', NULL)
        ON CONFLICT(intake_session_id) DO UPDATE SET
            youth_id = excluded.youth_id,
            candidate_profile_id = excluded.candidate_profile_id,
            profile_type = excluded.profile_type,
            session_status = 'in_progress',
            assistant_version = excluded.assistant_version,
            channel = excluded.channel,
            top_need_category = excluded.top_need_category
        """,
        (session_id, resolved_youth_id, resolved_candidate_id, resolved_type),
    )


def complete_intake_session(connection: sqlite3.Connection, session_id: str, answers: dict[str, str]) -> list[str]:
    summary_needs = infer_summary_needs(answers)
    top_need_category = answers.get("primary_need") or "general_support"
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
    return summary_needs


def load_assigned_resources(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "assigned_resources"):
        return pd.DataFrame()

    has_resources = table_exists(connection, "resources")
    if has_resources:
        query = """
            SELECT
                ar.resource_id,
                COALESCE(r.resource_name, ar.resource_id) AS resource_name,
                COALESCE(r.category, 'General Support') AS category,
                COALESCE(r.referral_method, 'Contact your caseworker for referral instructions') AS referral_method,
                ar.priority_level,
                COALESCE(ar.match_score, 0.0) AS match_score,
                COALESCE(ar.match_reason, '') AS reason,
                ar.assignment_status,
                ar.assigned_at,
                COALESCE(ar.follow_up_date, '') AS follow_up_date
            FROM assigned_resources ar
            LEFT JOIN resources r ON r.resource_id = ar.resource_id
            WHERE ar.youth_id = ?
            ORDER BY COALESCE(ar.assigned_at, '') DESC, ar.assignment_id DESC
            LIMIT 30
        """
    else:
        query = """
            SELECT
                resource_id,
                resource_id AS resource_name,
                'General Support' AS category,
                'Contact your caseworker for referral instructions' AS referral_method,
                priority_level,
                COALESCE(match_score, 0.0) AS match_score,
                COALESCE(match_reason, '') AS reason,
                assignment_status,
                assigned_at,
                COALESCE(follow_up_date, '') AS follow_up_date
            FROM assigned_resources
            WHERE youth_id = ?
            ORDER BY COALESCE(assigned_at, '') DESC, assignment_id DESC
            LIMIT 30
        """
    return pd.read_sql_query(query, connection, params=[youth_id])


def load_recommended_resources(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "recommendations"):
        return pd.DataFrame()

    has_resources = table_exists(connection, "resources")
    if has_resources:
        query = """
            SELECT
                rec.resource_id,
                COALESCE(r.resource_name, rec.resource_id) AS resource_name,
                COALESCE(rec.match_score, 0.0) AS match_score,
                COALESCE(rec.recommendation_reason, '') AS reason,
                COALESCE(rec.recommendation_status, 'proposed') AS recommendation_status,
                rec.created_at
            FROM recommendations rec
            LEFT JOIN resources r ON r.resource_id = rec.resource_id
            WHERE rec.youth_id = ?
            ORDER BY COALESCE(rec.created_at, '') DESC, rec.recommendation_id DESC
            LIMIT 20
        """
    else:
        query = """
            SELECT
                resource_id,
                resource_id AS resource_name,
                COALESCE(match_score, 0.0) AS match_score,
                COALESCE(recommendation_reason, '') AS reason,
                COALESCE(recommendation_status, 'proposed') AS recommendation_status,
                created_at
            FROM recommendations
            WHERE youth_id = ?
            ORDER BY COALESCE(created_at, '') DESC, recommendation_id DESC
            LIMIT 20
        """
    return pd.read_sql_query(query, connection, params=[youth_id])


def load_caseworker_contact(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "case_assignments") or not table_exists(connection, "caseworkers"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT
            ca.caseworker_id,
            cw.full_name,
            COALESCE(cw.email, '') AS email,
            ca.case_status,
            ca.priority_level,
            COALESCE(ca.next_follow_up_date, '') AS next_follow_up_date,
            ca.assigned_at
        FROM case_assignments ca
        JOIN caseworkers cw ON cw.caseworker_id = ca.caseworker_id
        WHERE ca.youth_id = ?
        ORDER BY COALESCE(ca.last_updated_at, ca.assigned_at, '') DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )


def load_follow_ups(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "follow_ups"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT
            follow_up_date,
            follow_up_status,
            COALESCE(details, '') AS details,
            created_at
        FROM follow_ups
        WHERE youth_id = ?
        ORDER BY COALESCE(follow_up_date, '') ASC, follow_up_id DESC
        LIMIT 20
        """,
        connection,
        params=[youth_id],
    )


def load_latest_risk_score(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "risk_scores"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT risk_level, overall_risk_score, calculated_at
        FROM risk_scores
        WHERE youth_id = ?
        ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )


def load_latest_intake_answers(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "intake_sessions") or not table_exists(connection, "intake_answers"):
        return pd.DataFrame()

    session = connection.execute(
        """
        SELECT intake_session_id
        FROM intake_sessions
        WHERE youth_id = ?
          AND profile_type = 'youth'
          AND session_status = 'completed'
        ORDER BY COALESCE(completed_at, started_at, '') DESC
        LIMIT 1
        """,
        (youth_id,),
    ).fetchone()
    if session is None:
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT question_key, answer_value
        FROM intake_answers
        WHERE intake_session_id = ?
        ORDER BY intake_answer_id ASC
        """,
        connection,
        params=[str(session[0])],
    )


def load_latest_intake_session(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "intake_sessions"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT intake_session_id, session_status, started_at, completed_at, top_need_category
        FROM intake_sessions
        WHERE youth_id = ?
          AND profile_type = 'youth'
        ORDER BY COALESCE(completed_at, started_at, '') DESC, intake_session_id DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )


def load_help_requests(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "help_requests"):
        return pd.DataFrame()

    return pd.read_sql_query(
        """
        SELECT request_text, urgency, preferred_contact, request_status, requested_at
        FROM help_requests
        WHERE youth_id = ?
        ORDER BY COALESCE(requested_at, '') DESC, help_request_id DESC
        LIMIT 20
        """,
        connection,
        params=[youth_id],
    )


def submit_help_request(
    connection: sqlite3.Connection,
    youth_id: str,
    caseworker_id: str | None,
    request_text: str,
    urgency: str,
    preferred_contact: str,
) -> None:
    connection.execute(
        """
        INSERT INTO help_requests (
            youth_id,
            caseworker_id,
            request_text,
            urgency,
            preferred_contact,
            request_status,
            requested_at
        ) VALUES (?, ?, ?, ?, ?, 'submitted', ?)
        """,
        (
            youth_id,
            caseworker_id,
            request_text.strip(),
            urgency,
            preferred_contact,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )


def status_chip_class(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"assigned", "in_progress"}:
        return "status-assigned"
    if normalized in {"contacted", "reviewed"}:
        return "status-contacted"
    if normalized in {"completed", "accepted", "closed"}:
        return "status-completed"
    return "status-recommended"


def format_question_option_label(question_key: str, value: str) -> str:
    mapping = {
        "housing_status": {
            "stable": "I have a stable place lined up",
            "temporary": "I am in temporary housing",
            "couch_surfing": "I am couch surfing",
            "shelter": "I am staying in a shelter",
            "at_risk": "I am at risk of homelessness",
        },
        "employment_status": {
            "full_time": "I am employed full-time",
            "part_time": "I am employed part-time",
            "unemployed": "I am currently unemployed",
            "training": "I am in training or internship",
            "seasonal": "I have seasonal work",
        },
        "education_status": {
            "in_school": "I am currently in school",
            "diploma_or_ged": "I completed diploma or GED",
            "no_diploma_or_ged": "I do not have a diploma or GED",
            "postsecondary": "I am in postsecondary education",
            "not_enrolled": "I am not enrolled right now",
        },
        "transportation_access": {
            "reliable": "My transportation is reliable",
            "limited": "My transportation is limited",
            "none": "I have no reliable transportation",
        },
        "food_access": {
            "yes": "Yes, I have consistent food access",
            "sometimes": "Sometimes, but not always",
            "no": "No, I need food support",
        },
        "health_wellness_need": {
            "yes": "Yes, I need health or wellness support",
            "no": "No, I do not need support right now",
        },
        "documents_status": {
            "all": "I have all key documents",
            "some": "I have some documents",
            "none": "I do not have my key documents",
        },
        "support_system": {
            "strong": "I have a strong support system",
            "limited": "My support system is limited",
            "none": "I do not have a support system right now",
        },
        "safety_concern": {
            "yes": "Yes, I have an immediate safety concern",
            "no": "No, I do not have an immediate safety concern",
        },
        "primary_need": {
            "housing": "Housing stability",
            "employment": "Employment",
            "education": "Education",
            "transportation": "Transportation",
            "food": "Food access",
            "health_wellness": "Health and wellness",
            "documents": "ID and documents",
            "support_system": "Support system",
            "safety": "Safety",
        },
    }
    if question_key in mapping and value in mapping[question_key]:
        return mapping[question_key][value]
    return value.replace("_", " ").title()


def render_intake_flow(connection: sqlite3.Connection, youth_id: str, intake_locked: bool, completed_at_label: str) -> None:
    st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
    st.subheader("Start AI Assistant")
    st.write("Answer a few quick questions so we can tailor support for you.")

    total_questions = len(QUESTIONS)

    if intake_locked:
        reset_intake_state()
        st.success(f"AI Intake already completed{completed_at_label}. Your support plan is active below.")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    if not st.session_state["youth_intake_started"]:
        if st.button("Start AI Assistant", type="primary", width="stretch"):
            session_id = f"intake-{uuid4()}"
            start_intake_session(connection, session_id, youth_id)
            st.session_state["youth_session_id"] = session_id
            st.session_state["youth_intake_started"] = True
            st.session_state["youth_intake_completed"] = False
            st.session_state["youth_current_index"] = 0
            st.session_state["youth_answers"] = {}
            st.session_state["youth_selected_choice"] = ""
            connection.commit()
            st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)
        return

    current_index = st.session_state["youth_current_index"]
    progress_pct = int((current_index / total_questions) * 100)
    st.progress(max(progress_pct, 1), text=f"Question {min(current_index + 1, total_questions)} of {total_questions}")

    if st.session_state["youth_intake_completed"]:
        st.success("Thanks, your intake is complete. Your recommendations are updated below.")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    question = QUESTIONS[current_index]
    question_key = question["key"]
    st.markdown(f"**{question['prompt']}**")

    default_choice = st.session_state.get("youth_selected_choice") or question["options"][0]
    choice = st.radio(
        "Choose one",
        options=question["options"],
        horizontal=False,
        format_func=lambda value: format_question_option_label(question_key, value),
        index=question["options"].index(default_choice) if default_choice in question["options"] else 0,
        key=f"youth_question_{current_index}",
    )

    action_col1, action_col2 = st.columns(2)
    with action_col1:
        next_pressed = st.button("Save and Continue", type="primary", width="stretch")
    with action_col2:
        cancel_pressed = st.button("Stop Intake", width="stretch")

    if cancel_pressed:
        session_id = st.session_state["youth_session_id"]
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
        reset_intake_state()
        st.warning("Intake stopped. You can restart anytime.")
        st.rerun()

    if not next_pressed:
        st.markdown('</div>', unsafe_allow_html=True)
        return

    session_id = st.session_state["youth_session_id"]
    save_answer(connection, session_id, question_key, question["prompt"], choice)
    st.session_state["youth_answers"][question_key] = choice

    next_index = current_index + 1
    if next_index < total_questions:
        st.session_state["youth_current_index"] = next_index
        connection.commit()
        st.rerun()

    complete_intake_session(connection, session_id, st.session_state["youth_answers"])
    assign_resources_from_intake(
        connection,
        intake_session_id=session_id,
        top_n=5,
        assigned_by="youth_dashboard_v1",
    )
    connection.commit()

    st.session_state["youth_intake_completed"] = True
    st.success("Intake complete. Your support plan has been refreshed.")
    st.rerun()


def render() -> None:
    st.set_page_config(page_title="Future Path Youth Dashboard", page_icon="FP", layout="wide")
    ensure_single_dashboard("youth_dashboard")
    initialize_state()
    inject_styles()

    render_top_navigation("youth_dashboard")

    st.markdown(
        """
        <div class="youth-header">
            <div class="youth-title">Welcome to Future Path</div>
            <div class="youth-subtitle">Complete your intake, track your support plan, and stay connected with your caseworker.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()
    st.sidebar.caption("Use your youth profile to view resources, next steps, and follow-ups.")

    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the data pipeline first, then return to this page.")
        return

    with sqlite3.connect(db_path) as setup_connection:
        setup_connection.row_factory = sqlite3.Row
        ensure_youth_portal_tables(setup_connection)
        setup_connection.commit()

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        youth_options = load_youth_options(connection)

    if youth_options.empty:
        st.warning("No youth profiles found yet.")
        return

    selected_option = st.selectbox("Select your profile", options=youth_options["display_option"].tolist(), index=0)
    selected_youth_id = selected_option.rsplit("(", 1)[1].rstrip(")")

    if st.session_state.get("youth_selected_id") != selected_youth_id:
        st.session_state["youth_selected_id"] = selected_youth_id
        reset_intake_state()

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        ok, message = profile_exists(connection, selected_youth_id)
        if not ok:
            st.error(message)
            return

        assigned_df = load_assigned_resources(connection, selected_youth_id)
        recommended_df = load_recommended_resources(connection, selected_youth_id)
        followups_df = load_follow_ups(connection, selected_youth_id)
        caseworker_df = load_caseworker_contact(connection, selected_youth_id)
        risk_df = load_latest_risk_score(connection, selected_youth_id)
        intake_session_df = load_latest_intake_session(connection, selected_youth_id)
        answers_df = load_latest_intake_answers(connection, selected_youth_id)
        help_df = load_help_requests(connection, selected_youth_id)

    top_needs = infer_summary_needs(dict(zip(answers_df.get("question_key", pd.Series(dtype=str)), answers_df.get("answer_value", pd.Series(dtype=str)))))
    completed_count = int((assigned_df["assignment_status"].str.lower().isin(["completed", "closed"]).sum()) if not assigned_df.empty else 0)
    total_assigned = int(len(assigned_df))
    progress_pct = int((completed_count / total_assigned) * 100) if total_assigned else 0

    next_follow_up = "Not scheduled"
    if not followups_df.empty:
        upcoming = followups_df.copy()
        upcoming["follow_up_date"] = pd.to_datetime(upcoming["follow_up_date"], errors="coerce")
        upcoming = upcoming[upcoming["follow_up_date"].notna()]
        upcoming = upcoming.sort_values("follow_up_date")
        if not upcoming.empty:
            next_follow_up = upcoming.iloc[0]["follow_up_date"].strftime("%b %d, %Y")

    risk_level = "Unknown"
    if not risk_df.empty:
        risk_level = str(risk_df.iloc[0]["risk_level"])

    intake_status = "Not Started"
    intake_completed_label = ""
    intake_locked = False
    if not intake_session_df.empty:
        latest_intake = intake_session_df.iloc[0]
        intake_status = str(latest_intake.get("session_status") or "Not Started").replace("_", " ").title()
        completed_value = str(latest_intake.get("completed_at") or "").strip()
        if completed_value:
            intake_completed_label = f" on {completed_value[:10]}"
        intake_locked = str(latest_intake.get("session_status") or "").lower() == "completed"
        if intake_locked:
            st.session_state["youth_intake_completed"] = True

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(
            f"""
            <div class="youth-kpi">
                <div class="youth-kpi-label">Progress Toward Goals</div>
                <div class="youth-kpi-value">{progress_pct}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k2:
        st.markdown(
            f"""
            <div class="youth-kpi">
                <div class="youth-kpi-label">Assigned Resources</div>
                <div class="youth-kpi-value">{total_assigned}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k3:
        st.markdown(
            f"""
            <div class="youth-kpi">
                <div class="youth-kpi-label">AI Intake Status</div>
                <div class="youth-kpi-value" style="font-size:1.2rem;">{intake_status}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k4:
        st.markdown(
            f"""
            <div class="youth-kpi">
                <div class="youth-kpi-label">Current Risk Level</div>
                <div class="youth-kpi-value">{risk_level}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with k5:
        st.markdown(
            f"""
            <div class="youth-kpi">
                <div class="youth-kpi-label">Next Follow-Up</div>
                <div class="youth-kpi-value" style="font-size:1.15rem;">{next_follow_up}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with sqlite3.connect(db_path) as intake_connection:
        intake_connection.row_factory = sqlite3.Row
        render_intake_flow(intake_connection, selected_youth_id, intake_locked, intake_completed_label)

    left_col, right_col = st.columns([1.35, 1])

    with left_col:
        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("My Top Needs")
        if top_needs:
            for need in top_needs[:5]:
                st.markdown(f"- {need}")
        else:
            st.caption("Complete the intake to generate personalized top needs.")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("My Assigned Resources")
        if assigned_df.empty:
            st.info("No assigned resources yet. Complete intake to generate your support plan.")
        else:
            for _, row in assigned_df.head(10).iterrows():
                status = str(row["assignment_status"]).replace("_", " ").title()
                chip_class = status_chip_class(str(row["assignment_status"]))
                follow_up = str(row.get("follow_up_date", "")).strip()
                follow_up_text = f" by {follow_up}" if follow_up else ""
                next_step = f"{row['referral_method']}{follow_up_text}."
                st.markdown(
                    f"""
                    <div style="border:1px solid #dce7ff;border-radius:12px;padding:10px 12px;margin-bottom:10px;background:#fff;">
                        <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
                            <div style="font-weight:800;color:#12367f;">{row['resource_name']}</div>
                            <span class="youth-resource-chip {chip_class}">{status}</span>
                        </div>
                        <div style="color:#304f88;font-size:0.9rem;margin-top:4px;">Category: {row['category']} | Priority: {row['priority_level']} | Match score: {float(row['match_score']):.1f}</div>
                        <div style="color:#1f3f7e;font-size:0.9rem;margin-top:6px;">Why this was assigned: {row['reason'] or 'Matched to your intake needs.'}</div>
                        <div style="color:#1f3f7e;font-size:0.9rem;margin-top:6px;"><strong>Next Step:</strong> {next_step}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("My Next Steps")
        steps: list[str] = []
        if answers_df.empty:
            steps.append("Complete your AI Assistant intake so we can personalize support.")
        if not assigned_df.empty:
            in_progress = assigned_df[assigned_df["assignment_status"].str.lower().isin(["assigned", "in_progress"])].head(3)
            for _, row in in_progress.iterrows():
                steps.append(f"Contact {row['resource_name']} and mention your Future Path referral.")
        if next_follow_up != "Not scheduled":
            steps.append(f"Prepare for your next follow-up on {next_follow_up}.")
        if caseworker_df.empty:
            steps.append("Submit a help request so a caseworker can be assigned or respond.")

        if not steps:
            steps = ["Keep checking your dashboard and complete any open tasks."]

        for step in steps[:6]:
            st.markdown(f"- {step}")
        st.markdown('</div>', unsafe_allow_html=True)

    with right_col:
        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("My Caseworker")
        caseworker_id_for_request: str | None = None
        if caseworker_df.empty:
            st.info("A caseworker has not been assigned yet.")
        else:
            row = caseworker_df.iloc[0]
            caseworker_id_for_request = str(row["caseworker_id"])
            st.markdown(f"**{row['full_name']}**")
            st.write(f"Caseworker ID: {row['caseworker_id']}")
            st.write(f"Email: {row['email'] or 'Not available'}")
            st.write(f"Case Status: {str(row['case_status']).replace('_', ' ').title()}")
            if row["next_follow_up_date"]:
                st.write(f"Next Follow-Up: {row['next_follow_up_date']}")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("Request Help From Caseworker")
        with st.form("youth_help_request_form", clear_on_submit=True):
            urgency = st.selectbox("Urgency", options=["Low", "Medium", "High", "Urgent"], index=1)
            preferred_contact = st.selectbox("Preferred contact method", options=["Phone", "Text", "Email", "In person"], index=1)
            request_text = st.text_area("What do you need help with?", height=120, placeholder="Example: I need help contacting a housing program.")
            submitted = st.form_submit_button("Send Help Request", type="primary", width="stretch")

        if submitted:
            if not request_text.strip():
                st.error("Please add a short message so your caseworker knows how to help.")
            else:
                with sqlite3.connect(db_path) as submit_connection:
                    submit_connection.row_factory = sqlite3.Row
                    ensure_youth_portal_tables(submit_connection)
                    submit_help_request(
                        submit_connection,
                        youth_id=selected_youth_id,
                        caseworker_id=caseworker_id_for_request,
                        request_text=request_text,
                        urgency=urgency,
                        preferred_contact=preferred_contact,
                    )
                    submit_connection.commit()
                st.success("Your help request was sent. A caseworker will follow up.")
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
        st.subheader("Upcoming Follow-Ups")
        if followups_df.empty:
            st.caption("No follow-ups scheduled yet.")
        else:
            st.dataframe(
                followups_df[["follow_up_date", "follow_up_status", "details"]].rename(
                    columns={
                        "follow_up_date": "Date",
                        "follow_up_status": "Status",
                        "details": "Details",
                    }
                ),
                hide_index=True,
                width="stretch",
            )
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
    st.subheader("Resource Status Tracker")
    status_rows: list[dict[str, str]] = []
    if not recommended_df.empty:
        for _, row in recommended_df.iterrows():
            status_rows.append(
                {
                    "Resource": str(row["resource_name"]),
                    "Status": "Recommended",
                    "Why": str(row["reason"] or "Matched to your intake profile."),
                }
            )
    if not assigned_df.empty:
        for _, row in assigned_df.iterrows():
            status = str(row["assignment_status"]).replace("_", " ").title()
            if status.lower() == "in progress":
                status = "Contacted"
            status_rows.append(
                {
                    "Resource": str(row["resource_name"]),
                    "Status": status,
                    "Why": str(row["reason"] or "Assigned by your support team."),
                }
            )

    if not status_rows:
        st.caption("No resource updates yet. Start the AI Assistant intake to build your plan.")
    else:
        tracker_df = pd.DataFrame(status_rows).drop_duplicates(subset=["Resource", "Status", "Why"])
        st.dataframe(tracker_df, hide_index=True, width="stretch")
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="youth-section-card">', unsafe_allow_html=True)
    st.subheader("My Help Requests")
    if help_df.empty:
        st.caption("No help requests submitted yet.")
    else:
        st.dataframe(
            help_df.rename(
                columns={
                    "request_text": "Request",
                    "urgency": "Urgency",
                    "preferred_contact": "Preferred Contact",
                    "request_status": "Status",
                    "requested_at": "Requested At",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    render()
