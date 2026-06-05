from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import streamlit as st


THEME_QUERY_PARAM = "theme"
THEME_STATE_KEY = "dashboard_theme_mode"
THEME_OPTIONS = {"light", "dark"}


THEME_PALETTES: dict[str, dict[str, str]] = {
    "light": {
        "color_scheme": "light",
        "app_background": "#f6f9ff",
        "app_background_alt": "#ebf3ff",
        "app_overlay_primary": "rgba(19, 84, 177, 0.16)",
        "app_overlay_accent": "rgba(18, 144, 160, 0.10)",
        "text_primary": "#10223f",
        "text_secondary": "#2c4465",
        "text_muted": "#4b607c",
        "heading": "#0a2e63",
        "sidebar_background": "#f5f9ff",
        "sidebar_background_alt": "#ebf3ff",
        "sidebar_text": "#123d7b",
        "sidebar_border": "#cfe0f6",
        "surface_primary": "#ffffff",
        "surface_secondary": "#f6fbff",
        "surface_tertiary": "#eef6ff",
        "border_primary": "#d2e0f3",
        "border_secondary": "#dce8f6",
        "input_background": "#ffffff",
        "input_border": "#c6dbf3",
        "input_text": "#10223f",
        "button_background": "#eaf2ff",
        "button_hover": "#dbeaff",
        "button_text": "#114381",
        "button_border": "#bdd4f0",
        "button_primary_background": "#158e9f",
        "button_primary_hover": "#0f7d8d",
        "button_primary_text": "#ffffff",
        "button_primary_border": "#158e9f",
        "badge_background": "#e7f0ff",
        "badge_text": "#195196",
        "accent_blue": "#1a56a5",
        "accent_blue_soft": "#e7f0ff",
        "accent_teal": "#169eb0",
        "success_background": "#eefaf4",
        "success_background_alt": "#e5f5ea",
        "success_text": "#17804f",
        "success_border": "#c8ead6",
        "warning_background": "#fff7ea",
        "warning_text": "#a36510",
        "warning_border": "#f0d6ab",
        "danger_background": "#fff0f1",
        "danger_text": "#c62d3a",
        "danger_border": "#f7c4ca",
        "shadow_soft": "rgba(16, 34, 63, 0.04)",
        "shadow_hover": "rgba(16, 34, 63, 0.08)",
        "chart_hole": "#ffffff",
        "row_background": "#f8fbff",
        "row_border": "#e1e9f6",
        "data_header_bg": "#edf3ff",
        "data_header_focus_bg": "#e6eeff",
        "data_cell_bg": "#ffffff",
    },
    "dark": {
        "color_scheme": "dark",
        "app_background": "#0d1623",
        "app_background_alt": "#122033",
        "app_overlay_primary": "rgba(58, 123, 215, 0.24)",
        "app_overlay_accent": "rgba(22, 158, 176, 0.18)",
        "text_primary": "#eef2f7",
        "text_secondary": "#d4deea",
        "text_muted": "#9eb0c7",
        "heading": "#fbfcfe",
        "sidebar_background": "#102033",
        "sidebar_background_alt": "#15304a",
        "sidebar_text": "#e1ebf7",
        "sidebar_border": "#2f4d70",
        "surface_primary": "#16283d",
        "surface_secondary": "#1b314a",
        "surface_tertiary": "#21405f",
        "border_primary": "#32506f",
        "border_secondary": "#406183",
        "input_background": "#1d3550",
        "input_border": "#4e7398",
        "input_text": "#f3f6fa",
        "button_background": "#1f3c5d",
        "button_hover": "#274b73",
        "button_text": "#eef2f7",
        "button_border": "#4a7098",
        "button_primary_background": "#19a7bb",
        "button_primary_hover": "#1294a7",
        "button_primary_text": "#081217",
        "button_primary_border": "#19a7bb",
        "badge_background": "#183556",
        "badge_text": "#eef5fb",
        "accent_blue": "#7fb7ff",
        "accent_blue_soft": "#23456b",
        "accent_teal": "#4fd1de",
        "success_background": "#21362d",
        "success_background_alt": "#274036",
        "success_text": "#9bd5b1",
        "success_border": "#426456",
        "warning_background": "#3a3123",
        "warning_text": "#e8c68e",
        "warning_border": "#6f5b38",
        "danger_background": "#40272c",
        "danger_text": "#ecadb6",
        "danger_border": "#7a4d56",
        "shadow_soft": "rgba(4, 9, 18, 0.38)",
        "shadow_hover": "rgba(4, 9, 18, 0.54)",
        "chart_hole": "#16283d",
        "row_background": "#1d3550",
        "row_border": "#446588",
        "data_header_bg": "#21405f",
        "data_header_focus_bg": "#295177",
        "data_cell_bg": "#16283d",
    },
}


def get_theme_mode() -> str:
    query_theme = str(st.query_params.get(THEME_QUERY_PARAM, "")).strip().lower()
    if query_theme in THEME_OPTIONS:
        st.session_state[THEME_STATE_KEY] = query_theme

    current_theme = str(st.session_state.get(THEME_STATE_KEY, "light")).strip().lower()
    if current_theme not in THEME_OPTIONS:
        current_theme = "light"
        st.session_state[THEME_STATE_KEY] = current_theme

    if str(st.query_params.get(THEME_QUERY_PARAM, "")).strip().lower() != current_theme:
        st.query_params[THEME_QUERY_PARAM] = current_theme

    return current_theme


def get_theme_palette() -> dict[str, str]:
    return THEME_PALETTES[get_theme_mode()]


