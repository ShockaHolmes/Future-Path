from __future__ import annotations

import sqlite3
from pathlib import Path
from textwrap import dedent

import pandas as pd
import streamlit as st


DEFAULT_DB_PATH = Path("database/future_path.db")


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


def load_dashboard_metrics(filtered_youth_df: pd.DataFrame, active_resources: int) -> dict[str, float | int]:
    metrics: dict[str, float | int] = {
        "total_youth": 0,
        "high_risk_cases": 0,
        "stable_housing_pct": 0.0,
        "employment_pct": 0.0,
        "active_resources": active_resources,
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
        ("Stable Housing %", f"{float(metrics['stable_housing_pct']):.0f}%", "current view"),
        ("Employed %", f"{float(metrics['employment_pct']):.0f}%", "current view"),
        ("Active Resources", f"{int(metrics['active_resources']):,}", "catalog count"),
    ]
    cols = st.columns(5)
    for index, (title, value, footnote) in enumerate(cards):
        with cols[index]:
            st.markdown(
                f"""
                <div class="overview-kpi-card">
                    <div class="overview-kpi-label">{title}</div>
                    <div class="overview-kpi-value">{value}</div>
                    <div class="overview-kpi-footnote">{footnote}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


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
    center_value = f"{total:,.0f}" if total >= 100 else f"{total:.0f}"

    st.markdown(
        dedent(
            f"""
            <div class="pie-chart-shell">
                <div class="pie-chart-ring" style="background: conic-gradient({gradient_css});">
                    <div class="pie-chart-hole">
                        <div class="pie-chart-total">{center_value}</div>
                        <div class="pie-chart-total-label">Total</div>
                    </div>
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
    county_rows = []
    if not top_counties.empty:
        max_need = max(float(top_counties["need_index"].max()), 1.0)
        for _, row in top_counties.iterrows():
            need_value = int(row["need_index"])
            if need_value >= max_need * 0.75:
                tag = "High Need"
                tag_class = "county-need-high"
            elif need_value >= max_need * 0.45:
                tag = "Moderate"
                tag_class = "county-need-medium"
            else:
                tag = "Lower Need"
                tag_class = "county-need-low"
            county_rows.append(
                f"""
                <div class="county-state-row">
                    <div>
                        <div class="county-state-row-name">{row['county']}</div>
                        <div class="county-state-row-value">{need_value:,} need index</div>
                    </div>
                    <div class="county-need-pill {tag_class}">{tag}</div>
                </div>
                """
            )

    county_rows_html = "".join(county_rows) if county_rows else "<div class='county-state-empty'>No county data available.</div>"

    st.markdown(
        f"""
        <div class="county-state-visual">
            <div class="county-state-map">
                <svg viewBox="0 0 120 220" role="img" aria-label="Delaware state visual">
                    <defs>
                        <linearGradient id="delawareFill" x1="0%" y1="0%" x2="100%" y2="100%">
                            <stop offset="0%" stop-color="#17a2b8" />
                            <stop offset="100%" stop-color="#3f7bd9" />
                        </linearGradient>
                    </defs>
                    <path d="M34 8 L88 8 L96 170 L88 180 L84 214 L42 214 L38 180 L30 176 L24 126 L28 70 Z" fill="url(#delawareFill)" opacity="0.96"/>
                    <path d="M34 8 L88 8 L96 170 L88 180 L84 214 L42 214 L38 180 L30 176 L24 126 L28 70 Z" fill="none" stroke="#ffffff" stroke-width="3"/>
                    <path d="M28 120 L96 120" stroke="#ffffff" stroke-width="2.5" opacity="0.9"/>
                    <path d="M40 60 L82 60" stroke="#ffffff" stroke-width="2.5" opacity="0.9"/>
                    <circle cx="56" cy="52" r="3.5" fill="#ffffff" opacity="0.95"/>
                    <circle cx="72" cy="96" r="3.5" fill="#ffffff" opacity="0.95"/>
                    <circle cx="62" cy="148" r="3.5" fill="#ffffff" opacity="0.95"/>
                </svg>
                <div class="county-state-label">Delaware</div>
            </div>
            <div class="county-state-list">
                {county_rows_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def inject_overview_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        .stApp {
            background: #f5f8fc;
            color: #10223f;
            font-family: 'Inter', sans-serif;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0b2440 0%, #081a31 100%);
            color: #ffffff;
            border-right: 1px solid rgba(255, 255, 255, 0.08);
        }

        [data-testid="stSidebar"] * {
            color: #eef5ff !important;
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
            border: 1px solid #d9e4f2;
            border-radius: 18px;
            background: #ffffff;
            box-shadow: 0 10px 22px rgba(16, 34, 63, 0.04);
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
            border-bottom: 1px solid #e5edf6;
        }

        .overview-title {
            font-size: 2rem;
            font-weight: 800;
            color: #0c1f44;
            line-height: 1.1;
        }

        .overview-subtitle {
            color: #5d6f86;
            font-size: 0.95rem;
            margin-top: 4px;
        }

        .overview-badge {
            padding: 8px 12px;
            border-radius: 999px;
            background: #eef5ff;
            color: #234e97;
            font-weight: 700;
            font-size: 0.88rem;
        }

        .overview-kpi-card {
            border: 1px solid #dce7f3;
            border-radius: 16px;
            background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
            padding: 14px 14px 12px 14px;
            box-shadow: 0 8px 18px rgba(16, 34, 63, 0.04);
            min-height: 110px;
        }

        .overview-kpi-label {
            color: #35557f;
            font-size: 0.9rem;
            font-weight: 700;
            margin-bottom: 10px;
        }

        .overview-kpi-value {
            color: #0c1f44;
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
        }

        .overview-kpi-footnote {
            color: #5f728e;
            font-size: 0.78rem;
            margin-top: 8px;
        }

        .overview-panel-title {
            color: #0c1f44;
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 10px;
        }

        .overview-panel-caption {
            color: #647892;
            font-size: 0.82rem;
            margin-top: 8px;
        }

        .overview-link {
            color: #2b6cb0;
            font-weight: 700;
            font-size: 0.88rem;
            margin-top: 6px;
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
            padding: 10px;
            box-sizing: border-box;
            margin: 0 auto;
            box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.9);
        }

        .pie-chart-hole {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            text-align: center;
            box-shadow: inset 0 0 0 1px #e5edf6;
        }

        .pie-chart-total {
            color: #0c1f44;
            font-size: 1.7rem;
            font-weight: 800;
            line-height: 1;
        }

        .pie-chart-total-label {
            color: #5f728e;
            font-size: 0.82rem;
            font-weight: 700;
            margin-top: 4px;
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
            color: #0c1f44;
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
            box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.9);
        }

        .pie-legend-label {
            font-size: 0.92rem;
            font-weight: 700;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .pie-legend-value {
            color: #35557f;
            font-size: 0.88rem;
            font-weight: 800;
            white-space: nowrap;
        }

        .pie-legend-value span {
            color: #5f728e;
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

        .county-state-label {
            color: #0c1f44;
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
            background: #f8fbff;
            border: 1px solid #e1e9f6;
        }

        .county-state-row-name {
            color: #0c1f44;
            font-size: 0.94rem;
            font-weight: 800;
            line-height: 1.2;
        }

        .county-state-row-value {
            color: #5f728e;
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
            color: #5f728e;
            font-size: 0.9rem;
            padding: 10px 0;
        }

        .stDataFrame,
        .stDataFrame [role="grid"] {
            border-radius: 14px;
        }

        .stDataFrame [role="gridcell"],
        .stDataFrame [role="columnheader"] {
            color: #10223f !important;
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
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render() -> None:
    st.set_page_config(page_title="Future Path Dashboard", page_icon="FP", layout="wide")
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
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Navigation")
    if st.sidebar.button("Overview", width="stretch"):
        st.switch_page("dashboard/overview.py")
    if st.sidebar.button("Youth Profiles", width="stretch"):
        try:
            st.switch_page("dashboard/profile_lookup.py")
        except Exception:
            st.info("Open dashboard/profile_lookup.py from Streamlit multipage navigation.")
    if st.sidebar.button("AI Assistant", width="stretch"):
        try:
            st.switch_page("dashboard/ai_assistant.py")
        except Exception:
            st.info("Open dashboard/ai_assistant.py from Streamlit multipage navigation.")
    if st.sidebar.button("Caseworker Dashboard", width="stretch"):
        try:
            st.switch_page("dashboard/caseworker_dashboard.py")
        except Exception:
            st.info("Open dashboard/caseworker_dashboard.py from Streamlit multipage navigation.")
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### Quick Insight")
    st.sidebar.caption("Use the filters in the main view to narrow the dashboard by county or risk level.")

    st.markdown(
        """
        <div class="overview-shell">
            <div class="overview-header">
                <div>
                    <div class="overview-title">Future Path</div>
                    <div class="overview-subtitle">Youth Transition Support Dashboard</div>
                </div>
                <div class="overview-badge">Overview</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

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

    search_query = st.text_input(
        "Search by Youth ID or County",
        placeholder="Search by Youth ID or County...",
        key="overview_search_query",
    ).strip().lower()

    st.markdown('<div class="overview-strip">', unsafe_allow_html=True)
    filter_cols = st.columns([2.2, 1.2, 1, 1, 1, 0.8, 0.8])
    with filter_cols[0]:
        selected_counties = st.multiselect("County Filter", options=counties, key="county_filter")
    with filter_cols[1]:
        selected_risk_levels = st.multiselect("Risk Level Filter", options=risk_levels, key="risk_level_filter")
    with filter_cols[2]:
        if st.button("High Risk Only", width="stretch"):
            st.session_state["risk_level_filter"] = ["High"] if "High" in risk_levels else []
            st.session_state["county_filter"] = []
            st.rerun()
    with filter_cols[3]:
        if st.button("Kent County", width="stretch"):
            st.session_state["county_filter"] = ["Kent"] if "Kent" in counties else []
            st.rerun()
    with filter_cols[4]:
        if st.button("Sussex County", width="stretch"):
            st.session_state["county_filter"] = ["Sussex"] if "Sussex" in counties else []
            st.rerun()
    with filter_cols[5]:
        if st.button("Clear", width="stretch"):
            st.session_state["county_filter"] = []
            st.session_state["risk_level_filter"] = []
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

    metrics = load_dashboard_metrics(filtered_youth_df, active_resources)
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

    render_metric_cards(metrics)
    st.markdown('<div style="height: 0.35rem;"></div>', unsafe_allow_html=True)
    st.caption(f"Showing {len(filtered_youth_df):,} of {len(youth_df):,} youth records based on current filters")

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
            colors=["#d7263d", "#f4a261", "#4f9d69", "#90a4ae"],
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
                colors=["#1d4ed8", "#38bdf8", "#7c3aed", "#f59e0b", "#10b981", "#ef4444"],
            )
            st.markdown('</div>', unsafe_allow_html=True)
        with chart_grid_right:
            st.markdown('<div class="overview-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
            render_pie_chart(
                employment_df,
                label_column="label",
                value_column="count",
                title="Employment Status",
                colors=["#0f766e", "#14b8a6", "#f97316", "#8b5cf6", "#ef4444", "#22c55e"],
            )
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="overview-panel" style="margin-top: 16px;">', unsafe_allow_html=True)
        render_pie_chart(
            education_df,
            label_column="label",
            value_column="count",
            title="Education Status",
            colors=["#2563eb", "#06b6d4", "#8b5cf6", "#f59e0b", "#ef4444", "#10b981"],
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
            colors=["#7c3aed", "#2563eb", "#14b8a6", "#f59e0b", "#ef4444", "#22c55e"],
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
        st.markdown('<div class="overview-side-panel">', unsafe_allow_html=True)
        st.markdown('<div class="overview-panel-title">Insight Callouts</div>', unsafe_allow_html=True)
        insights = build_insight_callouts(metrics, county_needs_df, top_resources_df, risk_breakdown)
        if not insights:
            st.info("Not enough data yet to generate insight callouts.")
        else:
            insight_cols = st.columns(2)
            for index, insight in enumerate(insights[:4]):
                with insight_cols[index % 2]:
                    st.success(insight)
        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    render()
