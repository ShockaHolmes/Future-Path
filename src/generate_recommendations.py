from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


NEED_PRIORITY = {
    "unstable_housing": 50,
    "unemployment": 40,
    "education_need": 35,
    "lack_support": 30,
    "wellness_need": 30,
    "general_support": 10,
}

NEED_TAG_MAP = {
    "unstable_housing": {"housing", "transitional_housing", "homelessness", "housing_navigation"},
    "unemployment": {"employment", "job_training", "workforce_development", "paid_work", "career_pathway"},
    "education_need": {"GED", "academic_support", "education", "credentials", "career_readiness"},
    "lack_support": {"mentorship", "community_support", "positive_adult", "peer_support"},
    "wellness_need": {"mental_health", "counseling", "health", "teen_health", "substance_use", "crisis"},
    "general_support": {"general_support"},
}

RESOURCE_DEFAULT_PRIORITY = {"High": 30, "Medium": 20, "Low": 10}
OUTPUT_PRIORITY_RANK = {"High": 1, "Medium": 2, "Low": 3}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and store youth recommendations from needs and risks.")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Path to SQLite database.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Max recommendations per youth.",
    )
    parser.add_argument(
        "--source",
        default="rules_recommender_v1",
        help="Recommendation source label stored in recommendations.recommendation_source.",
    )
    return parser.parse_args()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _split_tags(raw_value: str | None) -> set[str]:
    if not raw_value:
        return set()
    return {tag.strip() for tag in str(raw_value).split(";") if tag.strip()}


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"true", "1", "yes", "y", "urgent", "sometimes", "needed"}


def ensure_resources_table_populated(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "resources"):
        raise ValueError("Missing resources table. Apply relational schema before recommendations.")

    resource_columns = _table_columns(connection, "resources")
    if "contact_email" not in resource_columns:
        connection.execute("ALTER TABLE resources ADD COLUMN contact_email TEXT")

    resources_count = connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    if resources_count > 0 and not _table_exists(connection, "youth_resources"):
        return

    if not _table_exists(connection, "youth_resources"):
        raise ValueError("No resources data found. Load resources before generating recommendations.")

    youth_resource_columns = _table_columns(connection, "youth_resources")
    contact_phone_expr = "COALESCE(contact_phone, '')" if "contact_phone" in youth_resource_columns else "''"
    contact_email_expr = "COALESCE(contact_email, '')" if "contact_email" in youth_resource_columns else "''"
    website_expr = "COALESCE(website, '')" if "website" in youth_resource_columns else "''"

    connection.execute(
        f"""
        INSERT OR IGNORE INTO resources (
            resource_id,
            resource_name,
            category,
            need_tags,
            service_area,
            county,
            city,
            state,
            eligibility_age_min,
            eligibility_age_max,
            description,
            referral_method,
            contact_phone,
            contact_email,
            website,
            ai_match_rules,
            default_priority,
            caseworker_notes
        )
        SELECT
            resource_id,
            resource_name,
            category,
            need_tags,
            service_area,
            county,
            city,
            state,
            eligibility_age_min,
            eligibility_age_max,
            description,
            referral_method,
            {contact_phone_expr},
            {contact_email_expr},
            {website_expr},
            ai_match_rules,
            default_priority,
            caseworker_notes
        FROM youth_resources
        """
    )

    contact_phone_update_expr = (
        "COALESCE((SELECT y.contact_phone FROM youth_resources y WHERE y.resource_id = resources.resource_id), '')"
        if "contact_phone" in youth_resource_columns
        else "''"
    )
    contact_email_update_expr = (
        "COALESCE((SELECT y.contact_email FROM youth_resources y WHERE y.resource_id = resources.resource_id), '')"
        if "contact_email" in youth_resource_columns
        else "''"
    )
    website_update_expr = (
        "COALESCE((SELECT y.website FROM youth_resources y WHERE y.resource_id = resources.resource_id), '')"
        if "website" in youth_resource_columns
        else "''"
    )

    connection.execute(
        f"""
        UPDATE resources
        SET
            resource_name = COALESCE((SELECT y.resource_name FROM youth_resources y WHERE y.resource_id = resources.resource_id), resource_name),
            category = COALESCE((SELECT y.category FROM youth_resources y WHERE y.resource_id = resources.resource_id), category),
            need_tags = COALESCE((SELECT y.need_tags FROM youth_resources y WHERE y.resource_id = resources.resource_id), need_tags),
            service_area = COALESCE((SELECT y.service_area FROM youth_resources y WHERE y.resource_id = resources.resource_id), service_area),
            county = COALESCE((SELECT y.county FROM youth_resources y WHERE y.resource_id = resources.resource_id), county),
            city = COALESCE((SELECT y.city FROM youth_resources y WHERE y.resource_id = resources.resource_id), city),
            state = COALESCE((SELECT y.state FROM youth_resources y WHERE y.resource_id = resources.resource_id), state),
            eligibility_age_min = COALESCE((SELECT y.eligibility_age_min FROM youth_resources y WHERE y.resource_id = resources.resource_id), eligibility_age_min),
            eligibility_age_max = COALESCE((SELECT y.eligibility_age_max FROM youth_resources y WHERE y.resource_id = resources.resource_id), eligibility_age_max),
            description = COALESCE((SELECT y.description FROM youth_resources y WHERE y.resource_id = resources.resource_id), description),
            referral_method = COALESCE((SELECT y.referral_method FROM youth_resources y WHERE y.resource_id = resources.resource_id), referral_method),
            contact_phone = {contact_phone_update_expr},
            contact_email = {contact_email_update_expr},
            website = {website_update_expr},
            ai_match_rules = COALESCE((SELECT y.ai_match_rules FROM youth_resources y WHERE y.resource_id = resources.resource_id), ai_match_rules),
            default_priority = COALESCE((SELECT y.default_priority FROM youth_resources y WHERE y.resource_id = resources.resource_id), default_priority),
            caseworker_notes = COALESCE((SELECT y.caseworker_notes FROM youth_resources y WHERE y.resource_id = resources.resource_id), caseworker_notes)
        WHERE EXISTS (SELECT 1 FROM youth_resources y WHERE y.resource_id = resources.resource_id)
        """
    )


