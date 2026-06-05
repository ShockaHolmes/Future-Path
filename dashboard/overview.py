from __future__ import annotations

import base64
import sqlite3
import sys
from pathlib import Path
from textwrap import dedent

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from dashboard_server_manager import ensure_single_dashboard, switch_dashboard
from dashboard_theme import branded_palette, current_theme_badge_html, render_theme_toggle, theme_component_styles, theme_css_variables, themed_url
from candidate_promotion import load_promotable_candidate_intakes


DEFAULT_DB_PATH = Path("database/future_path.db")
STATE_ICON_PATH = Path("Assets/FuturePathPNG/State-of-Delaware.png")
LAUNCH_LOGO_PATH = Path("Assets/FuturePathPNG/Future-Path-Launch-Logo.png")
OVERVIEW_URL = "http://localhost:8501"
PROFILE_LOOKUP_URL = "http://localhost:8502"
AI_ASSISTANT_URL = "http://localhost:8503"
CASEWORKER_URL = "http://localhost:8504"
YOUTH_DASHBOARD_URL = "http://localhost:8505"


def load_image_data_uri(image_path: Path) -> str | None:
    if not image_path.exists():
        return None
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def load_active_resources(connection: sqlite3.Connection) -> int:
    if not table_exists(connection, "resources"):
        return 0
    row = connection.execute("SELECT COUNT(*) FROM resources").fetchone()
    return int(row[0]) if row is not None else 0


def load_youth_overview_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    if not table_exists(connection, "youth_profiles"):
        return pd.DataFrame(columns=["youth_id", "county", "housing", "employment", "education", "risk_level"])

    youth_df = pd.read_sql_query(
        """
        SELECT youth_id, county, housing, employment, education
        FROM youth_profiles
        """,
        connection,
    )

    if not table_exists(connection, "risk_scores"):
        youth_df["risk_level"] = "Unknown"
        return youth_df

    latest_risk_df = pd.read_sql_query(
        """
        WITH ranked AS (
            SELECT
                youth_id,
                risk_level,
                ROW_NUMBER() OVER (
                    PARTITION BY youth_id
                    ORDER BY COALESCE(calculated_at, '') DESC, risk_score_id DESC
                ) AS rn
            FROM risk_scores
        )
        SELECT youth_id, risk_level
        FROM ranked
        WHERE rn = 1
        """,
        connection,
    )

    merged = youth_df.merge(latest_risk_df, how="left", on="youth_id")
    merged["risk_level"] = merged["risk_level"].fillna("Unknown")
    return merged


def load_youth_identity_map(connection: sqlite3.Connection) -> dict[str, str]:
    if not table_exists(connection, "caseworker_youth"):
        return {}

    frame = pd.read_sql_query(
        """
        SELECT youth_id, first_name, last_name
        FROM caseworker_youth
        """,
        connection,
    )
    if frame.empty:
        return {}

    frame["full_name"] = (
        frame["first_name"].fillna("").astype(str).str.strip()
        + " "
        + frame["last_name"].fillna("").astype(str).str.strip()
    ).str.strip()
    frame["full_name"] = frame["full_name"].replace("", pd.NA).fillna(frame["youth_id"].astype(str))
    return dict(zip(frame["youth_id"].astype(str), frame["full_name"]))


def apply_filters(
    youth_frame: pd.DataFrame,
    selected_counties: list[str],
    selected_risk_levels: list[str],
) -> pd.DataFrame:
    filtered = youth_frame.copy()
    if selected_counties:
        filtered = filtered[filtered["county"].isin(selected_counties)]
    if selected_risk_levels:
        filtered = filtered[filtered["risk_level"].isin(selected_risk_levels)]
    return filtered


def load_dashboard_metrics(
    filtered_youth_df: pd.DataFrame,
    active_resources: int,
    candidate_queue_count: int = 0,
) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {
        "total_youth": 0,
        "high_risk_cases": 0,
        "stable_housing_pct": 0.0,
        "employment_pct": 0.0,
        "active_resources": active_resources,
        "candidate_queue_count": int(candidate_queue_count),
    }

    if filtered_youth_df.empty:
        return metrics

    metrics["total_youth"] = int(len(filtered_youth_df))
    metrics["high_risk_cases"] = int((filtered_youth_df["risk_level"] == "High").sum())
    metrics["stable_housing_pct"] = float((filtered_youth_df["housing"] == "Stable housing").mean() * 100.0)
    metrics["employment_pct"] = float((filtered_youth_df["employment"] != "Unemployed").mean() * 100.0)

    return metrics


def load_risk_breakdown(filtered_youth_df: pd.DataFrame) -> pd.DataFrame:
    if filtered_youth_df.empty:
        return pd.DataFrame(columns=["risk_level", "case_count"])

    order_map = {"High": 1, "Medium": 2, "Low": 3, "Unknown": 4}
    breakdown = (
        filtered_youth_df["risk_level"]
        .value_counts(dropna=False)
        .rename_axis("risk_level")
        .reset_index(name="case_count")
    )
    breakdown["order"] = breakdown["risk_level"].map(order_map).fillna(5)
    breakdown = breakdown.sort_values("order").drop(columns=["order"])
    return breakdown


