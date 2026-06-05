from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from uuid import uuid4

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from assign_resources_from_intake import AssignmentResult, assign_resources_from_intake
from dashboard_server_manager import ensure_single_dashboard, switch_dashboard
from dashboard_theme import current_theme_badge_html, render_theme_toggle, theme_component_styles, theme_css_variables, themed_url
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
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_intake_tables(connection: sqlite3.Connection) -> None:
    ensure_intake_tables_base(connection)


def initialize_state() -> None:
    defaults = {
        "assistant_started": False,
        "assistant_completed": False,
        "assistant_session_id": "",
        "assistant_profile_type": "youth",
        "assistant_youth_id": "",
        "assistant_candidate_id": "",
        "assistant_current_index": 0,
        "assistant_answers": {},
        "assistant_summary_needs": [],
        "assistant_assignment_result": None,
        "assistant_error": "",
        "assistant_selected_choice": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_assistant_state() -> None:
    st.session_state["assistant_started"] = False
    st.session_state["assistant_completed"] = False
    st.session_state["assistant_session_id"] = ""
    st.session_state["assistant_current_index"] = 0
    st.session_state["assistant_answers"] = {}
    st.session_state["assistant_summary_needs"] = []
    st.session_state["assistant_assignment_result"] = None
    st.session_state["assistant_error"] = ""
    st.session_state["assistant_selected_choice"] = ""


def render_notice() -> None:
    st.info(
        "Future Path AI Assistant is a decision-support tool that uses synthetic/demo data. "
        "It does not replace emergency services or professional case management."
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


def _is_contact_value(value: object) -> bool:
    normalized = str(value or "").strip()
    return normalized != "" and normalized.lower() not in {"not listed", "n/a", "na", "none", "null"}


def resource_contact_html(phone: object, email: object, website: object) -> str:
    segments: list[str] = []

    phone_text = str(phone or "").strip()
    if _is_contact_value(phone_text):
        phone_href = "".join(char for char in phone_text if char.isdigit() or char == "+")
        if phone_href:
            segments.append(f'Phone: <a href="tel:{escape(phone_href)}">{escape(phone_text)}</a>')
        else:
            segments.append(f"Phone: {escape(phone_text)}")

    email_text = str(email or "").strip()
    if _is_contact_value(email_text):
        segments.append(f'Email: <a href="mailto:{escape(email_text)}">{escape(email_text)}</a>')

    website_text = str(website or "").strip()
    if _is_contact_value(website_text):
        website_href = website_text if website_text.startswith(("http://", "https://")) else f"https://{website_text}"
        segments.append(f'Website: <a href="{escape(website_href)}" target="_blank">{escape(website_text)}</a>')

    if not segments:
        return "Contact: Ask your caseworker for current contact details."
    return " | ".join(segments)


def inject_ai_assistant_styles() -> None:
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
                radial-gradient(circle at 8% 4%, rgba(17, 98, 220, 0.10) 0%, rgba(17, 98, 220, 0.0) 34%),
                linear-gradient(180deg, #f8fbff 0%, #f3f7ff 100%);
            color: #102a78;
            font-family: 'Plus Jakarta Sans', sans-serif;
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
            max-width: 1080px;
            padding-top: 1.2rem;
        }

        h1, h2, h3, h4 {
            font-family: 'Manrope', sans-serif !important;
            color: #122f82;
        }

        .ai-step-banner {
            border: 1px solid #cddcff;
            border-radius: 18px;
            background: linear-gradient(180deg, #f7faff 0%, #f3f8ff 100%);
            padding: 10px 16px;
            margin-bottom: 0.8rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .ai-step-title {
            color: #12307f;
            font-size: 1.9rem;
            font-weight: 800;
            font-family: 'Manrope', sans-serif;
            line-height: 1;
        }

        .ai-header-card {
            border: 1px solid #d7e4ff;
            border-radius: 18px;
            background: #ffffff;
            padding: 14px 16px;
            margin-bottom: 0.9rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .ai-header-title {
            font-family: 'Manrope', sans-serif;
            color: #123688;
            font-size: 1.6rem;
            font-weight: 800;
        }

        .ai-section-card {
            border: 1px solid #dce7ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 14px;
            margin-bottom: 0.85rem;
        }

        .ai-question-shell {
            border: 1px solid #dbe6ff;
            border-radius: 18px;
            background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
            box-shadow: 0 10px 22px rgba(19, 54, 142, 0.05);
            padding: 18px 18px 14px 18px;
            margin-top: 0.8rem;
        }

        .ai-kicker {
            color: #2f4f97;
            font-size: 0.94rem;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .ai-question {
            font-family: 'Manrope', sans-serif;
            color: #12337f;
            font-size: 1.35rem;
            font-weight: 700;
            line-height: 1.25;
            margin-bottom: 8px;
        }

        .ai-question-lead {
            color: #14377e;
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 10px;
        }

        .ai-question-header {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 8px;
        }

        .ai-question-icon {
            width: 58px;
            height: 58px;
            border-radius: 18px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 1.7rem;
            font-weight: 800;
            color: #ffffff;
            box-shadow: 0 10px 24px rgba(19, 54, 142, 0.14);
            flex: 0 0 58px;
        }

        .ai-question-copy {
            min-width: 0;
            flex: 1;
        }

        .ai-selection-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 12px;
            padding: 10px 12px;
            border-radius: 14px;
            border: 1px solid #c8ead6;
            background: linear-gradient(180deg, #eefaf4 0%, #e9f7ef 100%);
            color: #1d6a47;
            font-weight: 700;
        }

        .ai-selection-check {
            width: 28px;
            height: 28px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #1f7b4f;
            color: #ffffff;
            font-size: 0.95rem;
            font-weight: 800;
            flex: 0 0 28px;
        }

        .ai-need-chip,
        .ai-risk-chip {
            display: inline-block;
            border-radius: 10px;
            padding: 6px 10px;
            font-weight: 700;
            font-size: 0.9rem;
        }

        .ai-need-chip {
            color: #0f5a73;
            background: #e8f8fb;
            border: 1px solid #b4e7ee;
        }

        .ai-risk-chip {
            color: #b11f2f;
            background: #fff2f3;
            border: 1px solid #ffc7ce;
        }

        .ai-summary-metric {
            border: 1px solid #dde8ff;
            border-radius: 14px;
            background: #fbfdff;
            padding: 12px 14px;
            min-height: 86px;
        }

        .ai-summary-label {
            color: #21427f;
            font-size: 0.9rem;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .ai-summary-value {
            color: #12327f;
            font-family: 'Manrope', sans-serif;
            font-size: 1.55rem;
            font-weight: 800;
            line-height: 1;
        }

        .ai-summary-card {
            border: 1px solid #dbe6ff;
            border-radius: 18px;
            background: #ffffff;
            box-shadow: 0 10px 22px rgba(19, 54, 142, 0.05);
            padding: 14px 16px 16px 16px;
            margin-top: 0.9rem;
        }

        .ai-resource-row {
            border: 1px solid #e3ecff;
            border-radius: 12px;
            padding: 10px;
            margin-bottom: 8px;
            background: #ffffff;
        }

        .ai-resource-title {
            color: #12367f;
            font-size: 1rem;
            font-weight: 700;
            line-height: 1.3;
        }

        .ai-resource-meta {
            color: #27437f;
            font-size: 0.88rem;
        }

        .assist-resource-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            border: 1px solid #e3ecff;
            border-radius: 14px;
            background: #ffffff;
            padding: 10px 12px;
            margin-bottom: 10px;
        }

        .assist-resource-row .assist-resource-title {
            font-size: 0.96rem;
        }

        .assist-resource-row .assist-resource-desc {
            color: #425c87;
            font-size: 0.84rem;
            line-height: 1.35;
            margin-top: 2px;
        }

        .assist-chat-strip {
            margin-top: 12px;
            border: 1px solid #dbe6ff;
            border-radius: 16px;
            background: #ffffff;
            padding: 10px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            color: #35558f;
            font-weight: 600;
        }

        .assist-footer-actions {
            display: flex;
            gap: 10px;
            margin-top: 12px;
            flex-wrap: wrap;
        }

        .assist-footer-actions .stButton > button {
            min-height: 40px;
            border-radius: 999px !important;
            background: #eef8f8 !important;
            color: #0f6f7f !important;
            border-color: #cce7e8 !important;
            padding-left: 14px !important;
            padding-right: 14px !important;
        }

        .assist-footer-actions .stButton > button:hover {
            background: #e2f3f4 !important;
            border-color: #b6dde0 !important;
        }

        .assist-chat-send {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            color: #ffffff;
            background: linear-gradient(180deg, #9db2dc 0%, #7d9bd0 100%);
            font-size: 1.15rem;
            font-weight: 800;
        }

        .ai-option-grid {
            display: grid;
            gap: 12px;
            margin-top: 10px;
        }

        .ai-option-badge {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            flex: 0 0 28px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #edf2ff;
            color: #23489d;
            font-family: 'Manrope', sans-serif;
            font-weight: 800;
        }

        .ai-option-tile.selected .ai-option-badge {
            background: rgba(255, 255, 255, 0.22);
            color: #ffffff;
        }

        .ai-option-text {
            font-size: 0.98rem;
            font-weight: 600;
            line-height: 1.25;
        }

        .ai-option-grid div[data-testid="stButton"] > button {
            width: 100%;
            min-height: 52px;
            padding: 10px 14px;
            display: flex;
            align-items: center;
            gap: 12px;
            justify-content: flex-start;
            text-align: left;
            border-radius: 14px !important;
            border: 1px solid #d6e2fb !important;
            background: #ffffff !important;
            color: #12307f !important;
            box-shadow: 0 4px 10px rgba(19, 54, 142, 0.03) !important;
            font-weight: 700 !important;
            line-height: 1.2 !important;
            white-space: normal !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button:hover {
            background: #f7fbff !important;
            border-color: #a9c2f7 !important;
            transform: translateY(-1px);
        }

        .ai-option-grid div[data-testid="stButton"] > button[kind="primary"] {
            background: linear-gradient(180deg, #1f8aa0 0%, #0f7286 100%) !important;
            color: #ffffff !important;
            border: 1px solid #0f7286 !important;
            box-shadow: 0 10px 22px rgba(15, 114, 134, 0.24) !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button[kind="primary"]:hover {
            background: linear-gradient(180deg, #238fa6 0%, #126f82 100%) !important;
            border-color: #126f82 !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button:focus {
            outline: 2px solid rgba(14, 127, 148, 0.35) !important;
            outline-offset: 2px;
        }

        .stSelectbox label,
        .stMultiSelect label,
        .stDateInput label,
        .stTextInput label,
        .stTextArea label,
        .stCheckbox label,
        .stNumberInput label,
        .stRadio label,
        .stMarkdown p,
        .stMarkdown li,
        .stCaption {
            color: #18356f !important;
            opacity: 1 !important;
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

        .stRadio div[role="radiogroup"] label p {
            color: #14367f !important;
            font-weight: 600 !important;
        }

        .stButton > button {
            border: 1px solid #d2dfff !important;
            border-radius: 12px !important;
            background: #f4f8ff !important;
            color: #173b80 !important;
            font-weight: 700 !important;
            min-height: 44px;
        }

        .stButton > button[kind="primary"] {
            background: #0e7c8b !important;
            color: #ffffff !important;
            border-color: #0e7c8b !important;
        }

        .stButton > button[kind="primary"]:hover {
            background: #0b6f7d !important;
            border-color: #0b6f7d !important;
        }

        .stButton > button:hover {
            background: #edf4ff !important;
        }

        .stProgress > div > div > div > div {
            background-color: #1f9fb0 !important;
        }

        .stApp {
            background:
                radial-gradient(circle at 8% 4%, var(--fp-app-overlay-primary) 0%, rgba(17, 98, 220, 0.0) 34%),
                linear-gradient(180deg, var(--fp-app-background) 0%, var(--fp-app-background-alt) 100%);
            color: var(--fp-text-primary);
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
        [data-testid="stSidebar"] div[data-baseweb="textarea"] > div {
            background: var(--fp-input-background) !important;
            border: 1px solid var(--fp-input-border) !important;
            color: var(--fp-input-text) !important;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] span {
            color: var(--fp-input-text) !important;
        }

        h1, h2, h3, h4,
        .ai-step-title,
        .ai-header-title,
        .ai-question,
        .ai-summary-value,
        .ai-resource-title {
            color: var(--fp-heading) !important;
        }

        .ai-step-banner,
        .ai-header-card,
        .ai-section-card,
        .ai-question-shell,
        .ai-summary-card,
        .ai-summary-metric,
        .ai-resource-row,
        .assist-resource-row,
        .assist-chat-strip {
            border-color: var(--fp-border-primary) !important;
        }

        .ai-step-banner {
            background: linear-gradient(180deg, var(--fp-surface-tertiary) 0%, var(--fp-surface-secondary) 100%) !important;
        }

        .ai-header-card,
        .ai-section-card,
        .ai-summary-card,
        .ai-resource-row,
        .assist-resource-row,
        .assist-chat-strip {
            background: var(--fp-surface-primary) !important;
        }

        .ai-question-shell,
        .ai-summary-metric {
            background: linear-gradient(180deg, var(--fp-surface-primary) 0%, var(--fp-surface-secondary) 100%) !important;
            box-shadow: 0 10px 22px var(--fp-shadow-soft) !important;
        }

        .ai-kicker,
        .ai-question-lead,
        .ai-resource-meta,
        .assist-resource-row .assist-resource-desc,
        .assist-chat-strip,
        .ai-summary-label,
        .stSelectbox label,
        .stMultiSelect label,
        .stDateInput label,
        .stTextInput label,
        .stTextArea label,
        .stCheckbox label,
        .stNumberInput label,
        .stRadio label,
        .stMarkdown p,
        .stMarkdown li,
        .stCaption {
            color: var(--fp-text-secondary) !important;
        }

        .ai-selection-indicator {
            border-color: var(--fp-success-border) !important;
            background: linear-gradient(180deg, var(--fp-success-background) 0%, var(--fp-success-background-alt) 100%) !important;
            color: var(--fp-success-text) !important;
        }

        .ai-selection-check {
            background: var(--fp-success-text) !important;
            color: var(--fp-surface-primary) !important;
        }

        .ai-need-chip {
            color: var(--fp-button-primary-text) !important;
            background: var(--fp-button-primary-background) !important;
            border-color: var(--fp-button-primary-border) !important;
        }

        .ai-risk-chip {
            color: var(--fp-danger-text) !important;
            background: var(--fp-danger-background) !important;
            border-color: var(--fp-danger-border) !important;
        }

        .ai-option-badge {
            background: var(--fp-accent-blue-soft) !important;
            color: var(--fp-accent-blue) !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button,
        .stButton > button,
        .assist-footer-actions .stButton > button {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border-color: var(--fp-button-border) !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button:hover,
        .stButton > button:hover,
        .assist-footer-actions .stButton > button:hover {
            background: var(--fp-button-hover) !important;
            color: var(--fp-button-text) !important;
        }

        .ai-option-grid div[data-testid="stButton"] > button[kind="primary"],
        .stButton > button[kind="primary"] {
            background: linear-gradient(180deg, var(--fp-button-primary-background) 0%, var(--fp-button-primary-hover) 100%) !important;
            color: var(--fp-button-primary-text) !important;
            border-color: var(--fp-button-primary-border) !important;
        }

        .stProgress > div > div > div > div {
            background-color: var(--fp-accent-teal) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def format_option_label(question_key: str, value: str) -> str:
    question_specific_mapping = {
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
    if question_key in question_specific_mapping and value in question_specific_mapping[question_key]:
        return question_specific_mapping[question_key][value]

    mapping = {
        "stable": "I have a stable place lined up",
        "temporary": "I am in temporary housing",
        "couch_surfing": "I am couch surfing",
        "shelter": "I am staying in a shelter",
        "at_risk": "I am at risk of homelessness",
        "full_time": "I am employed full-time",
        "part_time": "I am employed part-time",
        "unemployed": "I am currently unemployed",
        "training": "I am in training or internship",
        "seasonal": "I have seasonal work",
        "in_school": "I am currently in school",
        "diploma_or_ged": "I completed diploma or GED",
        "no_diploma_or_ged": "I do not have a diploma or GED",
        "postsecondary": "I am in postsecondary education",
        "not_enrolled": "I am not enrolled right now",
        "reliable": "My transportation is reliable",
        "limited": "My transportation is limited",
        "none": "I have no reliable transportation",
        "yes": "Yes",
        "no": "No",
        "all": "I have all key documents",
        "some": "I have some documents",
        "strong": "I have a strong support system",
        "housing": "Housing stability",
        "employment": "Employment",
        "education": "Education",
        "transportation": "Transportation",
        "food": "Food access",
        "health_wellness": "Health and wellness",
        "documents": "ID and documents",
        "support_system": "Support system",
        "safety": "Safety",
    }
    if value in mapping:
        return mapping[value]
    return value.replace("_", " ").strip().capitalize()


def format_need_label(value: str) -> str:
    return value.replace("_", " ").strip().title()


def option_letter(index: int) -> str:
    return chr(ord("A") + index)


def question_icon_markup(question_key: str) -> str:
    icon_map = {
        "housing_status": ("linear-gradient(180deg, #33b5bd 0%, #2399a2 100%)", "&#127968;"),
        "employment_status": ("linear-gradient(180deg, #3f87eb 0%, #2d6fd6 100%)", "&#128188;"),
        "education_status": ("linear-gradient(180deg, #ffc04a 0%, #f0aa27 100%)", "&#127891;"),
        "transportation_access": ("linear-gradient(180deg, #9a6bf3 0%, #7d4fd7 100%)", "&#128652;"),
        "food_access": ("linear-gradient(180deg, #45c265 0%, #2fa84e 100%)", "&#128717;"),
        "health_wellness_need": ("linear-gradient(180deg, #ff6a7c 0%, #ec4c61 100%)", "&#10084;"),
        "documents_status": ("linear-gradient(180deg, #3f87eb 0%, #2d6fd6 100%)", "&#128179;"),
        "support_system": ("linear-gradient(180deg, #9a6bf3 0%, #7d4fd7 100%)", "&#128101;"),
        "safety_concern": ("linear-gradient(180deg, #9a6bf3 0%, #7d4fd7 100%)", "&#128737;"),
        "primary_need": ("linear-gradient(180deg, #0fa9b9 0%, #0b8896 100%)", "&#10024;"),
    }
    background, icon = icon_map.get(question_key, ("linear-gradient(180deg, #6d8fd7 0%, #5275bc 100%)", "&#10022;"))
    return f'<div class="ai-question-icon" style="background:{background};">{icon}</div>'


def infer_need_and_risk(question_key: str, choice: str) -> tuple[str, str]:
    category_map = {
        "housing_status": "Housing Stability",
        "employment_status": "Employment",
        "education_status": "Education",
        "transportation_access": "Transportation",
        "food_access": "Food Access",
        "health_wellness_need": "Health and Wellness",
        "documents_status": "ID and Documents",
        "support_system": "Support System",
        "safety_concern": "Safety",
        "primary_need": "Primary Need",
    }
    high_values = {
        "temporary",
        "couch_surfing",
        "shelter",
        "at_risk",
        "unemployed",
        "no_diploma_or_ged",
        "not_enrolled",
        "none",
        "no",
    }
    medium_values = {"limited", "training", "seasonal", "some", "sometimes"}

    risk = "Low"
    if question_key == "safety_concern" and choice == "yes":
        risk = "High"
    elif choice in high_values:
        risk = "High"
    elif choice in medium_values:
        risk = "Medium"

    return category_map.get(question_key, "General Support"), risk


def profile_exists(connection: sqlite3.Connection, profile_type: str, youth_id: str, candidate_id: str) -> tuple[bool, str]:
    if profile_type == "candidate":
        if not candidate_id.strip():
            return False, "Candidate profile ID is required."
        return True, ""

    if not youth_id.strip():
        return False, "Youth ID is required."
    if not table_exists(connection, "youth_profiles"):
        return False, "youth_profiles table not found in database."

    row = connection.execute("SELECT 1 FROM youth_profiles WHERE youth_id = ?", (youth_id.strip(),)).fetchone()
    if row is None:
        return False, f"Youth ID not found: {youth_id.strip()}"

    completed_row = connection.execute(
        """
        SELECT completed_at
        FROM intake_sessions
        WHERE youth_id = ?
          AND profile_type = 'youth'
          AND session_status = 'completed'
        ORDER BY COALESCE(completed_at, started_at, '') DESC
        LIMIT 1
        """,
        (youth_id.strip(),),
    ).fetchone()
    if completed_row is not None:
        completed_at = str(completed_row[0] or "").replace("T", " ")[:16]
        return False, f"AI Intake already completed for {youth_id.strip()} on {completed_at or 'a previous session'}."

    return True, ""


def generate_next_candidate_id(connection: sqlite3.Connection) -> str:
    if not table_exists(connection, "intake_sessions"):
        return "CP-0001"

    rows = connection.execute(
        """
        SELECT candidate_profile_id
        FROM intake_sessions
        WHERE candidate_profile_id IS NOT NULL
          AND candidate_profile_id LIKE 'CP-%'
        """
    ).fetchall()

    current_max = 0
    for row in rows:
        candidate_id = str(row[0] or "")
        suffix = candidate_id[3:]
        if suffix.isdigit():
            current_max = max(current_max, int(suffix))
    return f"CP-{current_max + 1:04d}"


def start_intake_session(connection: sqlite3.Connection, session_id: str, profile_type: str, youth_id: str, candidate_id: str) -> None:
    ensure_intake_tables(connection)

    resolved_type, resolved_youth_id, resolved_candidate_id = resolve_profile_link(
        connection,
        youth_id.strip() if profile_type == "youth" else None,
        candidate_id.strip() if profile_type == "candidate" else None,
    )

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
        ) VALUES (?, ?, ?, ?, 'in_progress', 'future_path_ai_assistant_streamlit_v1', 'streamlit', NULL)
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


def render_chat_history() -> None:
    answered = st.session_state["assistant_answers"]
    for question in QUESTIONS:
        key = question["key"]
        if key not in answered:
            break
        with st.chat_message("assistant"):
            st.write(question["prompt"])
        with st.chat_message("user"):
            st.write(answered[key])


def render_progress() -> None:
    current_index = st.session_state["assistant_current_index"]
    total = len(QUESTIONS)
    percent = int((current_index / total) * 100)
    st.markdown(
        f"""
        <div class="ai-kicker" style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
            <span>Question {min(current_index + 1, total)} of {total}</span>
            <span>{percent}%</span>
        </div>
        <div style="height:8px;border-radius:999px;background:var(--fp-border-secondary);overflow:hidden;">
            <div style="height:100%;width:{max(percent, 2)}%;background:linear-gradient(90deg,var(--fp-button-primary-background) 0%,var(--fp-accent-teal) 100%);border-radius:inherit;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_question_input(connection: sqlite3.Connection, db_path: Path) -> None:
    current_index = st.session_state["assistant_current_index"]
    question = QUESTIONS[current_index]
    key = question["key"]

    st.markdown('<div class="ai-question-shell">', unsafe_allow_html=True)
    lead_text = "Let's start with housing." if current_index == 0 else "Choose the answer that best fits your situation."
    st.markdown(
        f'''
        <div class="ai-question-header">
            {question_icon_markup(key)}
            <div class="ai-question-copy">
                <div class="ai-question-lead">{lead_text}</div>
                <div class="ai-question">{question["prompt"]}</div>
            </div>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    current_choice = st.session_state.get("assistant_selected_choice", question["options"][0])
    choice_value = current_choice if current_choice in question["options"] else question["options"][0]

    st.markdown('<div class="ai-option-grid">', unsafe_allow_html=True)
    for index, option in enumerate(question["options"]):
        option_label = format_option_label(key, option)
        button_label = f"{option_letter(index)}  {option_label}"
        if option == choice_value:
            button_label = f"{option_letter(index)}  {option_label}   [Selected]"
        if st.button(
            button_label,
            key=f"assistant_option_{current_index}_{option}",
            width="stretch",
            type="primary" if option == choice_value else "secondary",
        ):
            choice_value = option
            st.session_state["assistant_selected_choice"] = option
    st.markdown('</div>', unsafe_allow_html=True)

    st.session_state["assistant_selected_choice"] = choice_value
    st.markdown(
        f'''
        <div class="ai-selection-indicator">
            <span class="ai-selection-check">&#10003;</span>
            <span>Selected answer: {format_option_label(key, choice_value)}</span>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    need_label, risk_level = infer_need_and_risk(key, choice_value)
    st.markdown(
        f"""
        <div style="display:flex;justify-content:space-between;gap:12px;margin-top:14px;flex-wrap:wrap;">
            <div style="font-size:0.95rem;color:var(--fp-text-secondary);font-weight:700;">Need Category Triggered</div>
            <div style="font-size:0.95rem;color:var(--fp-text-secondary);font-weight:700;">Risk Impact</div>
        </div>
        <div style="display:flex;justify-content:space-between;gap:12px;margin-top:8px;flex-wrap:wrap;">
            <span class="ai-need-chip">{need_label}</span>
            <span class="ai-risk-chip">{risk_level}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)

    action_col1, action_col2 = st.columns([1, 1])
    with action_col1:
        submit = st.button("Save and Continue", type="primary", width="stretch")
    with action_col2:
        stop = st.button("Stop Intake", width="stretch")

    if stop:
        session_id = st.session_state["assistant_session_id"]
        with sqlite3.connect(db_path) as stop_connection:
            stop_connection.execute(
                """
                UPDATE intake_sessions
                SET session_status = 'abandoned',
                    completed_at = ?
                WHERE intake_session_id = ?
                """,
                (datetime.now(UTC).isoformat(timespec="seconds"), session_id),
            )
            stop_connection.commit()
        st.warning("Intake stopped. Session marked as abandoned.")
        reset_assistant_state()
        st.rerun()

    if not submit:
        return

    session_id = st.session_state["assistant_session_id"]
    save_answer(connection, session_id, key, question["prompt"], choice_value)
    st.session_state["assistant_answers"][key] = choice_value

    if key == "safety_concern" and choice_value == "yes":
        st.error(
            "Emergency support warning: This assistant cannot provide crisis intervention. "
            "If there is immediate danger, contact emergency services now. In the U.S., call or text 988."
        )

    next_index = current_index + 1
    if next_index < len(QUESTIONS):
        st.session_state["assistant_current_index"] = next_index
        st.rerun()

    summary_needs = complete_intake_session(connection, session_id, st.session_state["assistant_answers"])
    assignment_result = assign_resources_from_intake(
        connection,
        intake_session_id=session_id,
        top_n=5,
        assigned_by="streamlit_ai_assistant_v1",
    )

    st.session_state["assistant_summary_needs"] = summary_needs
    st.session_state["assistant_assignment_result"] = assignment_result
    st.session_state["assistant_completed"] = True
    st.rerun()


def render_final_summary() -> None:
    session_id = st.session_state["assistant_session_id"]
    st.success("Intake complete. Recommendations generated.")
    st.caption(f"Session ID: {session_id}")

    summary_needs = st.session_state["assistant_summary_needs"]
    primary_need = format_need_label(summary_needs[0]) if summary_needs else "General Support"

    result: AssignmentResult | None = st.session_state["assistant_assignment_result"]
    top_priority = "Low"
    if result and result.get("assignments"):
        top_priority = str(result["assignments"][0]["priority_level"])

    st.markdown('<div class="ai-summary-card">', unsafe_allow_html=True)
    st.markdown('<div class="ai-kicker">Your Assessment Summary</div>', unsafe_allow_html=True)
    metrics_col1, metrics_col2 = st.columns(2)
    with metrics_col1:
        st.markdown(
            f"""
            <div class="ai-summary-metric">
                <div class="ai-summary-label">Overall Risk Level</div>
                <div class="ai-summary-value">{top_priority}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with metrics_col2:
        st.markdown(
            f"""
            <div class="ai-summary-metric">
                <div class="ai-summary-label">Top Need</div>
                <div class="ai-summary-value">{primary_need}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown('<div style="margin-top:16px;font-weight:800;color:var(--fp-heading);">Recommended Resources</div>', unsafe_allow_html=True)
    if not result:
        st.info("No recommendations were generated.")
    else:
        assignments = result["assignments"]
        if not assignments:
            st.info("No eligible resources matched this intake.")
        else:
            for item in assignments:
                left, right = st.columns([5, 1])
                with left:
                    st.markdown(
                        f"""
                        <div class="assist-resource-row">
                            <div class="assist-resource-main">
                                <div class="ai-resource-title">{item['resource_name']}</div>
                                <div class="assist-resource-desc">Priority: {item['priority_level']} | Score: {item['match_score']:.1f}</div>
                                <div class="assist-resource-desc">{resource_contact_html(item.get('contact_phone', ''), item.get('contact_email', ''), item.get('website', ''))}</div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with right:
                    if st.button("View", key=f"view_resource_{item['resource_id']}", width="stretch"):
                        st.info(item["match_reason"])
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="assist-chat-wrap">', unsafe_allow_html=True)
    chat_col1, chat_col2, chat_col3 = st.columns([0.42, 3.7, 2.4])
    with chat_col1:
        st.markdown('<div class="assist-bot-avatar">🤖</div>', unsafe_allow_html=True)
    with chat_col2:
        st.markdown(
            f'<div class="assist-chat-bubble">{st.session_state.get("assistant_message", "I\'m here to help. What would you like to do next?")}</div>',
            unsafe_allow_html=True,
        )
    with chat_col3:
        st.markdown('<div class="assist-footer-actions">', unsafe_allow_html=True)
        if st.button("Ask a question", width="stretch"):
            st.info("Ask me about housing, employment, education, documents, or safety.")
        if st.button("Explore resources", width="stretch"):
            st.info("Recommended resources are shown above.")
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        """
        <div class="assist-chat-strip">
            <span>Type your message...</span>
            <span class="assist-chat-send">➤</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.session_state.get("assistant_profile_type") == "candidate":
        if st.button("Start New Intake", width="stretch"):
            reset_assistant_state()
            st.rerun()


def render() -> None:
    st.set_page_config(page_title="Future Path AI Assistant", page_icon="FP", layout="wide")
    ensure_single_dashboard("ai_assistant")
    initialize_state()

    inject_ai_assistant_styles()
    st.markdown(
        """
        <div class="ai-step-banner">
            <span class="ai-step-title">Future Path AI Assistant</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="ai-header-card fp-brand-header">
            <div class="ai-header-title">Future Path AI Assistant</div>
            <div class="fp-header-meta">{current_theme_badge_html()}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("Guided intake assessment with recommendation support")

    render_top_navigation("ai_assistant")

    render_notice()
    render_theme_toggle()

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()

    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the data pipeline first, then return to this page.")
        return

    if not st.session_state["assistant_started"]:
        st.markdown('<div class="ai-section-card">', unsafe_allow_html=True)
        st.subheader("Start Intake")
        st.write("Select whether this intake is for a youth profile or a candidate profile.")

        profile_type = str(st.radio("Profile Type", options=["youth", "candidate"], horizontal=True))
        youth_id_input = st.text_input(
            "Youth ID",
            value=st.session_state["assistant_youth_id"],
            disabled=profile_type != "youth",
        )
        candidate_id_col, candidate_action_col = st.columns([2.2, 1])
        with candidate_id_col:
            candidate_id_input = st.text_input(
                "Candidate Profile ID",
                value=st.session_state["assistant_candidate_id"],
                disabled=profile_type != "candidate",
            )
        with candidate_action_col:
            st.write("")
            st.write("")
            if st.button(
                "Generate ID",
                width="stretch",
                key="assistant_generate_candidate_id",
                disabled=profile_type != "candidate",
            ):
                with sqlite3.connect(db_path) as generate_connection:
                    generated_id = generate_next_candidate_id(generate_connection)
                st.session_state["assistant_candidate_id"] = generated_id
                st.rerun()

        if profile_type == "candidate":
            st.caption("Tip: Use Generate ID for quick onboarding, then start intake.")
        youth_id = youth_id_input or ""
        candidate_id = candidate_id_input or ""

        start_disabled = False
        with sqlite3.connect(db_path) as connection:
            ok, validation_message = profile_exists(connection, profile_type, youth_id, candidate_id)
            start_disabled = not ok
            if validation_message:
                st.warning(validation_message)

        if st.button("Start Future Path AI Assistant", type="primary", width="stretch", disabled=start_disabled):
            session_id = f"intake-{uuid4()}"
            st.session_state["assistant_session_id"] = session_id
            st.session_state["assistant_profile_type"] = profile_type
            st.session_state["assistant_youth_id"] = youth_id.strip()
            st.session_state["assistant_candidate_id"] = candidate_id.strip()
            st.session_state["assistant_started"] = True
            st.session_state["assistant_completed"] = False
            st.session_state["assistant_current_index"] = 0
            st.session_state["assistant_answers"] = {}
            st.session_state["assistant_summary_needs"] = []
            st.session_state["assistant_assignment_result"] = None

            with sqlite3.connect(db_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                start_intake_session(
                    connection,
                    session_id=session_id,
                    profile_type=profile_type,
                    youth_id=youth_id,
                    candidate_id=candidate_id,
                )
                connection.commit()

            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        return

    if st.session_state["assistant_completed"]:
        render_final_summary()
        return

    render_progress()
    st.markdown('<div class="ai-question-card"><div class="ai-question-heading">Your current intake</div><div class="ai-question-text">Answer the question below and continue through the assessment.</div></div>', unsafe_allow_html=True)

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        render_question_input(connection, db_path)
        connection.commit()


if __name__ == "__main__":
    render()