def ensure_recommendations_table_integrity(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "recommendations"):
        raise ValueError("Missing recommendations table. Apply relational schema before recommendations.")

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'recommendations'"
    ).fetchone()
    table_sql = table_sql_row[0] if table_sql_row else ""
    if "risk_scores_legacy" not in table_sql:
        return

    connection.execute("ALTER TABLE recommendations RENAME TO recommendations_legacy")
    connection.execute(
        """
        CREATE TABLE recommendations (
            recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            risk_score_id INTEGER,
            intake_session_id TEXT,
            match_score REAL,
            priority_rank INTEGER,
            recommendation_reason TEXT,
            recommendation_source TEXT NOT NULL DEFAULT 'ai_matcher',
            recommendation_status TEXT NOT NULL DEFAULT 'proposed' CHECK (
                recommendation_status IN ('proposed', 'reviewed', 'accepted', 'rejected')
            ),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (resource_id) REFERENCES resources(resource_id) ON DELETE RESTRICT,
            FOREIGN KEY (risk_score_id) REFERENCES risk_scores(risk_score_id) ON DELETE SET NULL,
            FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE SET NULL,
            UNIQUE (youth_id, resource_id, intake_session_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO recommendations (
            recommendation_id,
            youth_id,
            resource_id,
            risk_score_id,
            intake_session_id,
            match_score,
            priority_rank,
            recommendation_reason,
            recommendation_source,
            recommendation_status,
            created_at
        )
        SELECT
            recommendation_id,
            youth_id,
            resource_id,
            risk_score_id,
            intake_session_id,
            match_score,
            priority_rank,
            recommendation_reason,
            recommendation_source,
            recommendation_status,
            created_at
        FROM recommendations_legacy
        """
    )
    connection.execute("DROP TABLE recommendations_legacy")


def load_latest_intake_context(connection: sqlite3.Connection) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    if not _table_exists(connection, "intake_sessions") or not _table_exists(connection, "intake_answers"):
        return {}, {}

    intake_columns = _table_columns(connection, "intake_sessions")
    has_profile_type = "profile_type" in intake_columns
    has_session_status = "session_status" in intake_columns

    status_filter = "AND session_status = 'completed'" if has_session_status else ""
    profile_filter = "AND profile_type = 'youth'" if has_profile_type else ""

    rows = connection.execute(
        """
        SELECT s.youth_id, s.intake_session_id, a.question_key, a.answer_value
        FROM intake_sessions s
        JOIN intake_answers a ON a.intake_session_id = s.intake_session_id
        JOIN (
            SELECT youth_id, MAX(COALESCE(completed_at, started_at)) AS latest_session_at
            FROM intake_sessions
            WHERE youth_id IS NOT NULL
            {profile_filter}
            {status_filter}
            GROUP BY youth_id
        ) latest ON latest.youth_id = s.youth_id AND latest.latest_session_at = COALESCE(s.completed_at, s.started_at)
        WHERE s.youth_id IS NOT NULL
        {profile_filter}
        {status_filter}
        """
        .format(profile_filter=profile_filter, status_filter=status_filter)
    ).fetchall()

    answers_by_youth: dict[str, dict[str, str]] = {}
    latest_session_by_youth: dict[str, str] = {}
    for youth_id, intake_session_id, question_key, answer_value in rows:
        normalized_youth_id = str(youth_id)
        latest_session_by_youth[normalized_youth_id] = str(intake_session_id)
        answers_by_youth.setdefault(normalized_youth_id, {})[str(question_key)] = (
            "" if answer_value is None else str(answer_value)
        )
    return answers_by_youth, latest_session_by_youth


