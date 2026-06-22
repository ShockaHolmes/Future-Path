"""Consolidated Future Path app for Streamlit Community Cloud.

This single entry point hosts all five dashboards as pages of one Streamlit
multi-page app. Locally each dashboard can still be run on its own port via
``start.command``; this file is what Streamlit Community Cloud serves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
for _sub in ("src", "dashboard"):
    _path = str(PROJECT_ROOT / _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Signal to the dashboards that they are running consolidated (single server).
os.environ["FUTURE_PATH_CONSOLIDATED"] = "1"

import streamlit as st  # noqa: E402

# Must be the first Streamlit command, before any page renders.
st.set_page_config(page_title="Future Path", page_icon="🧭", layout="wide")

import ai_assistant  # noqa: E402
import caseworker_dashboard  # noqa: E402
import overview  # noqa: E402
import profile_lookup  # noqa: E402
import youth_dashboard  # noqa: E402
from dashboard_runtime import register_pages  # noqa: E402

overview_page = st.Page(
    overview.render, title="Overview", icon="📊", url_path="overview", default=True
)
youth_page = st.Page(
    youth_dashboard.render, title="Youth Dashboard", icon="🧑", url_path="youth"
)
profiles_page = st.Page(
    profile_lookup.render, title="Youth Profiles", icon="🗂️", url_path="profiles"
)
assistant_page = st.Page(
    ai_assistant.render, title="AI Assistant", icon="🤖", url_path="assistant"
)
caseworker_page = st.Page(
    caseworker_dashboard.render, title="Caseworker", icon="🧰", url_path="caseworker"
)

register_pages(
    {
        "overview": overview_page,
        "youth_dashboard": youth_page,
        "profile_lookup": profiles_page,
        "ai_assistant": assistant_page,
        "caseworker_dashboard": caseworker_page,
    }
)

navigation = st.navigation(
    [overview_page, youth_page, profiles_page, assistant_page, caseworker_page]
)
navigation.run()