def load_housing_employment_distribution(filtered_youth_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if filtered_youth_df.empty:
        empty = pd.DataFrame(columns=["label", "count"])
        return empty, empty

    housing_df = (
        filtered_youth_df["housing"]
        .value_counts(dropna=False)
        .rename_axis("label")
        .reset_index(name="count")
    )

    employment_df = (
        filtered_youth_df["employment"]
        .value_counts(dropna=False)
        .rename_axis("label")
        .reset_index(name="count")
    )

    return housing_df, employment_df


def load_education_distribution(filtered_youth_df: pd.DataFrame) -> pd.DataFrame:
    if filtered_youth_df.empty:
        return pd.DataFrame(columns=["label", "count"])

    return (
        filtered_youth_df["education"]
        .value_counts(dropna=False)
        .rename_axis("label")
        .reset_index(name="count")
    )


def load_top_recommended_resources(
    connection: sqlite3.Connection,
    filtered_youth_ids: list[str],
    limit: int = 10,
) -> pd.DataFrame:
    if not filtered_youth_ids:
        return pd.DataFrame(columns=["resource_name", "recommendation_count"])

    if table_exists(connection, "recommendations"):
        placeholders = ",".join("?" for _ in filtered_youth_ids)
        has_resources = table_exists(connection, "resources")
        if has_resources:
            query = f"""
                SELECT
                    COALESCE(res.resource_name, r.resource_id) AS resource_name,
                    COUNT(*) AS recommendation_count
                FROM recommendations r
                LEFT JOIN resources res ON res.resource_id = r.resource_id
                WHERE r.youth_id IN ({placeholders})
                GROUP BY COALESCE(res.resource_name, r.resource_id)
                ORDER BY recommendation_count DESC, resource_name ASC
                LIMIT ?
            """
        else:
            query = f"""
                SELECT
                    r.resource_id AS resource_name,
                    COUNT(*) AS recommendation_count
                FROM recommendations r
                WHERE r.youth_id IN ({placeholders})
                GROUP BY r.resource_id
                ORDER BY recommendation_count DESC, resource_name ASC
                LIMIT ?
            """
        params = [*filtered_youth_ids, limit]
        top_df = pd.read_sql_query(query, connection, params=params)
        if not top_df.empty:
            return top_df

    csv_path = Path("data/processed/youth_resource_matches.csv")
    if csv_path.exists():
        frame = pd.read_csv(csv_path)
        if {"youth_id", "resource_name"}.issubset(set(frame.columns)):
            filtered = frame[frame["youth_id"].astype(str).isin(filtered_youth_ids)]
            if not filtered.empty:
                return (
                    filtered["resource_name"]
                    .value_counts()
                    .rename_axis("resource_name")
                    .reset_index(name="recommendation_count")
                    .head(limit)
                )

    return pd.DataFrame(columns=["resource_name", "recommendation_count"])


def load_candidate_promotion_queue(connection: sqlite3.Connection) -> pd.DataFrame:
    queue_df = load_promotable_candidate_intakes(connection)
    if queue_df.empty:
        return pd.DataFrame(columns=["Candidate ID", "Top Need", "Completed", "Assigned Resources"])

    display_df = queue_df.copy().head(5)
    display_df["Completed"] = display_df["completed_at"].fillna(display_df["started_at"]).astype(str).str.slice(0, 10)
    display_df["Assigned Resources"] = display_df["assignment_count"].fillna(0).astype(int)
    return display_df.rename(
        columns={
            "candidate_profile_id": "Candidate ID",
            "top_need_category": "Top Need",
        }
    )[["Candidate ID", "Top Need", "Completed", "Assigned Resources"]]


def load_county_level_needs(filtered_youth_df: pd.DataFrame) -> pd.DataFrame:
    if filtered_youth_df.empty:
        return pd.DataFrame(
            columns=["county", "youth_count", "high_risk_cases", "unstable_housing_cases", "unemployment_cases", "need_index"]
        )

    frame = filtered_youth_df.copy()
    frame["is_high_risk"] = frame["risk_level"].eq("High").astype(int)
    frame["is_unstable_housing"] = frame["housing"].ne("Stable housing").astype(int)
    frame["is_unemployed"] = frame["employment"].eq("Unemployed").astype(int)

    grouped = (
        frame.groupby("county", dropna=False)
        .agg(
            youth_count=("youth_id", "count"),
            high_risk_cases=("is_high_risk", "sum"),
            unstable_housing_cases=("is_unstable_housing", "sum"),
            unemployment_cases=("is_unemployed", "sum"),
        )
        .reset_index()
    )
    grouped["need_index"] = grouped["high_risk_cases"] * 3 + grouped["unstable_housing_cases"] * 2 + grouped["unemployment_cases"]
    grouped = grouped.sort_values(["need_index", "youth_count"], ascending=[False, False])
    return grouped


def build_insight_callouts(
    metrics: dict[str, float | int],
    county_needs_df: pd.DataFrame,
    top_resources_df: pd.DataFrame,
    risk_breakdown: pd.DataFrame,
) -> list[str]:
    insights: list[str] = []

    total_youth = int(metrics.get("total_youth", 0))
    high_risk_cases = int(metrics.get("high_risk_cases", 0))
    stable_housing_pct = float(metrics.get("stable_housing_pct", 0.0))
    employment_pct = float(metrics.get("employment_pct", 0.0))

    if total_youth > 0:
        insights.append(
            f"This view covers {total_youth:,} youth, with {high_risk_cases:,} currently classified as high risk."
        )
        insights.append(
            f"Housing stability is {stable_housing_pct:.1f}% and employment participation is {employment_pct:.1f}% in the selected population."
        )

    if not county_needs_df.empty:
        top_county = county_needs_df.iloc[0]
        insights.append(
            f"{top_county['county']} has the highest need index ({int(top_county['need_index'])}), driven by high-risk, housing instability, and unemployment signals."
        )

    if not top_resources_df.empty:
        top_resource = top_resources_df.iloc[0]
        insights.append(
            f"Most recommended support right now: {top_resource['resource_name']} ({int(top_resource['recommendation_count'])} matches)."
        )

    if not risk_breakdown.empty:
        total_scored = int(risk_breakdown["case_count"].sum())
        high_row = risk_breakdown[risk_breakdown["risk_level"] == "High"]
        if total_scored > 0 and not high_row.empty:
            high_count = int(high_row.iloc[0]["case_count"])
            high_pct = (high_count / total_scored) * 100.0
            insights.append(f"High-risk share is {high_pct:.1f}% of risk-scored youth in this filtered view.")

    return insights


def render_metric_cards(metrics: dict[str, float | int]) -> None:
    cards = [
        ("Total Youth", f"{int(metrics['total_youth']):,}", "vs last month"),
        ("High Risk Cases", f"{int(metrics['high_risk_cases']):,}", "priority review"),
        ("Candidates Waiting", f"{int(metrics['candidate_queue_count']):,}", "ready to promote"),
        ("Stable Housing %", f"{float(metrics['stable_housing_pct']):.0f}%", "current view"),
        ("Employed %", f"{float(metrics['employment_pct']):.0f}%", "current view"),
        ("Active Resources", f"{int(metrics['active_resources']):,}", "catalog count"),
    ]
    cols = st.columns(6)
    for index, (title, value, footnote) in enumerate(cards):
        with cols[index]:
            card_markup = f"""
                <div class="overview-kpi-card">
                    <div class="overview-kpi-label">{title}</div>
                    <div class="overview-kpi-value">{value}</div>
                    <div class="overview-kpi-footnote">{footnote}</div>
                </div>
            """
            if title == "Candidates Waiting":
                card_markup = f'<a class="overview-kpi-card-link" href="#candidate-queue">{card_markup}</a>'
            st.markdown(card_markup, unsafe_allow_html=True)


def render_pie_chart(frame: pd.DataFrame, label_column: str, value_column: str, title: str, colors: list[str]) -> None:
    if frame.empty:
        st.info(f"No {title.lower()} data found.")
        return

    chart_frame = frame[[label_column, value_column]].copy()
    chart_frame[value_column] = pd.to_numeric(chart_frame[value_column], errors="coerce").fillna(0)
    chart_frame = chart_frame[chart_frame[value_column] > 0]
    if chart_frame.empty:
        st.info(f"No {title.lower()} data found.")
        return

    total = float(chart_frame[value_column].sum())
    if total <= 0:
        st.info(f"No {title.lower()} data found.")
        return

    segments: list[str] = []
    legend_rows: list[str] = []
    cumulative = 0.0
    palette = colors or ["#2563eb", "#14b8a6", "#f59e0b", "#8b5cf6", "#ef4444", "#22c55e"]

    for index, row in chart_frame.reset_index(drop=True).iterrows():
        value = float(row[value_column])
        share = value / total
        start = cumulative * 100.0
        end = (cumulative + share) * 100.0
        color = palette[index % len(palette)]
        segments.append(f"{color} {start:.2f}% {end:.2f}%")
        legend_rows.append(
            f"""
            <div class="pie-legend-row">
                <div class="pie-legend-left">
                    <span class="pie-legend-dot" style="background:{color};"></span>
                    <span class="pie-legend-label">{row[label_column]}</span>
                </div>
                <div class="pie-legend-value">{value:,.0f} <span>({share * 100.0:.0f}%)</span></div>
            </div>
            """
        )
        cumulative += share

    legend_html = "".join(row.strip() for row in legend_rows)
    gradient_css = ", ".join(segments)
    st.markdown(
        dedent(
            f"""
            <div class="pie-chart-shell">
                <div class="pie-chart-ring" style="background: conic-gradient({gradient_css});">
                    <div class="pie-chart-hole"></div>
                </div>
                <div class="pie-chart-legend">
                    {legend_html}
                </div>
            </div>
            """
        ).strip(),
        unsafe_allow_html=True,
    )


def render_state_visual(county_needs_df: pd.DataFrame) -> None:
    top_counties = county_needs_df.head(3).copy() if not county_needs_df.empty else pd.DataFrame(columns=["county", "need_index"])
    left_col, right_col = st.columns([0.75, 1.65], gap="medium")

    with left_col:
        if STATE_ICON_PATH.exists():
            st.markdown('<div class="state-image-wrap">', unsafe_allow_html=True)
            st.image(str(STATE_ICON_PATH), caption="Delaware", width=100)
            st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Delaware state icon not found.")

    with right_col:
        if top_counties.empty:
            st.caption("No county data available.")
            return

        max_need = max(float(top_counties["need_index"].max()), 1.0)
        for _, row in top_counties.iterrows():
            need_value = int(row["need_index"])
            if need_value >= max_need * 0.75:
                label = "High Need"
            elif need_value >= max_need * 0.45:
                label = "Moderate"
            else:
                label = "Lower Need"

            row_left, row_right = st.columns([3, 1], gap="small")
            with row_left:
                st.markdown(
                    f"**{row['county']}**  \n"
                    f"{need_value:,} need index",
                )
            with row_right:
                st.markdown(
                    f"<div class='county-need-pill county-need-{'high' if label == 'High Need' else 'medium' if label == 'Moderate' else 'low'}'>{label}</div>",
                    unsafe_allow_html=True,
                )


def inject_overview_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        """
        + theme_css_variables()
        + theme_component_styles()
        + """

        .stApp {
            background: var(--fp-app-background);
            color: var(--fp-text-primary);
            font-family: 'Inter', sans-serif;
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
            color: var(--fp-sidebar-text) !important;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] span {
            color: var(--fp-sidebar-text) !important;
            opacity: 1 !important;
        }

        [data-testid="stSidebar"] .stButton > button {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border: 1px solid var(--fp-button-border) !important;
        }

        .main .block-container {
            max-width: 1360px;
            padding-top: 1.2rem;
            padding-bottom: 2rem;
        }

        .overview-shell,
        .overview-panel,
        .overview-side-panel,
        .overview-table-shell,
        .overview-strip {
            border: 1px solid var(--fp-border-primary);
            border-radius: 18px;
            background: var(--fp-surface-primary);
            box-shadow: 0 10px 22px var(--fp-shadow-soft);
            padding: 14px 16px;
        }

        .overview-shell {
            border-radius: 22px;
            margin-bottom: 1rem;
            overflow: hidden;
            padding: 0;
        }

        .overview-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            padding: 18px 20px;
            border-bottom: 1px solid var(--fp-border-secondary);
        }

        .overview-title {
            font-size: 2rem;
            font-weight: 800;
            color: var(--fp-heading);
            line-height: 1.1;
        }

        .overview-launch-logo {
            height: 70px;
            width: auto;
            max-width: 100%;
            object-fit: contain;
            display: block;
        }

        .overview-subtitle {
            color: var(--fp-text-secondary);
            font-size: 0.95rem;
            margin-top: 4px;
        }

        .overview-badge {
            padding: 8px 12px;
            border-radius: 999px;
            background: var(--fp-badge-background);
            color: var(--fp-badge-text);
            font-weight: 700;
            font-size: 0.88rem;
        }

        .overview-kpi-card {
            border: 1px solid var(--fp-border-primary);
            border-radius: 16px;
            background: linear-gradient(180deg, var(--fp-surface-primary) 0%, var(--fp-surface-secondary) 100%);
            padding: 14px 14px 12px 14px;
            box-shadow: 0 8px 18px var(--fp-shadow-soft);
            min-height: 110px;
        }

        .overview-kpi-card-link {
            text-decoration: none;
            display: block;
        }

        .overview-kpi-card-link .overview-kpi-card {
            position: relative;
            transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
        }

        .overview-kpi-card-link .overview-kpi-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 24px var(--fp-shadow-hover);
            border-color: var(--fp-button-border);
        }

        .overview-kpi-card-link .overview-kpi-card::after {
            content: 'Open queue';
            position: absolute;
            top: 12px;
            right: 14px;
            font-size: 0.72rem;
            font-weight: 800;
            color: var(--fp-accent-blue);
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }

        .overview-kpi-label {
            color: var(--fp-text-secondary);
            font-size: 0.9rem;
            font-weight: 700;
            margin-bottom: 10px;
        }

        .overview-kpi-value {
            color: var(--fp-heading);
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
        }

        .overview-kpi-footnote {
            color: var(--fp-text-muted);
            font-size: 0.78rem;
            margin-top: 8px;
        }

        .overview-panel-title {
            color: var(--fp-heading);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 10px;
        }

        .overview-panel-caption {
            color: var(--fp-text-secondary);
            font-size: 0.82rem;
            margin-top: 8px;
        }

        .overview-link {
            color: var(--fp-accent-blue);
            font-weight: 700;
            font-size: 0.88rem;
            margin-top: 6px;
        }

        .overview-helper-text {
            color: var(--fp-text-secondary);
            font-size: 0.88rem;
            font-weight: 600;
            margin-top: 0.1rem;
        }

        .main .stTextInput label,
        .main .stMultiSelect label,
        .main .stSelectbox label,
        .main .stRadio label {
            color: var(--fp-text-secondary) !important;
            font-weight: 700 !important;
            opacity: 1 !important;
        }

        .main div[data-baseweb="input"] > div,
        .main div[data-baseweb="select"] > div {
            background: var(--fp-input-background) !important;
            border: 1px solid var(--fp-input-border) !important;
            color: var(--fp-input-text) !important;
        }

        .main div[data-baseweb="input"] input,
        .main div[data-baseweb="select"] span {
            color: var(--fp-input-text) !important;
            opacity: 1 !important;
        }

        .main .stButton > button {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border: 1px solid var(--fp-button-border) !important;
            font-weight: 700 !important;
            box-shadow: none !important;
        }

        .overview-strip .stButton > button {
            white-space: nowrap !important;
        }

        .main .stButton > button:hover {
            background: var(--fp-button-hover) !important;
            color: var(--fp-button-text) !important;
        }

        .stButton > button,
        .stButton > button[kind="secondary"],
        .stButton > button[kind="tertiary"] {
            background: var(--fp-button-background) !important;
            color: var(--fp-button-text) !important;
            border: 1px solid var(--fp-button-border) !important;
            box-shadow: none !important;
        }

        .stButton > button[kind="secondary"]:hover,
        .stButton > button[kind="tertiary"]:hover {
            background: var(--fp-button-hover) !important;
            color: var(--fp-button-text) !important;
        }

        .stButton > button[kind="primary"] {
            background: var(--fp-button-primary-background) !important;
            color: var(--fp-button-primary-text) !important;
            border: 1px solid var(--fp-button-primary-border) !important;
        }

        .stButton > button[kind="primary"]:hover {
            background: var(--fp-button-primary-hover) !important;
            color: var(--fp-button-primary-text) !important;
            border: 1px solid var(--fp-button-primary-border) !important;
        }

        .main div[data-baseweb="input"] > div,
        .main div[data-baseweb="select"] > div,
        .main div[data-baseweb="textarea"] > div,
        .main div[data-baseweb="base-input"] > div {
            background: var(--fp-input-background) !important;
            border: 1px solid var(--fp-input-border) !important;
            color: var(--fp-input-text) !important;
        }

        .main div[data-baseweb="input"] input,
        .main div[data-baseweb="select"] span,
        .main div[data-baseweb="textarea"] textarea,
        .main input,
        .main textarea {
            color: var(--fp-input-text) !important;
            opacity: 1 !important;
        }

        .pie-chart-shell {
            display: grid;
            grid-template-columns: 180px 1fr;
            gap: 16px;
            align-items: center;
        }

        .pie-chart-ring {
            width: 180px;
            height: 180px;
            border-radius: 50%;
            position: relative;
            box-sizing: border-box;
            margin: 0 auto;
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.9);
        }

        .pie-chart-hole {
            width: 64%;
            height: 64%;
            border-radius: 50%;
            background: var(--fp-chart-hole);
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: inset 0 0 0 1px var(--fp-border-secondary);
        }

        .pie-chart-legend {
            display: flex;
            flex-direction: column;
            gap: 10px;
            min-width: 0;
        }

        .pie-legend-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            color: var(--fp-heading);
        }

        .pie-legend-left {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
        }

        .pie-legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            flex: 0 0 10px;
            box-shadow: 0 0 0 3px var(--fp-surface-primary);
        }

        .pie-legend-label {
            font-size: 0.92rem;
            font-weight: 700;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .pie-legend-value {
            color: var(--fp-text-primary);
            font-size: 0.88rem;
            font-weight: 800;
            white-space: nowrap;
        }

        .pie-legend-value span {
            color: var(--fp-text-muted);
            font-weight: 700;
        }

        .county-state-visual {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding: 10px 6px 4px 6px;
        }

        .county-state-map {
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
            min-width: 110px;
        }

        .county-state-visual svg {
            width: 84px;
            height: auto;
            filter: drop-shadow(0 8px 18px rgba(29, 78, 216, 0.22));
        }

        .county-state-icon {
            width: 84px;
            height: auto;
            display: block;
            filter: drop-shadow(0 8px 18px rgba(29, 78, 216, 0.22));
        }

        .county-state-label {
            color: var(--fp-heading);
            font-size: 0.92rem;
            font-weight: 800;
            letter-spacing: 0.01em;
        }

        .county-state-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            flex: 1;
            min-width: 0;
        }

        .county-state-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 8px 10px;
            border-radius: 14px;
            background: var(--fp-row-background);
            border: 1px solid var(--fp-row-border);
        }

        .county-state-row-name {
            color: var(--fp-heading);
            font-size: 0.94rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .county-state-row-value {
            color: var(--fp-text-muted);
            font-size: 0.8rem;
            margin-top: 2px;
        }

        .county-need-pill {
            flex: 0 0 auto;
            border-radius: 999px;
            padding: 5px 10px;
            font-size: 0.76rem;
            font-weight: 800;
            white-space: nowrap;
        }

        .county-need-high {
            background: #fff0f1;
            color: #c62d3a;
            border: 1px solid #f7c4ca;
        }

        .county-need-medium {
            background: #fff7ea;
            color: #cb7d12;
            border: 1px solid #f5ddb0;
        }

        .county-need-low {
            background: #eefaf4;
            color: #17804f;
            border: 1px solid #c8ead6;
        }

        .county-state-empty {
            color: var(--fp-text-muted);
            font-size: 0.9rem;
            padding: 10px 0;
        }

        .state-image-wrap img {
            mix-blend-mode: multiply;
            background: transparent !important;
            border-radius: 6px;
        }

        .insight-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }

        .insight-card {
            border-radius: 16px;
            padding: 14px;
            background: linear-gradient(180deg, var(--fp-success-background) 0%, var(--fp-success-background-alt) 100%);
            border: 1px solid var(--fp-success-border);
            box-shadow: 0 8px 18px var(--fp-shadow-soft);
            min-height: 132px;
        }

        .insight-card-index {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 999px;
            margin-bottom: 12px;
            background: var(--fp-accent-blue);
            color: var(--fp-surface-primary);
            font-size: 0.78rem;
            font-weight: 800;
        }

        .insight-card-text {
            color: var(--fp-text-primary);
            font-size: 0.96rem;
            font-weight: 600;
            line-height: 1.55;
        }

        .insight-card-empty {
            color: var(--fp-text-muted);
            font-size: 0.9rem;
        }

        .stDataFrame,
        .stDataFrame [role="grid"] {
            border-radius: 14px;
        }

        .stDataFrame [role="gridcell"],
        .stDataFrame [role="columnheader"] {
            color: var(--fp-text-primary) !important;
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

        @media (max-width: 1100px) {
            .overview-top-layout {
                grid-template-columns: 1fr !important;
            }

            .pie-chart-shell {
                grid-template-columns: 1fr;
                justify-items: center;
            }

            .pie-chart-legend {
                width: 100%;
            }

            .insight-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_insight_cards(insights: list[str]) -> None:
    if not insights:
        st.caption("Not enough data yet to generate insight callouts.")
        return

    rows = st.columns(2, gap="medium")
    for index, insight in enumerate(insights[:4]):
        with rows[index % 2]:
            st.markdown(
                f"""
                <div class="insight-card">
                    <div class="insight-card-index">{index + 1}</div>
                    <div class="insight-card-text">{insight}</div>
                </div>
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
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0; url={next_url}">',
                        unsafe_allow_html=True,
                    )
                    st.stop()


def render() -> None:
    st.set_page_config(page_title="Future Path Dashboard", page_icon="FP", layout="wide")
    ensure_single_dashboard("overview")
    inject_overview_styles()

    st.sidebar.markdown(
        """
        <div style="padding: 0.8rem 0 0.4rem 0;">
            <div style="font-size: 1.25rem; font-weight: 800; line-height: 1.1;">Future Path</div>
            <div style="font-size: 0.86rem; opacity: 0.82;">Youth Transition Support Dashboard</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_theme_toggle()
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Navigation")
    st.sidebar.button("Overview", use_container_width=True, disabled=True, key="sidebar_overview_disabled")
    if st.sidebar.button("Youth Dashboard", use_container_width=True, key="sidebar_switch_youth"):
        next_url = themed_url(switch_dashboard("youth_dashboard", current_key="overview"))
        st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
        st.stop()
    if st.sidebar.button("Youth Profiles", use_container_width=True, key="sidebar_switch_profile"):
        next_url = themed_url(switch_dashboard("profile_lookup", current_key="overview"))
        st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
        st.stop()
    if st.sidebar.button("AI Assistant", use_container_width=True, key="sidebar_switch_ai"):
        next_url = themed_url(switch_dashboard("ai_assistant", current_key="overview"))
        st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
        st.stop()
    if st.sidebar.button("Caseworker Dashboard", use_container_width=True, key="sidebar_switch_caseworker"):
        next_url = themed_url(switch_dashboard("caseworker_dashboard", current_key="overview"))
        st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
        st.stop()

    st.sidebar.markdown("---")
    st.sidebar.markdown("#### Quick Insight")
    st.sidebar.caption("Use the filters in the main view to narrow the dashboard by county or risk level.")

    render_top_navigation("overview")

    launch_logo_uri = load_image_data_uri(LAUNCH_LOGO_PATH)
    header_logo_html = (
        f'<img class="overview-launch-logo" src="{launch_logo_uri}" alt="Future Path" />'
        if launch_logo_uri
        else '<div class="overview-title">Future Path</div>'
    )

    st.markdown(
        f"""
        <div class="overview-shell">
            <div class="overview-header fp-brand-header">
                <div>
                    {header_logo_html}
                    <div class="overview-subtitle">Youth Transition Support Dashboard</div>
                </div>
                <div class="fp-header-meta">
                    <div class="overview-badge">Overview</div>
                    {current_theme_badge_html()}
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    launch_col1, launch_col2 = st.columns([1.1, 2.4])
    with launch_col1:
        if st.button("Open Youth Dashboard", type="primary", use_container_width=True, key="overview_launch_youth"):
            next_url = themed_url(switch_dashboard("youth_dashboard", current_key="overview"))
            st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
            st.stop()
    with launch_col2:
        st.caption("For youth users: complete intake, view assigned resources, and contact your caseworker.")

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()
    st.sidebar.write("Use the pipeline outputs to populate the database before viewing metrics.")

    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the data pipeline first, then reload this page.")
        return

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        youth_df = load_youth_overview_frame(connection)
        active_resources = load_active_resources(connection)
        youth_name_map = load_youth_identity_map(connection)

    if youth_df.empty:
        st.warning("No youth profile data found. Run ETL and reload the dashboard.")
        return

    counties = sorted(str(value) for value in youth_df["county"].dropna().unique())
    risk_levels = [
        level
        for level in ["High", "Medium", "Low", "Unknown"]
        if level in set(str(value) for value in youth_df["risk_level"].dropna().unique())
    ]

    if "county_filter" not in st.session_state:
        st.session_state["county_filter"] = []
    if "risk_level_filter" not in st.session_state:
        st.session_state["risk_level_filter"] = []

    # Apply queued quick-filter updates before widgets are created.
    if "pending_county_filter" in st.session_state:
        st.session_state["county_filter"] = st.session_state.pop("pending_county_filter")
    if "pending_risk_level_filter" in st.session_state:
        st.session_state["risk_level_filter"] = st.session_state.pop("pending_risk_level_filter")

    search_query = st.text_input(
        "Search by Youth ID or County",
        placeholder="Search by Youth ID or County...",
        key="overview_search_query",
    ).strip().lower()

    st.markdown('<div class="overview-strip">', unsafe_allow_html=True)
    filter_cols = st.columns([2.2, 1.2, 1, 1, 1, 0.85, 1.05])
    with filter_cols[0]:
        selected_counties = st.multiselect("County Filter", options=counties, key="county_filter")
    with filter_cols[1]:
        selected_risk_levels = st.multiselect("Risk Level Filter", options=risk_levels, key="risk_level_filter")
    with filter_cols[2]:
        if st.button("High Risk Only", width="stretch"):
            st.session_state["pending_risk_level_filter"] = ["High"] if "High" in risk_levels else []
            st.session_state["pending_county_filter"] = []
            st.rerun()
    with filter_cols[3]:
        if st.button("Kent County", width="stretch"):
            st.session_state["pending_county_filter"] = ["Kent"] if "Kent" in counties else []
            st.rerun()
    with filter_cols[4]:
        if st.button("Sussex County", width="stretch"):
            st.session_state["pending_county_filter"] = ["Sussex"] if "Sussex" in counties else []
            st.rerun()
    with filter_cols[5]:
        if st.button("Clear", width="stretch"):
            st.session_state["pending_county_filter"] = []
            st.session_state["pending_risk_level_filter"] = []
            st.rerun()
    with filter_cols[6]:
        if st.button("Refresh", width="stretch"):
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    filtered_youth_df = apply_filters(youth_df, selected_counties, selected_risk_levels)
    if search_query:
        filtered_youth_df = filtered_youth_df[
            filtered_youth_df["youth_id"].astype(str).str.lower().str.contains(search_query)
            | filtered_youth_df["county"].astype(str).str.lower().str.contains(search_query)
        ]

    risk_breakdown = load_risk_breakdown(filtered_youth_df)
    housing_df, employment_df = load_housing_employment_distribution(filtered_youth_df)
    education_df = load_education_distribution(filtered_youth_df)
    county_needs_df = load_county_level_needs(filtered_youth_df)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        top_resources_df = load_top_recommended_resources(
            connection,
            [str(value) for value in filtered_youth_df["youth_id"].astype(str).tolist()],
            limit=10,
        )
        candidate_queue_df = load_candidate_promotion_queue(connection)

    metrics = load_dashboard_metrics(
        filtered_youth_df,
        active_resources,
        candidate_queue_count=len(candidate_queue_df),
    )

    render_metric_cards(metrics)
    st.markdown('<div style="height: 0.35rem;"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="overview-helper-text">Showing {len(filtered_youth_df):,} of {len(youth_df):,} youth records based on current filters</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="overview-top-layout" style="display:grid;grid-template-columns:1.25fr 1fr;gap:16px;">', unsafe_allow_html=True)
    left_col, right_col = st.columns([1.25, 1])

    with left_col:
        st.markdown('<div class="overview-panel">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Risk Score Breakdown</div>', unsafe_allow_html=True)
        render_pie_chart(
            risk_breakdown,
            label_column="risk_level",
            value_column="case_count",
            title="Risk Score Breakdown",
            colors=branded_palette("risk"),
        )
        st.markdown('<div class="overview-panel-caption">Based on the latest available risk assessment data.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        chart_grid_left, chart_grid_right = st.columns(2)
        with chart_grid_left:
            st.markdown('<div class="overview-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
            render_pie_chart(
                housing_df,
                label_column="label",
                value_column="count",
                title="Housing Status",
                colors=branded_palette("housing"),
            )
            st.markdown('</div>', unsafe_allow_html=True)
        with chart_grid_right:
            st.markdown('<div class="overview-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
            render_pie_chart(
                employment_df,
                label_column="label",
                value_column="count",
                title="Employment Status",
                colors=branded_palette("employment"),
            )
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="overview-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
        render_pie_chart(
            education_df,
            label_column="label",
            value_column="count",
            title="Education Status",
            colors=branded_palette("education"),
        )
        st.markdown('<div class="overview-panel-caption">Shows where youth are in the education pathway.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with right_col:
        st.markdown('<div class="overview-side-panel">', unsafe_allow_html=True)
        render_pie_chart(
            county_needs_df,
            label_column="county",
            value_column="need_index",
            title="Delaware County Insights",
            colors=branded_palette("county"),
        )
        render_state_visual(county_needs_df)
        st.markdown('<div class="overview-panel-caption">Need Index combines high-risk, unstable housing, and unemployment signals.</div>', unsafe_allow_html=True)
        st.markdown('<div class="overview-link">View county analytics →</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="overview-side-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Data Coverage Snapshot</div>', unsafe_allow_html=True)
        coverage_rows = [
            {"Metric": "Youth profiles", "Count": int(metrics["total_youth"])},
            {"Metric": "Risk-scored youth", "Count": int(risk_breakdown["case_count"].sum()) if not risk_breakdown.empty else 0},
            {"Metric": "Resources", "Count": int(metrics["active_resources"])},
        ]
        st.dataframe(pd.DataFrame(coverage_rows), hide_index=True, width="stretch")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="overview-side-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Top Recommended Supports</div>', unsafe_allow_html=True)
        if top_resources_df.empty:
            st.info("No recommendation data found.")
        else:
            top_preview = top_resources_df.head(5).copy()
            top_preview["recommendation_count"] = top_preview["recommendation_count"].astype(int)
            st.dataframe(top_preview, hide_index=True, width="stretch")
        st.markdown('<div class="overview-link">View all recommendations →</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 0.8rem;"></div>', unsafe_allow_html=True)

    bottom_left, bottom_right = st.columns([1.55, 1])
    with bottom_left:
        st.markdown('<div class="overview-table-shell">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Recent Youth Profiles</div>', unsafe_allow_html=True)
        recent_profiles = filtered_youth_df.copy().head(5)
        recent_profiles["Name"] = recent_profiles["youth_id"].astype(str).map(youth_name_map).fillna(recent_profiles["youth_id"])
        display_profiles = recent_profiles[["youth_id", "Name", "county", "risk_level", "housing", "employment"]].rename(
            columns={"youth_id": "Youth ID", "county": "County", "risk_level": "Risk Level", "housing": "Housing Status", "employment": "Employment"}
        )
        st.dataframe(display_profiles, hide_index=True, width="stretch")
        st.markdown('<div class="overview-link">View all profiles →</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with bottom_right:
        st.markdown('<div id="candidate-queue"></div>', unsafe_allow_html=True)
        st.markdown('<div class="overview-side-panel">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Candidate Queue</div>', unsafe_allow_html=True)
        st.caption("Completed candidate intakes ready to promote into teen records.")
        if candidate_queue_df.empty:
            st.info("No candidate intakes are waiting for promotion.")
            if st.button("Start Candidate Intake", width="stretch", key="overview_start_candidate_intake"):
                next_url = switch_dashboard("ai_assistant", current_key="overview")
                st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                st.stop()
        else:
            st.dataframe(candidate_queue_df, hide_index=True, width="stretch")
            action_col1, action_col2, action_col3 = st.columns(3)
            with action_col1:
                if st.button("Start Candidate Intake", width="stretch", key="overview_start_candidate_intake_from_queue"):
                    next_url = switch_dashboard("ai_assistant", current_key="overview")
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()
            with action_col2:
                if st.button("Promote In Caseworker", width="stretch", key="overview_promote_candidate"):
                    next_url = switch_dashboard("caseworker_dashboard", current_key="overview")
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()
            with action_col3:
                if st.button("Open AI Assistant", width="stretch", key="overview_open_ai_assistant"):
                    next_url = switch_dashboard("ai_assistant", current_key="overview")
                    st.markdown(f'<meta http-equiv="refresh" content="0; url={next_url}">', unsafe_allow_html=True)
                    st.stop()
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="overview-side-panel">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Insight Callouts</div>', unsafe_allow_html=True)
        insights = build_insight_callouts(metrics, county_needs_df, top_resources_df, risk_breakdown)
        render_insight_cards(insights)
        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    render()
