from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from dashboard_server_manager import ensure_single_dashboard, switch_dashboard
from dashboard_theme import current_theme_badge_html, render_theme_toggle, theme_component_styles, theme_css_variables, themed_url


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

        .pl-step-banner {
            border: 1px solid #cbdcff;
            border-radius: 18px;
            background: linear-gradient(180deg, #f6faff 0%, #f3f8ff 100%);
            padding: 10px 16px;
            margin-bottom: 0.7rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .pl-step-title {
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

        .pl-shell {
            border: 1px solid #d7e4ff;
            border-radius: 22px;
            background: linear-gradient(180deg, #ffffff 0%, #fdfefe 100%);
            box-shadow: 0 14px 38px rgba(17, 61, 156, 0.07);
            margin-bottom: 1rem;
            overflow: hidden;
        }

        .pl-brandbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
            border-bottom: 1px solid #e2ebff;
        }

        .pl-brand-left {
            font-family: 'Manrope', sans-serif;
            font-weight: 800;
            letter-spacing: 0.2px;
            color: #12389f;
            font-size: 1.45rem;
        }

        .pl-brand-right {
            text-align: right;
            color: #16336f;
            font-size: 0.92rem;
            line-height: 1.3;
        }

        .pl-section-card {
            border: 1px solid #dee8ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 12px;
            margin-bottom: 0.9rem;
        }

        .pl-metric-card {
            border: 1px solid #e1eaff;
            border-radius: 14px;
            background: #fbfdff;
            padding: 14px 14px 12px 14px;
            min-height: 110px;
        }

        .pl-metric-label {
            color: #1f3f7e;
            font-weight: 700;
            font-size: 0.86rem;
            line-height: 1.3;
        }

        .pl-metric-value {
            color: #0f1f62;
            font-family: 'Manrope', sans-serif;
            font-size: 2rem;
            font-weight: 800;
            margin-top: 7px;
        }

        .stCaption,
        .stMarkdown p,
        .stMarkdown li,
        .stText {
            color: #18356f !important;
        }

        .stMetric [data-testid="stMetricLabel"],
        .stMetric label,
        .stSelectbox label,
        .stRadio label,
        .stTextInput label,
        .stMultiSelect label {
            color: #1d3773 !important;
            font-weight: 700 !important;
        }

        .stMetric [data-testid="stMetricValue"] {
            color: #143681 !important;
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

        [data-testid="stAlert"] {
            border-radius: 14px;
        }

        [data-testid="stAlert"] *,
        [data-testid="stAlert"] p,
        [data-testid="stAlert"] div {
            color: inherit !important;
            opacity: 1 !important;
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

        .stDataFrame {
            border: 1px solid #e5edff;
            border-radius: 14px;
        }

        .stDataFrame [role="grid"],
        .stDataFrame [role="columnheader"],
        .stDataFrame [role="gridcell"] {
            color: #10223f !important;
        }

        .stDataFrame [role="columnheader"] {
            background-color: #edf3ff !important;
            font-weight: 700 !important;
        }

        @media (max-width: 920px) {
            .pl-brandbar {
                flex-direction: column;
                align-items: flex-start;
                gap: 4px;
            }

            .pl-brand-right {
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

        [data-testid="stSidebar"] * {
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

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] span,
        div[data-baseweb="select"] *,
        div[data-baseweb="input"] input,
        div[data-baseweb="textarea"] textarea {
            color: var(--fp-input-text) !important;
            opacity: 1 !important;
        }

        .pl-step-banner,
        .pl-shell,
        .pl-section-card,
        .pl-metric-card,
        .stDataFrame {
            border-color: var(--fp-border-primary) !important;
        }

        .pl-step-banner {
            background: linear-gradient(180deg, var(--fp-surface-tertiary) 0%, var(--fp-surface-secondary) 100%) !important;
        }

        .pl-step-title,
        .pl-brand-left,
        .pl-metric-value,
        h1, h2, h3, h4,
        .stMetric [data-testid="stMetricValue"] {
            color: var(--fp-heading) !important;
        }

        .pl-shell {
            background: linear-gradient(180deg, var(--fp-surface-primary) 0%, var(--fp-surface-secondary) 100%) !important;
            box-shadow: 0 14px 38px var(--fp-shadow-soft) !important;
        }

        .pl-brandbar {
            border-bottom: 1px solid var(--fp-border-secondary) !important;
        }

        .pl-brand-right,
        .pl-metric-label,
        .stCaption,
        .stMarkdown p,
        .stMarkdown li,
        .stText,
        .stMetric [data-testid="stMetricLabel"],
        .stMetric label,
        .stSelectbox label,
        .stRadio label,
        .stTextInput label,
        .stMultiSelect label {
            color: var(--fp-text-secondary) !important;
        }

        .stCaption,
        .stCaption p {
            font-size: 0.98rem !important;
        }

        .pl-section-card {
            background: var(--fp-surface-primary) !important;
        }

        .pl-metric-card {
            background: var(--fp-surface-secondary) !important;
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

        .pl-jump-link {
            display: block;
            width: 100%;
            text-decoration: none !important;
            border: 1px solid var(--fp-button-border);
            border-radius: 10px;
            background: var(--fp-button-background);
            color: var(--fp-button-text) !important;
            font-weight: 700;
            text-align: center;
            padding: 0.42rem 0.55rem;
            margin: 0.18rem 0;
        }

        .pl-jump-link:hover {
            background: var(--fp-button-hover);
            color: var(--fp-button-text) !important;
        }

        .pl-anchor-target {
            position: relative;
            top: -72px;
            visibility: hidden;
            height: 0;
            display: block;
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

        [data-testid="stDataFrame"] canvas {
            background: var(--fp-data-cell-bg) !important;
        }

        .stDataFrame [role="grid"] {
            background: var(--fp-data-cell-bg) !important;
            border: 1px solid var(--fp-border-primary);
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
            background-color: var(--fp-data-cell-bg) !important;
            font-size: 1.04rem !important;
            font-weight: 600 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
                    next_url = themed_url(switch_dashboard(page_key, current_key=current_page))
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()


def render() -> None:
    st.set_page_config(page_title="Future Path Profile Lookup", page_icon="FP", layout="wide")
    ensure_single_dashboard("profile_lookup")
    inject_profile_lookup_styles()

    st.markdown(
        """
        <div class="pl-step-banner">
            <span class="pl-step-title">Youth Profiles</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Search by youth ID or name to review profile details, risk score, recommendations, and intake context.")

    render_top_navigation("profile_lookup")
    render_theme_toggle()

    st.markdown(
        f"""
        <div class="pl-shell">
            <div class="pl-brandbar fp-brand-header">
                <div class="pl-brand-left">Future Path</div>
                <div class="pl-brand-right">
                    <div><strong>Profile Lookup Workspace</strong></div>
                    <div>Review youth details and AI context</div>
                    <div style="margin-top:8px;">{current_theme_badge_html()}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()
    st.sidebar.divider()
    st.sidebar.markdown("### Quick Jump")
    profile_jump_tabs = st.sidebar.tabs(["Top", "Workflow", "Records"])
    with profile_jump_tabs[0]:
        st.markdown('<a class="pl-jump-link" href="#profile-search">Search</a>', unsafe_allow_html=True)
        st.markdown('<a class="pl-jump-link" href="#profile-info">Profile Info</a>', unsafe_allow_html=True)
    with profile_jump_tabs[1]:
        st.markdown('<a class="pl-jump-link" href="#profile-risk">Risk Score</a>', unsafe_allow_html=True)
        st.markdown('<a class="pl-jump-link" href="#profile-recommendations">Recommendations</a>', unsafe_allow_html=True)
    with profile_jump_tabs[2]:
        st.markdown('<a class="pl-jump-link" href="#profile-intake-summary">AI Intake Summary</a>', unsafe_allow_html=True)

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

    st.markdown('<span id="profile-search" class="pl-anchor-target"></span>', unsafe_allow_html=True)
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

    st.markdown('<span id="profile-info" class="pl-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Profile Information")
    st.markdown('<div class="pl-section-card">', unsafe_allow_html=True)
    info_col1, info_col2, info_col3 = st.columns(3)
    with info_col1:
        st.markdown(
            f"""
            <div class="pl-metric-card">
                <div class="pl-metric-label">Youth ID</div>
                <div class="pl-metric-value">{selected_row['youth_id']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
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
    st.markdown('</div>', unsafe_allow_html=True)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        risk_df = load_latest_risk_score(connection, selected_youth_id)
        rec_df = load_recommendations(connection, selected_youth_id)
        intake_session_df, intake_answers_df = load_latest_intake_summary(connection, selected_youth_id)

    st.divider()
    st.markdown('<span id="profile-risk" class="pl-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Latest Risk Score")
    st.markdown('<div class="pl-section-card">', unsafe_allow_html=True)
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
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<span id="profile-recommendations" class="pl-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Recommended Resources")
    st.markdown('<div class="pl-section-card">', unsafe_allow_html=True)
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
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.markdown('<span id="profile-intake-summary" class="pl-anchor-target"></span>', unsafe_allow_html=True)
    st.subheader("Latest AI Assistant Intake Summary")
    st.markdown('<div class="pl-section-card">', unsafe_allow_html=True)
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
    st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    render()
