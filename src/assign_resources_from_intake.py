from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TypedDict


DEFAULT_DATABASE = Path("database/future_path.db")
DEFAULT_TOP_N = 5

NEED_TAG_MAP = {
    "unstable_housing": {"housing", "homelessness", "transitional_housing", "housing_navigation"},
    "unemployment": {"employment", "job_training", "workforce_development", "paid_work", "career_pathway"},
    "education_need": {"education", "ged", "academic_support", "credentials", "career_readiness"},
    "transportation_need": {"transportation", "transit", "bus_pass", "mobility"},
    "food_support": {"food", "food_access", "nutrition", "meal_support"},
    "wellness_need": {"health", "mental_health", "counseling", "teen_health", "crisis", "substance_use"},
    "documents_need": {"documents", "id_support", "benefits_navigation", "legal_aid"},
    "support_need": {"mentorship", "community_support", "positive_adult", "peer_support"},
    "safety_need": {"safety", "crisis", "legal_aid", "violence_prevention", "emergency_support"},
    "general_support": {"general_support"},
}

RESOURCE_PRIORITY_SCORE = {"High": 25, "Medium": 15, "Low": 8}


class NeedPayload(TypedDict):
    risk_points: int
    reasons: list[str]


class ResourceCandidate(TypedDict):
    resource_id: str
    resource_name: str
    match_score: float
    match_reasons: list[str]
    contact_phone: str
    contact_email: str
    website: str


class ResourceMatch(TypedDict):
    resource_id: str
    resource_name: str
    match_score: float
    priority_level: str
    match_reason: str
    contact_phone: str
    contact_email: str
    website: str


