"""Runtime helpers that let the dashboards run either as standalone Streamlit
servers (local multi-port mode) or consolidated into a single multi-page app
(Streamlit Community Cloud).

When the entry script ``streamlit_app.py`` sets ``FUTURE_PATH_CONSOLIDATED=1``
the dashboards skip per-page ``set_page_config`` and navigate between each other
using ``st.switch_page`` instead of localhost redirects.
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from dashboard_server_manager import switch_dashboard
from dashboard_theme import themed_url

CONSOLIDATED_ENV = "FUTURE_PATH_CONSOLIDATED"

# Maps dashboard keys (as used by DASHBOARD_CONFIG / render_top_navigation) to the
# ``st.Page`` objects registered by the consolidated entry script.
_PAGE_REGISTRY: dict[str, Any] = {}


def is_consolidated() -> bool:
    """Return True when running as a single consolidated multi-page app."""
    return os.environ.get(CONSOLIDATED_ENV) == "1"


def register_pages(pages: dict[str, Any]) -> None:
    """Register the dashboard-key -> st.Page mapping for consolidated navigation."""
    _PAGE_REGISTRY.clear()
    _PAGE_REGISTRY.update(pages)


def get_page(dashboard_key: str) -> Any | None:
    return _PAGE_REGISTRY.get(dashboard_key)


def configure_page(**kwargs: Any) -> None:
    """Apply ``st.set_page_config`` only when running as a standalone server.

    In consolidated mode the entry script already called ``set_page_config`` once,
    so calling it again from a page would raise; this becomes a safe no-op.
    """
    if is_consolidated():
        return
    try:
        st.set_page_config(**kwargs)
    except Exception:
        pass


def navigate(target_key: str, current_key: str | None = None) -> None:
    """Switch to another dashboard.

    Consolidated mode uses ``st.switch_page``; standalone mode falls back to the
    original localhost meta-refresh redirect and stops the current run.
    """
    if is_consolidated():
        page = get_page(target_key)
        if page is not None:
            st.switch_page(page)
            return

    next_url = themed_url(switch_dashboard(target_key, current_key=current_key))
    st.markdown(
        f'<meta http-equiv="refresh" content="0; url={next_url}">',
        unsafe_allow_html=True,
    )
    st.stop()