def load_latest_risk_factors(connection: sqlite3.Connection) -> dict[str, set[str]]:
    if not _table_exists(connection, "risk_scores"):
        return {}

    rows = connection.execute(
        """
        SELECT rs.youth_id, rs.risk_factors_json
        FROM risk_scores rs
        JOIN (
            SELECT youth_id, MAX(calculated_at) AS latest_calculated_at
            FROM risk_scores
            GROUP BY youth_id
        ) latest ON latest.youth_id = rs.youth_id AND latest.latest_calculated_at = rs.calculated_at
        """
    ).fetchall()

    by_youth: dict[str, set[str]] = {}
    for youth_id, payload in rows:
        factor_names: set[str] = set()
        if payload:
            parsed = json.loads(payload)
            all_factors = parsed.get("all_triggered_factors", [])
            for factor in all_factors:
                name = factor.get("name")
                if name:
                    factor_names.add(str(name))
        by_youth[str(youth_id)] = factor_names
    return by_youth


def determine_needs(
    youth_row: sqlite3.Row,
    intake_answers: dict[str, str],
    risk_factor_names: set[str],
) -> list[tuple[str, str]]:
    needs: list[tuple[str, str]] = []

    housing = str(youth_row["housing"])
    employment = str(youth_row["employment"])
    education = str(youth_row["education"])
    mentor_status = str(youth_row["mentor_status"])
    placement_count = int(youth_row["placement_count"])
    prior_homelessness = str(youth_row["prior_homelessness"])

    if housing in {"Couch surfing", "Temporary shelter", "Transitional housing", "At risk of homelessness"}:
        needs.append(("unstable_housing", f"housing status is {housing}"))
    elif prior_homelessness == "Yes" or "homelessness_risk" in risk_factor_names:
        needs.append(("unstable_housing", "prior homelessness or housing instability risk detected"))

    if employment == "Unemployed":
        needs.append(("unemployment", "youth is currently unemployed"))

    if education in {"Not enrolled", "Middle school", "High school"}:
        needs.append(("education_need", f"education level indicates GED/tutoring support need ({education})"))

    if mentor_status == "Not assigned" or placement_count >= 5:
        needs.append(("lack_support", "limited support network inferred from mentor status or placement history"))

    wellness_signal = (
        _truthy(intake_answers.get("mental_health"))
        or _truthy(intake_answers.get("mental_health_need"))
        or _truthy(intake_answers.get("health"))
        or _truthy(intake_answers.get("health_need"))
        or _truthy(intake_answers.get("healthcare_needed"))
        or _truthy(intake_answers.get("health_insurance_needed"))
        or "mental_health" in risk_factor_names
        or "health" in risk_factor_names
    )
    if wellness_signal:
        needs.append(("wellness_need", "wellness or counseling support need identified"))

    if not needs:
        needs.append(("general_support", "baseline support recommendation"))

    return needs


def determine_priority_level(match_score: int) -> str:
    if match_score >= 85:
        return "High"
    if match_score >= 50:
        return "Medium"
    return "Low"


