from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st


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


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def load_profiles(connection: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(connection, "youth_profiles"):
        return pd.DataFrame()

    has_caseworker = table_exists(connection, "caseworker_youth")
    if has_caseworker:
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
                cw.first_name,
                cw.last_name
            FROM youth_profiles yp
            LEFT JOIN caseworker_youth cw ON cw.youth_id = yp.youth_id
            ORDER BY yp.youth_id
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
                NULL AS first_name,
                NULL AS last_name
            FROM youth_profiles
            ORDER BY youth_id
        """

    frame = pd.read_sql_query(query, connection)
    frame["full_name"] = (
        frame["first_name"].fillna("").str.strip() + " " + frame["last_name"].fillna("").str.strip()
    ).str.strip()
    frame["search_name"] = frame["full_name"].str.lower()
    return frame


def load_latest_risk_score(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "risk_scores"):
        return pd.DataFrame()

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


def load_recommendations(connection: sqlite3.Connection, youth_id: str) -> pd.DataFrame:
    if not table_exists(connection, "recommendations"):
        return pd.DataFrame()

    has_resources = table_exists(connection, "resources")
    if has_resources:
        query = """
            SELECT
                r.resource_id,
                COALESCE(res.resource_name, r.resource_id) AS resource_name,
                r.priority_rank,
                r.match_score,
                r.recommendation_reason,
                r.recommendation_source,
                r.recommendation_status,
                r.created_at
            FROM recommendations r
            LEFT JOIN resources res ON res.resource_id = r.resource_id
            WHERE r.youth_id = ?
            ORDER BY COALESCE(r.created_at, '') DESC, r.recommendation_id DESC
            LIMIT 15
        """
    else:
        query = """
            SELECT
                resource_id,
                resource_id AS resource_name,
                priority_rank,
                match_score,
                recommendation_reason,
                recommendation_source,
                recommendation_status,
                created_at
            FROM recommendations
            WHERE youth_id = ?
            ORDER BY COALESCE(created_at, '') DESC, recommendation_id DESC
            LIMIT 15
        """

    return pd.read_sql_query(query, connection, params=[youth_id])


def load_latest_intake_summary(connection: sqlite3.Connection, youth_id: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not table_exists(connection, "intake_sessions"):
        return pd.DataFrame(), pd.DataFrame()

    intake_cols = table_columns(connection, "intake_sessions")
    top_need_select = "top_need_category" if "top_need_category" in intake_cols else "NULL AS top_need_category"
    status_select = "session_status" if "session_status" in intake_cols else "NULL AS session_status"
    started_select = "started_at" if "started_at" in intake_cols else "NULL AS started_at"
    completed_select = "completed_at" if "completed_at" in intake_cols else "NULL AS completed_at"
    version_select = "assistant_version" if "assistant_version" in intake_cols else "NULL AS assistant_version"
    channel_select = "channel" if "channel" in intake_cols else "NULL AS channel"

    session_df = pd.read_sql_query(
        f"""
        SELECT
            intake_session_id,
            {status_select},
            {top_need_select},
            {started_select},
            {completed_select},
            {version_select},
            {channel_select}
        FROM intake_sessions
        WHERE youth_id = ?
        ORDER BY COALESCE(completed_at, started_at, '') DESC
        LIMIT 1
        """,
        connection,
        params=[youth_id],
    )

    if session_df.empty or not table_exists(connection, "intake_answers"):
        return session_df, pd.DataFrame()

    session_id = str(session_df.iloc[0]["intake_session_id"])
    answers_df = pd.read_sql_query(
        """
        SELECT
            question_key,
            question_text,
            answer_value,
            answered_at
        FROM intake_answers
        WHERE intake_session_id = ?
        ORDER BY intake_answer_id ASC
        """,
        connection,
        params=[session_id],
    )
    return session_df, answers_df


def filter_profiles(frame: pd.DataFrame, mode: str, query: str) -> pd.DataFrame:
    cleaned = query.strip().lower()
    if not cleaned:
        return frame

    if mode == "Youth ID":
        return frame[frame["youth_id"].str.lower().str.contains(cleaned, na=False)]

    return frame[frame["search_name"].str.contains(cleaned, na=False)]


def inject_profile_lookup_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            color: #10223f;
        }

        h1, h2, h3, h4 {
            color: #10223f;
        }

        .stCaption,
        .stMarkdown p,
        .stMarkdown li {
            color: #314862 !important;
        }

        .stMetric [data-testid="stMetricLabel"],
        .stMetric label,
        .stSelectbox label,
        .stRadio label,
        .stTextInput label,
        .stMultiSelect label {
            color: #244268 !important;
            font-weight: 700 !important;
        }

        .stMetric [data-testid="stMetricValue"] {
            color: #10223f !important;
        }

        .stButton > button {
            color: #143d7a !important;
            font-weight: 700 !important;
        }

        .stButton > button[kind="primary"] {
            color: #ffffff !important;
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

        .stDataFrame [role="grid"],
        .stDataFrame [role="columnheader"],
        .stDataFrame [role="gridcell"] {
            color: #10223f !important;
        }

        .stDataFrame [role="columnheader"] {
            background: #edf3ff !important;
            font-weight: 700 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
    st.set_page_config(page_title="Future Path Profile Lookup", page_icon="FP", layout="wide")
    inject_profile_lookup_styles()

    st.title("Youth Profile Lookup")
    st.caption("Search by youth ID or name to review profile details, risk score, recommendations, and intake context")

    render_top_navigation("profile_lookup")

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()

    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the pipeline first, then reload this page.")
        return

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        profiles = load_profiles(connection)

    if profiles.empty:
        st.warning("No youth profiles found. Load youth profiles into the database first.")
        return

    mode_col, query_col = st.columns([1, 3])
    with mode_col:
        search_mode = st.radio("Search By", options=["Youth ID", "Name"], horizontal=True)
    with query_col:
        placeholder = "e.g., YP-0001" if search_mode == "Youth ID" else "e.g., Aaliyah Smith"
        search_query = st.text_input("Search", placeholder=placeholder)

    filtered_profiles = filter_profiles(profiles, search_mode, search_query)
    st.caption(f"Matches: {len(filtered_profiles):,}")

    if filtered_profiles.empty:
        st.info("No profile matches your search.")
        return

    options = filtered_profiles.apply(
        lambda row: f"{row['youth_id']} - {row['full_name'] if row['full_name'] else 'No name available'}",
        axis=1,
    ).tolist()

    selected_label = st.selectbox("Select Profile", options=options)
    selected_youth_id = selected_label.split(" - ", 1)[0]

    selected_row = filtered_profiles[filtered_profiles["youth_id"] == selected_youth_id].iloc[0]

    st.subheader("Profile Information")
    info_col1, info_col2, info_col3 = st.columns(3)
    with info_col1:
        st.metric("Youth ID", str(selected_row["youth_id"]))
        st.write(f"Name: {selected_row['full_name'] if selected_row['full_name'] else 'Not available'}")
        st.write(f"Age: {int(selected_row['age'])}")
    with info_col2:
        st.write(f"County: {selected_row['county']}")
        st.write(f"Education: {selected_row['education']}")
        st.write(f"Employment: {selected_row['employment']}")
    with info_col3:
        st.write(f"Housing: {selected_row['housing']}")
        st.write(f"Mentor Status: {selected_row['mentor_status']}")
        st.write(f"Placement Count: {int(selected_row['placement_count'])}")

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        risk_df = load_latest_risk_score(connection, selected_youth_id)
        rec_df = load_recommendations(connection, selected_youth_id)
        intake_session_df, intake_answers_df = load_latest_intake_summary(connection, selected_youth_id)

    st.divider()
    st.subheader("Latest Risk Score")
    if risk_df.empty:
        st.info("No risk score found for this profile.")
    else:
        risk = risk_df.iloc[0]
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Risk Level", str(risk["risk_level"]))
        r2.metric("Overall Score", f"{float(risk['overall_risk_score']) * 100:.1f}%")
        r3.metric("Housing Risk", f"{float(risk['housing_risk_score'] or 0.0) * 100:.1f}%")
        r4.metric("Employment Risk", f"{float(risk['employment_risk_score'] or 0.0) * 100:.1f}%")
        st.caption(f"Calculated at: {risk['calculated_at']} | Model: {risk['model_name']} {risk['model_version'] or ''}")

    st.divider()
    st.subheader("Recommended Resources")
    if rec_df.empty:
        st.info("No recommendations found for this profile.")
    else:
        display_rec = rec_df.copy()
        display_rec["priority"] = display_rec["priority_rank"].map({1: "High", 2: "Medium", 3: "Low"}).fillna("N/A")
        st.dataframe(
            display_rec[
                [
                    "resource_name",
                    "priority",
                    "match_score",
                    "recommendation_status",
                    "recommendation_source",
                    "recommendation_reason",
                    "created_at",
                ]
            ],
            hide_index=True,
            width="stretch",
        )

    st.divider()
    st.subheader("Latest AI Assistant Intake Summary")
    if intake_session_df.empty:
        st.info("No AI intake session found for this profile.")
    else:
        session = intake_session_df.iloc[0]
        st.write(f"Session ID: {session['intake_session_id']}")
        st.write(f"Status: {session['session_status']}")
        st.write(f"Top Need Category: {session['top_need_category'] if session['top_need_category'] else 'Not set'}")
        st.write(f"Completed At: {session['completed_at'] if session['completed_at'] else 'In progress'}")

        if intake_answers_df.empty:
            st.caption("No intake answers available for this session.")
        else:
            st.dataframe(
                intake_answers_df[["question_text", "answer_value", "answered_at"]],
                hide_index=True,
                width="stretch",
            )


if __name__ == "__main__":
    render()