class AssignmentResult(TypedDict):
    session_id: str
    profile_type: str
    youth_id: str | None
    candidate_profile_id: str | None
    total_risk_points: int
    needs: dict[str, NeedPayload]
    assignments: list[ResourceMatch]
    assigned_rows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assign resources from completed AI intake answers and save to assigned_resources."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="Path to SQLite database.")
    parser.add_argument(
        "--session-id",
        help="Optional intake_session_id. If omitted, uses the latest completed intake session.",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Maximum resources to assign.")
    parser.add_argument(
        "--assigned-by",
        default="ai_intake_matcher_v1",
        help="Value stored in assigned_resources.assigned_by.",
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
    return {tag.strip().lower() for tag in str(raw_value).split(";") if tag.strip()}


def ensure_resources_table_populated(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "resources"):
        raise ValueError("Missing resources table. Apply relational schema before assigning resources.")

    resource_columns = _table_columns(connection, "resources")
    if "contact_email" not in resource_columns:
        connection.execute("ALTER TABLE resources ADD COLUMN contact_email TEXT")

    resources_count = connection.execute("SELECT COUNT(*) FROM resources").fetchone()[0]
    if resources_count > 0 and not _table_exists(connection, "youth_resources"):
        return

    if not _table_exists(connection, "youth_resources"):
        raise ValueError("No resources data found. Load resources before assigning from intake.")

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


def _create_assigned_resources_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS assigned_resources (
            assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            youth_id TEXT,
            candidate_profile_id TEXT,
            profile_type TEXT NOT NULL CHECK (profile_type IN ('youth', 'candidate')),
            resource_id TEXT NOT NULL,
            intake_session_id TEXT,
            recommendation_id INTEGER,
            assigned_by TEXT,
            priority_level TEXT NOT NULL CHECK (priority_level IN ('High', 'Medium', 'Low')),
            match_score REAL,
            match_reason TEXT,
            assignment_status TEXT NOT NULL DEFAULT 'assigned' CHECK (
                assignment_status IN ('assigned', 'in_progress', 'completed', 'declined', 'closed')
            ),
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            follow_up_date TEXT,
            notes TEXT,
            FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE,
            FOREIGN KEY (resource_id) REFERENCES resources(resource_id) ON DELETE RESTRICT,
            FOREIGN KEY (recommendation_id) REFERENCES recommendations(recommendation_id) ON DELETE SET NULL,
            FOREIGN KEY (intake_session_id) REFERENCES intake_sessions(intake_session_id) ON DELETE SET NULL,
            CHECK (
                (profile_type = 'youth' AND youth_id IS NOT NULL AND candidate_profile_id IS NULL)
                OR
                (profile_type = 'candidate' AND candidate_profile_id IS NOT NULL AND youth_id IS NULL)
            ),
            UNIQUE (intake_session_id, resource_id)
        )
        """
    )


def ensure_assigned_resources_table_integrity(connection: sqlite3.Connection) -> None:
    required_columns = {
        "youth_id",
        "candidate_profile_id",
        "profile_type",
        "resource_id",
        "intake_session_id",
        "priority_level",
        "match_score",
        "match_reason",
    }
    existing_columns = _table_columns(connection, "assigned_resources")

    if not existing_columns:
        _create_assigned_resources_table(connection)
    elif required_columns - existing_columns:
        connection.execute("ALTER TABLE assigned_resources RENAME TO assigned_resources_legacy")
        _create_assigned_resources_table(connection)
        connection.execute(
            """
            INSERT INTO assigned_resources (
                assignment_id,
                youth_id,
                candidate_profile_id,
                profile_type,
                resource_id,
                intake_session_id,
                recommendation_id,
                assigned_by,
                priority_level,
                match_score,
                match_reason,
                assignment_status,
                assigned_at,
                follow_up_date,
                notes
            )
            SELECT
                assignment_id,
                youth_id,
                NULL,
                'youth',
                resource_id,
                NULL,
                recommendation_id,
                assigned_by,
                'Medium',
                NULL,
                COALESCE(notes, 'Migrated assignment'),
                assignment_status,
                assigned_at,
                follow_up_date,
                notes
            FROM assigned_resources_legacy
            """
        )
        connection.execute("DROP TABLE assigned_resources_legacy")

    connection.execute("CREATE INDEX IF NOT EXISTS idx_assigned_resources_youth_id ON assigned_resources (youth_id)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_assigned_resources_candidate_profile_id ON assigned_resources (candidate_profile_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_assigned_resources_intake_session_id ON assigned_resources (intake_session_id)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_assigned_resources_resource_id ON assigned_resources (resource_id)")


def fetch_target_session(connection: sqlite3.Connection, session_id: str | None) -> sqlite3.Row:
    if not _table_exists(connection, "intake_sessions"):
        raise ValueError("Missing intake_sessions table. Run intake first.")

    connection.row_factory = sqlite3.Row
    if session_id:
        row = connection.execute(
            """
            SELECT intake_session_id, youth_id, candidate_profile_id, profile_type, top_need_category
            FROM intake_sessions
            WHERE intake_session_id = ?
            """,
            (session_id,),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT intake_session_id, youth_id, candidate_profile_id, profile_type, top_need_category
            FROM intake_sessions
            WHERE session_status = 'completed'
            ORDER BY COALESCE(completed_at, started_at) DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        if session_id:
            raise ValueError(f"No intake session found for id: {session_id}")
        raise ValueError("No completed intake session found.")

    return row


def fetch_answers_by_key(connection: sqlite3.Connection, intake_session_id: str) -> dict[str, str]:
    if not _table_exists(connection, "intake_answers"):
        raise ValueError("Missing intake_answers table. Run intake first.")

    rows = connection.execute(
        """
        SELECT question_key, answer_value
        FROM intake_answers
        WHERE intake_session_id = ?
        """,
        (intake_session_id,),
    ).fetchall()
    if not rows:
        raise ValueError(f"No answers found for intake session: {intake_session_id}")

    return {str(row[0]): "" if row[1] is None else str(row[1]).strip().lower() for row in rows}


def _priority_from_score(score: float) -> str:
    if score >= 85:
        return "High"
    if score >= 50:
        return "Medium"
    return "Low"


def map_answers_to_needs(answers: dict[str, str], top_need_category: str | None) -> tuple[dict[str, NeedPayload], int]:
    needs: dict[str, NeedPayload] = {}

    def add_need(need_key: str, points: int, reason: str) -> None:
        if need_key not in needs:
            needs[need_key] = {"risk_points": 0, "reasons": []}
        needs[need_key]["risk_points"] = needs[need_key]["risk_points"] + points
        needs[need_key]["reasons"].append(reason)

    housing = answers.get("housing_status", "")
    if housing in {"temporary", "couch_surfing", "shelter", "at_risk"}:
        add_need("unstable_housing", 45, f"housing_status={housing}")

    employment = answers.get("employment_status", "")
    if employment == "unemployed":
        add_need("unemployment", 35, "employment_status=unemployed")
    elif employment in {"training", "seasonal"}:
        add_need("unemployment", 15, f"employment_status={employment}")

    education = answers.get("education_status", "")
    if education in {"no_diploma_or_ged", "not_enrolled"}:
        add_need("education_need", 28, f"education_status={education}")

    transport = answers.get("transportation_access", "")
    if transport == "none":
        add_need("transportation_need", 25, "transportation_access=none")
    elif transport == "limited":
        add_need("transportation_need", 15, "transportation_access=limited")

    food = answers.get("food_access", "")
    if food == "no":
        add_need("food_support", 25, "food_access=no")
    elif food == "sometimes":
        add_need("food_support", 15, "food_access=sometimes")

    if answers.get("health_wellness_need", "") == "yes":
        add_need("wellness_need", 24, "health_wellness_need=yes")

    documents = answers.get("documents_status", "")
    if documents == "none":
        add_need("documents_need", 20, "documents_status=none")
    elif documents == "some":
        add_need("documents_need", 10, "documents_status=some")

    support = answers.get("support_system", "")
    if support == "none":
        add_need("support_need", 20, "support_system=none")
    elif support == "limited":
        add_need("support_need", 12, "support_system=limited")

    if answers.get("safety_concern", "") == "yes":
        add_need("safety_need", 40, "safety_concern=yes")

    primary = answers.get("primary_need", "")
    primary_map = {
        "housing": "unstable_housing",
        "employment": "unemployment",
        "education": "education_need",
        "transportation": "transportation_need",
        "food": "food_support",
        "health_wellness": "wellness_need",
        "documents": "documents_need",
        "support_system": "support_need",
        "safety": "safety_need",
    }
    primary_need_key = primary_map.get(primary) or primary_map.get((top_need_category or "").strip().lower())
    if primary_need_key:
        add_need(primary_need_key, 20, f"primary_need={primary or top_need_category}")

    if not needs:
        add_need("general_support", 10, "No high-risk responses detected")

    total_points = sum(needs[need_key]["risk_points"] for need_key in needs)
    return needs, total_points


def _load_profile_context(connection: sqlite3.Connection, youth_id: str | None) -> tuple[int | None, str | None]:
    if not youth_id:
        return None, None
    if not _table_exists(connection, "youth_profiles"):
        return None, None

    row = connection.execute(
        "SELECT age, county FROM youth_profiles WHERE youth_id = ?",
        (youth_id,),
    ).fetchone()
    if row is None:
        return None, None
    return int(row[0]), str(row[1])


def _match_resources(
    resources: list[sqlite3.Row],
    needs: dict[str, NeedPayload],
    age: int | None,
    county: str | None,
    top_n: int,
) -> list[ResourceMatch]:
    by_resource: dict[str, ResourceCandidate] = {}

    for need_key, payload in needs.items():
        target_tags = NEED_TAG_MAP.get(need_key, {"general_support"})
        need_points = payload["risk_points"]
        need_reasons = payload["reasons"]

        for resource in resources:
            min_age = int(resource["eligibility_age_min"])
            max_age = int(resource["eligibility_age_max"])
            if age is not None and (age < min_age or age > max_age):
                continue

            resource_county = str(resource["county"])
            service_area = str(resource["service_area"])
            statewide_match = resource_county == "Statewide" or service_area == "Statewide"
            county_match = county is not None and resource_county == county
            if county is not None and not county_match and not statewide_match:
                continue

            tags = _split_tags(resource["need_tags"])
            overlap = sorted(target_tags & tags)
            if not overlap:
                continue

            default_priority = str(resource["default_priority"])
            score = float(need_points + RESOURCE_PRIORITY_SCORE.get(default_priority, 0))
            score += min(len(overlap) * 2, 8)
            if county_match:
                score += 5
            elif statewide_match:
                score += 3
            else:
                score += 1

            resource_id = str(resource["resource_id"])
            match_reason = (
                f"Matched {need_key} using intake signals: {', '.join(need_reasons)}; "
                f"resource tags matched: {', '.join(overlap)}"
            )

            if resource_id not in by_resource:
                by_resource[resource_id] = {
                    "resource_id": resource_id,
                    "resource_name": str(resource["resource_name"]),
                    "match_score": score,
                    "match_reasons": [match_reason],
                    "contact_phone": str(resource["contact_phone"] or "").strip(),
                    "contact_email": str(resource["contact_email"] or "").strip(),
                    "website": str(resource["website"] or "").strip(),
                }
            else:
                by_resource[resource_id]["match_score"] = by_resource[resource_id]["match_score"] + score
                by_resource[resource_id]["match_reasons"].append(match_reason)

    ranked = list(by_resource.values())
    ranked.sort(key=lambda row: (-row["match_score"], row["resource_name"]))

    output: list[ResourceMatch] = []
    for row in ranked[:top_n]:
        score = row["match_score"]
        output.append(
            {
                "resource_id": row["resource_id"],
                "resource_name": row["resource_name"],
                "match_score": score,
                "priority_level": _priority_from_score(score),
                "match_reason": " | ".join(row["match_reasons"]),
                "contact_phone": row["contact_phone"],
                "contact_email": row["contact_email"],
                "website": row["website"],
            }
        )

    return output


def _priority_rank(priority_level: str) -> int:
    normalized = priority_level.strip().lower()
    if normalized == "high":
        return 1
    if normalized == "medium":
        return 2
    return 3


def assign_resources_from_intake(
    connection: sqlite3.Connection,
    intake_session_id: str | None,
    top_n: int,
    assigned_by: str,
) -> AssignmentResult:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    ensure_resources_table_populated(connection)
    ensure_assigned_resources_table_integrity(connection)

    session = fetch_target_session(connection, intake_session_id)
    session_id = str(session["intake_session_id"])
    profile_type = str(session["profile_type"])
    youth_id = None if session["youth_id"] is None else str(session["youth_id"])
    candidate_profile_id = None if session["candidate_profile_id"] is None else str(session["candidate_profile_id"])

    answers = fetch_answers_by_key(connection, session_id)
    needs, total_risk_points = map_answers_to_needs(
        answers,
        None if session["top_need_category"] is None else str(session["top_need_category"]),
    )

    age, county = _load_profile_context(connection, youth_id)

    resources = connection.execute(
        f"""
        SELECT
            resource_id,
            resource_name,
            need_tags,
            county,
            service_area,
            eligibility_age_min,
            eligibility_age_max,
            default_priority,
            {"COALESCE(contact_phone, '')" if "contact_phone" in _table_columns(connection, "resources") else "''"} AS contact_phone,
            {"COALESCE(contact_email, '')" if "contact_email" in _table_columns(connection, "resources") else "''"} AS contact_email,
            {"COALESCE(website, '')" if "website" in _table_columns(connection, "resources") else "''"} AS website
        FROM resources
        """
    ).fetchall()
    matched = _match_resources(resources, needs, age, county, top_n=top_n)

    if _table_exists(connection, "recommendations") and profile_type == "youth" and youth_id:
        for row in matched:
            connection.execute(
                """
                INSERT INTO recommendations (
                    youth_id,
                    resource_id,
                    risk_score_id,
                    intake_session_id,
                    match_score,
                    priority_rank,
                    recommendation_reason,
                    recommendation_source,
                    recommendation_status
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, 'ai_intake_mapper_v1', 'proposed')
                ON CONFLICT(youth_id, resource_id, intake_session_id) DO UPDATE SET
                    match_score = excluded.match_score,
                    priority_rank = excluded.priority_rank,
                    recommendation_reason = excluded.recommendation_reason,
                    recommendation_source = excluded.recommendation_source,
                    recommendation_status = 'proposed'
                """,
                (
                    youth_id,
                    row["resource_id"],
                    session_id,
                    row["match_score"],
                    _priority_rank(row["priority_level"]),
                    row["match_reason"],
                ),
            )

    connection.execute("DELETE FROM assigned_resources WHERE intake_session_id = ?", (session_id,))

    assigned_rows = 0
    for row in matched:
        connection.execute(
            """
            INSERT INTO assigned_resources (
                youth_id,
                candidate_profile_id,
                profile_type,
                resource_id,
                intake_session_id,
                recommendation_id,
                assigned_by,
                priority_level,
                match_score,
                match_reason,
                assignment_status,
                notes
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'assigned', ?)
            """,
            (
                youth_id,
                candidate_profile_id,
                profile_type,
                row["resource_id"],
                session_id,
                assigned_by,
                row["priority_level"],
                row["match_score"],
                row["match_reason"],
                "Assigned automatically from AI intake responses",
            ),
        )
        assigned_rows += 1

    return {
        "session_id": session_id,
        "profile_type": profile_type,
        "youth_id": youth_id,
        "candidate_profile_id": candidate_profile_id,
        "total_risk_points": total_risk_points,
        "needs": needs,
        "assignments": matched,
        "assigned_rows": assigned_rows,
    }


def _format_summary(result: AssignmentResult) -> str:
    profile_type = str(result["profile_type"])
    profile_label = (
        f"youth_id={result['youth_id']}" if profile_type == "youth" else f"candidate_profile_id={result['candidate_profile_id']}"
    )

    lines = [
        "Intake-to-resource assignment completed.",
        f"Session: {result['session_id']}",
        f"Profile: {profile_type} ({profile_label})",
        f"Total risk points: {result['total_risk_points']}",
        "Needs identified:",
    ]

    needs = result["needs"]
    for need_key, payload in sorted(needs.items(), key=lambda item: int(item[1]["risk_points"]), reverse=True):
        lines.append(f"- {need_key}: {payload['risk_points']} points")

    lines.append("Assigned resources:")
    assignments = result["assignments"]
    if not assignments:
        lines.append("- No matching resources found for the intake profile.")
    else:
        for item in assignments:
            lines.append(
                f"- {item['resource_name']} ({item['resource_id']}) | "
                f"Priority: {item['priority_level']} | Score: {item['match_score']:.1f}"
            )
            lines.append(f"  Reason: {item['match_reason']}")

    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    try:
        if not args.database.exists():
            raise FileNotFoundError(f"Database not found: {args.database}")

        with sqlite3.connect(args.database) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            result = assign_resources_from_intake(
                connection,
                intake_session_id=args.session_id,
                top_n=args.top_n,
                assigned_by=args.assigned_by,
            )
            connection.commit()

        print(_format_summary(result))
        print(f"Saved assignments: {result['assigned_rows']}")

    except (sqlite3.Error, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"Resource assignment failed: {error}") from error


if __name__ == "__main__":
    main()
