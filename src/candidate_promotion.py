from __future__ import annotations

import sqlite3

import pandas as pd


COUNTY_OPTIONS = ["New Castle", "Kent", "Sussex"]
EDUCATION_OPTIONS = [
    "Not enrolled",
    "Middle school",
    "High school",
    "GED/HiSET",
    "Associate degree",
    "Unknown",
]
EMPLOYMENT_OPTIONS = ["Unemployed", "Part-time", "Full-time", "Seasonal", "Training / internship", "Unknown"]
HOUSING_OPTIONS = [
    "Stable housing",
    "Couch surfing",
    "Temporary shelter",
    "Transitional housing",
    "At risk of homelessness",
    "Unknown",
]
MENTOR_STATUS_OPTIONS = ["Assigned", "Not assigned"]
PRIOR_HOMELESSNESS_OPTIONS = ["Yes", "No", "Unknown"]


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def generate_next_youth_id(connection: sqlite3.Connection) -> str:
    if not table_exists(connection, "youth_profiles"):
        return "YP-0001"

    rows = connection.execute("SELECT youth_id FROM youth_profiles WHERE youth_id LIKE 'YP-%'").fetchall()
    current_max = 0
    for row in rows:
        youth_id = str(row[0] or "")
        suffix = youth_id[3:]
        if suffix.isdigit():
            current_max = max(current_max, int(suffix))
    return f"YP-{current_max + 1:04d}"


def load_promotable_candidate_intakes(connection: sqlite3.Connection) -> pd.DataFrame:
    columns = [
        "candidate_name",
        "candidate_profile_id",
        "intake_session_id",
        "session_status",
        "started_at",
        "completed_at",
        "top_need_category",
        "assignment_count",
    ]
    if not table_exists(connection, "intake_sessions"):
        return pd.DataFrame(columns=columns)

    frame = pd.read_sql_query(
        """
        WITH latest_candidate AS (
            SELECT
                candidate_profile_id,
                intake_session_id,
                session_status,
                started_at,
                completed_at,
                top_need_category,
                ROW_NUMBER() OVER (
                    PARTITION BY candidate_profile_id
                    ORDER BY COALESCE(completed_at, started_at, '') DESC, intake_session_id DESC
                ) AS rn
            FROM intake_sessions
            WHERE profile_type = 'candidate'
              AND candidate_profile_id IS NOT NULL
              AND LOWER(COALESCE(session_status, '')) IN ('in_progress', 'completed')
        )
        SELECT
            candidate_profile_id,
            intake_session_id,
            session_status,
            started_at,
            completed_at,
            COALESCE(top_need_category, 'not_set') AS top_need_category
        FROM latest_candidate
        WHERE rn = 1
        ORDER BY COALESCE(completed_at, started_at, '') DESC, candidate_profile_id ASC
        """,
        connection,
    )

    if frame.empty:
        return pd.DataFrame(columns=columns)

    if table_exists(connection, "candidate_profiles"):
        names_df = pd.read_sql_query(
            """
            SELECT candidate_profile_id, COALESCE(first_name, '') AS first_name, COALESCE(last_name, '') AS last_name
            FROM candidate_profiles
            """,
            connection,
        )
        if not names_df.empty:
            names_df["candidate_name"] = (
                names_df["first_name"].astype(str).str.strip() + " " + names_df["last_name"].astype(str).str.strip()
            ).str.strip()
            names_df["candidate_name"] = names_df["candidate_name"].replace("", pd.NA)
            frame = frame.merge(names_df[["candidate_profile_id", "candidate_name"]], on="candidate_profile_id", how="left")

    if "candidate_name" not in frame.columns:
        frame["candidate_name"] = pd.NA
    frame["candidate_name"] = frame["candidate_name"].fillna(frame["candidate_profile_id"].astype(str))

    if table_exists(connection, "assigned_resources"):
        assignment_counts = pd.read_sql_query(
            """
            SELECT candidate_profile_id, COUNT(*) AS assignment_count
            FROM assigned_resources
            WHERE profile_type = 'candidate'
              AND candidate_profile_id IS NOT NULL
            GROUP BY candidate_profile_id
            """,
            connection,
        )
        if not assignment_counts.empty:
            frame = frame.merge(assignment_counts, on="candidate_profile_id", how="left")

    if "assignment_count" not in frame.columns:
        frame["assignment_count"] = 0
    frame["assignment_count"] = frame["assignment_count"].fillna(0).astype(int)
    return frame[columns]


def load_candidate_intake_answers(connection: sqlite3.Connection, intake_session_id: str) -> dict[str, str]:
    if not intake_session_id.strip() or not table_exists(connection, "intake_answers"):
        return {}

    rows = connection.execute(
        """
        SELECT question_key, answer_value
        FROM intake_answers
        WHERE intake_session_id = ?
        ORDER BY intake_answer_id ASC
        """,
        (intake_session_id.strip(),),
    ).fetchall()
    return {str(row[0]): str(row[1] or "") for row in rows}


