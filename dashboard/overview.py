from __future__ import annotations

import sqlite3
from pathlib import Path

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
    grouped["need_index"] = (
        grouped["high_risk_cases"] * 3
        + grouped["unstable_housing_cases"] * 2
        + grouped["unemployment_cases"]
    )
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
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Youth Records", f"{int(metrics['total_youth']):,}")
    col2.metric("High-Risk Cases", f"{int(metrics['high_risk_cases']):,}")
    col3.metric("Stable Housing %", f"{float(metrics['stable_housing_pct']):.1f}%")
    col4.metric("Employment %", f"{float(metrics['employment_pct']):.1f}%")
    col5.metric("Active Resources", f"{int(metrics['active_resources']):,}")


def render() -> None:
    st.set_page_config(page_title="Future Path Dashboard", page_icon="FP", layout="wide")

    st.title("Future Path Dashboard Overview")
    st.caption("MVP insights powered by the SQLite project database")

    launch_col1, launch_col2 = st.columns([1, 3])
    with launch_col1:
        if st.button("🤖 Future Path AI Assistant", width="stretch"):
            try:
                st.switch_page("dashboard/ai_assistant.py")
            except Exception:
                st.info("Open dashboard/ai_assistant.py from Streamlit multipage navigation.")
    with launch_col2:
        st.write("Start a guided intake assessment and generate recommendations.")

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

    preset_col1, preset_col2, preset_col3, preset_col4 = st.columns(4)
    with preset_col1:
        if st.button("High Risk Only", width="stretch"):
            st.session_state["risk_level_filter"] = ["High"] if "High" in risk_levels else []
            st.session_state["county_filter"] = []
    with preset_col2:
        if st.button("Kent County", width="stretch"):
            st.session_state["county_filter"] = ["Kent"] if "Kent" in counties else []
    with preset_col3:
        if st.button("Sussex County", width="stretch"):
            st.session_state["county_filter"] = ["Sussex"] if "Sussex" in counties else []
    with preset_col4:
        if st.button("Reset Filters", width="stretch"):
            st.session_state["county_filter"] = []
            st.session_state["risk_level_filter"] = []

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        selected_counties = st.multiselect(
            "County Filter",
            options=counties,
            key="county_filter",
        )
    with filter_col2:
        selected_risk_levels = st.multiselect(
            "Risk Level Filter",
            options=risk_levels,
            key="risk_level_filter",
        )

    filtered_youth_df = apply_filters(youth_df, selected_counties, selected_risk_levels)

    metrics = load_dashboard_metrics(filtered_youth_df, active_resources)
    risk_breakdown = load_risk_breakdown(filtered_youth_df)
    housing_df, employment_df = load_housing_employment_distribution(filtered_youth_df)
    education_df = load_education_distribution(filtered_youth_df)
    county_needs_df = load_county_level_needs(filtered_youth_df)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        top_resources_df = load_top_recommended_resources(
            connection,
            filtered_youth_ids=[str(value) for value in filtered_youth_df["youth_id"].astype(str).tolist()],
            limit=10,
        )

    render_metric_cards(metrics)

    st.divider()

    st.caption(f"Showing {len(filtered_youth_df):,} of {len(youth_df):,} youth records based on current filters")

    left, right = st.columns([1.2, 1])

    with left:
        st.subheader("Risk Score Breakdown")
        if risk_breakdown.empty:
            st.warning("No risk score data found. Run risk scoring to populate this chart.")
        else:
            chart_df = risk_breakdown.set_index("risk_level")
            st.bar_chart(chart_df)
            st.dataframe(risk_breakdown, hide_index=True, width="stretch")

    with right:
        st.subheader("Data Coverage Snapshot")
        coverage_rows = [
            {"Metric": "Youth profiles", "Count": int(metrics["total_youth"])},
            {"Metric": "Risk-scored youth", "Count": int(risk_breakdown["case_count"].sum()) if not risk_breakdown.empty else 0},
            {"Metric": "Resources", "Count": int(metrics["active_resources"])},
        ]
        st.dataframe(pd.DataFrame(coverage_rows), hide_index=True, width="stretch")

    st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Housing Status Distribution")
        if housing_df.empty:
            st.info("No housing data found.")
        else:
            st.bar_chart(housing_df.set_index("label"))

    with c2:
        st.subheader("Employment Status Distribution")
        if employment_df.empty:
            st.info("No employment data found.")
        else:
            st.bar_chart(employment_df.set_index("label"))

    st.divider()

    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Education Status Distribution")
        st.caption("Shows where youth are in the education pathway.")
        if education_df.empty:
            st.info("No education data found.")
        else:
            st.bar_chart(education_df.set_index("label"))

    with c4:
        st.subheader("Top Recommended Resources")
        st.caption("Most frequently recommended supports from the database (or processed matches fallback).")
        if top_resources_df.empty:
            st.info("No recommendation data found.")
        else:
            st.bar_chart(top_resources_df.set_index("resource_name"))
            st.dataframe(top_resources_df, hide_index=True, width="stretch")

    st.divider()

    st.subheader("Insight Callouts")
    insights = build_insight_callouts(metrics, county_needs_df, top_resources_df, risk_breakdown)
    if not insights:
        st.info("Not enough data yet to generate insight callouts.")
    else:
        for insight in insights:
            st.success(insight)

    st.divider()

    st.subheader("County-Level Needs")
    st.caption("Need Index combines high-risk, unstable housing, and unemployment signals to highlight county demand.")
    if county_needs_df.empty:
        st.info("No county-level data found.")
    else:
        st.bar_chart(county_needs_df.set_index("county")[["need_index"]])
        st.dataframe(county_needs_df, hide_index=True, width="stretch")


if __name__ == "__main__":
    render()