def render_theme_toggle() -> str:
    current_theme = get_theme_mode()
    toggled = st.sidebar.toggle(
        "Dark mode",
        value=current_theme == "dark",
        key="dashboard_theme_toggle",
        help="Switch between the light and dark dashboard theme.",
    )
    next_theme = "dark" if toggled else "light"
    if next_theme != current_theme:
        st.session_state[THEME_STATE_KEY] = next_theme
        st.query_params[THEME_QUERY_PARAM] = next_theme
        st.rerun()
    return next_theme


def themed_url(url: str) -> str:
    current_theme = get_theme_mode()
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[THEME_QUERY_PARAM] = current_theme
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def theme_css_variables() -> str:
    palette = get_theme_palette()
    variables = "\n".join(
        f"            --fp-{token.replace('_', '-')}: {value};"
        for token, value in palette.items()
    )
    return f"""
        :root {{
{variables}
        }}
    """


def theme_component_styles() -> str:
    return """
        .fp-brand-header {
            position: relative;
            overflow: hidden;
        }

        .fp-brand-header::after {
            content: '';
            position: absolute;
            inset: 0;
            background:
                radial-gradient(circle at 12% 18%, color-mix(in srgb, var(--fp-accent-blue) 22%, transparent) 0%, transparent 58%),
                radial-gradient(circle at 88% 20%, color-mix(in srgb, var(--fp-accent-teal) 18%, transparent) 0%, transparent 56%);
            pointer-events: none;
        }

        .fp-brand-header > * {
            position: relative;
            z-index: 1;
        }

        .fp-theme-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.42rem 0.78rem;
            border-radius: 999px;
            border: 1px solid color-mix(in srgb, var(--fp-accent-blue) 30%, var(--fp-border-primary));
            background: linear-gradient(135deg, color-mix(in srgb, var(--fp-accent-blue) 18%, var(--fp-badge-background)) 0%, color-mix(in srgb, var(--fp-accent-teal) 20%, var(--fp-badge-background)) 100%);
            color: var(--fp-badge-text);
            font-size: 0.74rem;
            font-weight: 800;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            white-space: nowrap;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), 0 10px 24px color-mix(in srgb, var(--fp-accent-blue) 16%, transparent);
        }

        .fp-theme-badge[data-theme='light'] {
            border-color: color-mix(in srgb, var(--fp-accent-blue) 24%, var(--fp-border-primary));
            background: linear-gradient(135deg, color-mix(in srgb, var(--fp-accent-blue) 12%, var(--fp-badge-background)) 0%, color-mix(in srgb, var(--fp-accent-teal) 12%, var(--fp-badge-background)) 100%);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 8px 18px color-mix(in srgb, var(--fp-accent-blue) 10%, transparent);
        }

        .fp-theme-badge[data-theme='dark'] {
            border-color: color-mix(in srgb, var(--fp-accent-blue) 42%, var(--fp-border-primary));
            background: linear-gradient(135deg, color-mix(in srgb, var(--fp-accent-blue) 24%, var(--fp-badge-background)) 0%, color-mix(in srgb, var(--fp-accent-teal) 26%, var(--fp-badge-background)) 100%);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06), 0 12px 28px color-mix(in srgb, var(--fp-accent-teal) 22%, transparent);
        }

        .fp-theme-badge::before {
            content: '';
            width: 0.58rem;
            height: 0.58rem;
            border-radius: 999px;
            background: var(--fp-accent-teal);
            box-shadow: 0 0 0 0.18rem color-mix(in srgb, var(--fp-accent-teal) 22%, transparent);
        }

        .fp-theme-badge[data-theme='light']::before {
            background: var(--fp-accent-blue);
            box-shadow: 0 0 0 0.16rem color-mix(in srgb, var(--fp-accent-blue) 22%, transparent);
        }

        .fp-header-meta {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
    """


def current_theme_badge_html() -> str:
    mode = get_theme_mode()
    label = "Dark Mode" if mode == "dark" else "Light Mode"
    return f'<span class="fp-theme-badge" data-theme="{mode}">{label}</span>'


def branded_palette(name: str) -> list[str]:
    mode = get_theme_mode()

    light_palettes = {
        "risk": ["#cf4a63", "#f2ac52", "#1f9faa", "#6b87ab"],
        "housing": ["#1a56a5", "#1a98a7", "#3d86da", "#f1a44a", "#2aa07f", "#d66679"],
        "employment": ["#1a98a7", "#2bb4c0", "#2b70ca", "#ef9f4d", "#d66679", "#339777"],
        "education": ["#2b70ca", "#1aa0af", "#5492de", "#efaa54", "#d86b82", "#369d7d"],
        "county": ["#1a56a5", "#1a98a7", "#5b90d8", "#efab57", "#d86b82", "#3a9f81"],
        "default": ["#1a56a5", "#1a98a7", "#5b90d8", "#efab57", "#d86b82", "#3a9f81"],
    }

    dark_palettes = {
        "risk": ["#ff6f8a", "#ffcb73", "#43d1cd", "#89acd8"],
        "housing": ["#7fb7ff", "#4fd1de", "#5aa3ff", "#ffc16f", "#56d2a6", "#ff8ea1"],
        "employment": ["#4fd1de", "#7ae5f0", "#6daeff", "#ffbc6e", "#ff8ea1", "#59d6ad"],
        "education": ["#6daeff", "#4fd1de", "#8cbfff", "#ffc87e", "#ff9ab0", "#66dcb6"],
        "county": ["#7fb7ff", "#4fd1de", "#94c3ff", "#ffc87e", "#ff9ab0", "#66dcb6"],
        "default": ["#7fb7ff", "#4fd1de", "#94c3ff", "#ffc87e", "#ff9ab0", "#66dcb6"],
    }

    palette_map = dark_palettes if mode == "dark" else light_palettes
    return palette_map.get(name, palette_map["default"])