def create_recommendations_for_youth(
    youth_row: sqlite3.Row,
    resources: list[sqlite3.Row],
    needs: list[tuple[str, str]],
    top_n: int,
) -> list[dict[str, object]]:
    age = int(youth_row["age"])
    county = str(youth_row["county"])
    candidate_by_resource: dict[str, dict[str, object]] = {}

    for need_key, need_reason in needs:
        target_tags = NEED_TAG_MAP[need_key]
        need_weight = NEED_PRIORITY[need_key]

        for resource in resources:
            min_age = int(resource["eligibility_age_min"])
            max_age = int(resource["eligibility_age_max"])
            if age < min_age or age > max_age:
                continue

            resource_county = str(resource["county"])
            service_area = str(resource["service_area"])
            county_match = resource_county == county
            statewide_match = resource_county == "Statewide" or service_area == "Statewide"
            if not county_match and not statewide_match:
                continue

            resource_tags = _split_tags(resource["need_tags"])
            overlap = sorted(target_tags & resource_tags)
            if not overlap:
                continue

            default_priority = str(resource["default_priority"])
            score = need_weight + RESOURCE_DEFAULT_PRIORITY.get(default_priority, 0)
            score += 5 if county_match else 3
            score += min(len(overlap) * 2, 8)

            resource_id = str(resource["resource_id"])
            if resource_id not in candidate_by_resource:
                candidate_by_resource[resource_id] = {
                    "resource_id": resource_id,
                    "resource_name": str(resource["resource_name"]),
                    "match_score": score,
                    "priority_level": determine_priority_level(score),
                    "need_keys": [need_key],
                    "reason_parts": [
                        f"matched {need_key} ({need_reason}) using tags {', '.join(overlap)}"
                    ],
                }
            else:
                existing = candidate_by_resource[resource_id]
                existing["match_score"] = int(existing["match_score"]) + score
                existing["priority_level"] = determine_priority_level(int(existing["match_score"]))
                existing["need_keys"].append(need_key)
                existing["reason_parts"].append(
                    f"matched {need_key} ({need_reason}) using tags {', '.join(overlap)}"
                )

    candidates = list(candidate_by_resource.values())
    candidates.sort(key=lambda row: (-int(row["match_score"]), str(row["resource_name"])))

    final_rows: list[dict[str, object]] = []
    for idx, candidate in enumerate(candidates[:top_n], start=1):
        priority_level = str(candidate["priority_level"])
        reason = (
            f"Priority level: {priority_level}. "
            + " | ".join(candidate["reason_parts"])
        )
        final_rows.append(
            {
                "resource_id": str(candidate["resource_id"]),
                "match_score": float(candidate["match_score"]),
                "priority_rank": OUTPUT_PRIORITY_RANK[priority_level],
                "recommendation_reason": reason,
            }
        )

    return final_rows


def save_recommendations(
    connection: sqlite3.Connection,
    source: str,
    top_n: int,
) -> tuple[int, int]:
    if not _table_exists(connection, "youth_profiles"):
        raise ValueError("Missing youth_profiles table. Load youth profiles before recommendations.")
    ensure_recommendations_table_integrity(connection)

    ensure_resources_table_populated(connection)

    resources = connection.execute(
        """
        SELECT resource_id, resource_name, need_tags, county, service_area, eligibility_age_min, eligibility_age_max, default_priority
        FROM resources
        """
    ).fetchall()

    youth_rows = connection.execute(
        """
        SELECT youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
        FROM youth_profiles
        ORDER BY youth_id
        """
    ).fetchall()
    if not youth_rows:
        raise ValueError("No youth profiles found. Nothing to recommend.")

    intake_by_youth, intake_session_by_youth = load_latest_intake_context(connection)
    risk_factors_by_youth = load_latest_risk_factors(connection)

    connection.execute("DELETE FROM recommendations WHERE recommendation_source = ?", (source,))

    inserted = 0
    for youth in youth_rows:
        youth_id = str(youth["youth_id"])
        intake_session_id = intake_session_by_youth.get(youth_id)
        needs = determine_needs(
            youth,
            intake_by_youth.get(youth_id, {}),
            risk_factors_by_youth.get(youth_id, set()),
        )

        rows = create_recommendations_for_youth(youth, resources, needs, top_n=top_n)
        if not rows:
            fallback = connection.execute(
                """
                SELECT resource_id
                FROM resources
                WHERE need_tags LIKE '%general_support%'
                ORDER BY default_priority = 'High' DESC, resource_name ASC
                LIMIT 1
                """
            ).fetchone()
            if fallback is not None:
                rows = [
                    {
                        "resource_id": str(fallback["resource_id"]),
                        "match_score": 25.0,
                        "priority_rank": OUTPUT_PRIORITY_RANK["Low"],
                        "recommendation_reason": "Priority level: Low. fallback general support recommendation.",
                    }
                ]

        for row in rows:
            connection.execute(
                """
                INSERT INTO recommendations (
                    youth_id,
                    resource_id,
                    intake_session_id,
                    match_score,
                    priority_rank,
                    recommendation_reason,
                    recommendation_source,
                    recommendation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')
                """,
                (
                    youth_id,
                    row["resource_id"],
                    intake_session_id,
                    row["match_score"],
                    row["priority_rank"],
                    row["recommendation_reason"],
                    source,
                ),
            )
            inserted += 1

    return len(youth_rows), inserted


def main() -> None:
    args = parse_args()
    if args.top_n < 1:
        raise SystemExit("Recommendation generation failed: --top-n must be at least 1")

    try:
        if not args.database.exists():
            raise FileNotFoundError(f"Database not found: {args.database}")

        with sqlite3.connect(args.database) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            youth_count, inserted_count = save_recommendations(connection, source=args.source, top_n=args.top_n)
            connection.commit()

        print(f"Generated recommendations for youth: {youth_count}")
        print(f"Inserted recommendations: {inserted_count}")
        print(f"Saved recommendations to database source={args.source}")

    except (sqlite3.Error, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"Recommendation generation failed: {error}") from error


if __name__ == "__main__":
    main()