def build_profile_defaults_from_answers(answers: dict[str, str]) -> dict[str, str | int]:
    housing_map = {
        "stable": "Stable housing",
        "temporary": "Temporary shelter",
        "couch_surfing": "Couch surfing",
        "shelter": "Temporary shelter",
        "at_risk": "At risk of homelessness",
    }
    employment_map = {
        "unemployed": "Unemployed",
        "part_time": "Part-time",
        "full_time": "Full-time",
        "seasonal": "Seasonal",
        "training": "Training / internship",
    }
    education_map = {
        "in_school": "High school",
        "diploma_or_ged": "GED/HiSET",
        "no_diploma_or_ged": "Not enrolled",
        "postsecondary": "Associate degree",
        "not_enrolled": "Not enrolled",
    }

    housing = housing_map.get(str(answers.get("housing_status") or "").strip().lower(), "Unknown")
    employment = employment_map.get(str(answers.get("employment_status") or "").strip().lower(), "Unknown")
    education = education_map.get(str(answers.get("education_status") or "").strip().lower(), "Unknown")

    prior_homelessness = "Unknown"
    if housing in {"Couch surfing", "Temporary shelter", "Transitional housing", "At risk of homelessness"}:
        prior_homelessness = "Yes"
    elif housing == "Stable housing":
        prior_homelessness = "No"

    return {
        "education": education,
        "employment": employment,
        "housing": housing,
        "mentor_status": "Not assigned",
        "placement_count": 0,
        "prior_homelessness": prior_homelessness,
    }


def promote_candidate_to_youth(
    connection: sqlite3.Connection,
    *,
    candidate_profile_id: str,
    youth_id: str,
    age: int,
    county: str,
    education: str,
    employment: str,
    housing: str,
    mentor_status: str,
    placement_count: int,
    prior_homelessness: str,
    first_name: str = "",
    last_name: str = "",
    caseworker_id: str | None = None,
    case_priority: str = "Medium",
) -> str:
    normalized_candidate_id = candidate_profile_id.strip()
    normalized_youth_id = youth_id.strip()
    if not normalized_candidate_id:
        raise ValueError("Candidate profile ID is required.")
    if not normalized_youth_id:
        raise ValueError("Youth ID is required.")
    if age < 0 or age > 120:
        raise ValueError("Age must be between 0 and 120.")
    if placement_count < 0:
        raise ValueError("Placement count cannot be negative.")

    if not table_exists(connection, "youth_profiles"):
        raise ValueError("youth_profiles table not found in database.")

    exists_row = connection.execute("SELECT 1 FROM youth_profiles WHERE youth_id = ?", (normalized_youth_id,)).fetchone()
    if exists_row is not None:
        raise ValueError(f"Youth ID already exists: {normalized_youth_id}")

    latest_session = connection.execute(
        """
        SELECT intake_session_id
        FROM intake_sessions
        WHERE candidate_profile_id = ?
          AND profile_type = 'candidate'
          AND LOWER(COALESCE(session_status, '')) = 'completed'
        ORDER BY COALESCE(completed_at, started_at, '') DESC, intake_session_id DESC
        LIMIT 1
        """,
        (normalized_candidate_id,),
    ).fetchone()
    if latest_session is None:
        raise ValueError(f"No completed candidate intake found for {normalized_candidate_id}.")

    connection.execute(
        """
        INSERT INTO youth_profiles (
            youth_id,
            age,
            county,
            education,
            employment,
            housing,
            mentor_status,
            placement_count,
            prior_homelessness
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized_youth_id,
            age,
            county.strip(),
            education.strip(),
            employment.strip(),
            housing.strip(),
            mentor_status.strip(),
            placement_count,
            prior_homelessness.strip(),
        ),
    )

    normalized_first_name = first_name.strip()
    normalized_last_name = last_name.strip()
    if table_exists(connection, "caseworker_youth") and normalized_first_name and normalized_last_name:
        connection.execute(
            """
            INSERT INTO caseworker_youth (youth_id, first_name, last_name)
            VALUES (?, ?, ?)
            ON CONFLICT(youth_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name
            """,
            (normalized_youth_id, normalized_first_name, normalized_last_name),
        )

    connection.execute(
        """
        UPDATE intake_sessions
        SET youth_id = ?,
            candidate_profile_id = NULL,
            profile_type = 'youth'
        WHERE candidate_profile_id = ?
          AND profile_type = 'candidate'
        """,
        (normalized_youth_id, normalized_candidate_id),
    )

    if table_exists(connection, "assigned_resources"):
        connection.execute(
            """
            UPDATE assigned_resources
            SET youth_id = ?,
                candidate_profile_id = NULL,
                profile_type = 'youth'
            WHERE candidate_profile_id = ?
              AND profile_type = 'candidate'
            """,
            (normalized_youth_id, normalized_candidate_id),
        )

    normalized_caseworker_id = (caseworker_id or "").strip()
    if normalized_caseworker_id and table_exists(connection, "case_assignments") and table_exists(connection, "caseworkers"):
        caseworker_exists = connection.execute(
            "SELECT 1 FROM caseworkers WHERE caseworker_id = ?",
            (normalized_caseworker_id,),
        ).fetchone()
        if caseworker_exists is not None:
            connection.execute(
                """
                INSERT INTO case_assignments (youth_id, caseworker_id, case_status, priority_level)
                VALUES (?, ?, 'assigned', ?)
                ON CONFLICT(youth_id) DO UPDATE SET
                    caseworker_id = excluded.caseworker_id,
                    case_status = 'assigned',
                    priority_level = excluded.priority_level,
                    last_updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_youth_id, normalized_caseworker_id, case_priority.strip() or "Medium"),
            )

    return str(latest_session[0])