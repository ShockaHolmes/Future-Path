from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import cast


MODEL_NAME = "rules_based_risk"
MODEL_VERSION = "1.0.0"


@dataclass(frozen=True)
class FactorResult:
    name: str
    score: int
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate youth risk scores and store results in risk_scores table."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("database/future_path.db"),
        help="Path to SQLite database.",
    )
    return parser.parse_args()


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_risk_scores_table(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "risk_scores"):
        connection.execute(
            """
            CREATE TABLE risk_scores (
                risk_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                youth_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_version TEXT,
                overall_risk_score REAL NOT NULL CHECK (overall_risk_score >= 0.0 AND overall_risk_score <= 1.0),
                housing_risk_score REAL CHECK (housing_risk_score >= 0.0 AND housing_risk_score <= 1.0),
                employment_risk_score REAL CHECK (employment_risk_score >= 0.0 AND employment_risk_score <= 1.0),
                education_risk_score REAL CHECK (education_risk_score >= 0.0 AND education_risk_score <= 1.0),
                risk_level TEXT NOT NULL CHECK (risk_level IN ('Low', 'Medium', 'High')),
                risk_factors_json TEXT,
                calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_risk_scores_youth_id ON risk_scores (youth_id)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_risk_scores_calculated_at ON risk_scores (calculated_at)"
        )
        return

    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'risk_scores'"
    ).fetchone()
    table_sql = table_sql_row[0] if table_sql_row else ""
    if "Moderate" in table_sql or "Critical" in table_sql:
        connection.execute("ALTER TABLE risk_scores RENAME TO risk_scores_legacy")
        connection.execute(
            """
            CREATE TABLE risk_scores (
                risk_score_id INTEGER PRIMARY KEY AUTOINCREMENT,
                youth_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                model_version TEXT,
                overall_risk_score REAL NOT NULL CHECK (overall_risk_score >= 0.0 AND overall_risk_score <= 1.0),
                housing_risk_score REAL CHECK (housing_risk_score >= 0.0 AND housing_risk_score <= 1.0),
                employment_risk_score REAL CHECK (employment_risk_score >= 0.0 AND employment_risk_score <= 1.0),
                education_risk_score REAL CHECK (education_risk_score >= 0.0 AND education_risk_score <= 1.0),
                risk_level TEXT NOT NULL CHECK (risk_level IN ('Low', 'Medium', 'High')),
                risk_factors_json TEXT,
                calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (youth_id) REFERENCES youth_profiles(youth_id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            INSERT INTO risk_scores (
                youth_id,
                model_name,
                model_version,
                overall_risk_score,
                housing_risk_score,
                employment_risk_score,
                education_risk_score,
                risk_level,
                risk_factors_json,
                calculated_at
            )
            SELECT
                youth_id,
                model_name,
                model_version,
                overall_risk_score,
                housing_risk_score,
                employment_risk_score,
                education_risk_score,
                CASE
                    WHEN risk_level IN ('High', 'Critical') THEN 'High'
                    WHEN risk_level IN ('Medium', 'Moderate') THEN 'Medium'
                    ELSE 'Low'
                END,
                risk_factors_json,
                calculated_at
            FROM risk_scores_legacy
            """
        )
        connection.execute("DROP TABLE risk_scores_legacy")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_risk_scores_youth_id ON risk_scores (youth_id)")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_risk_scores_calculated_at ON risk_scores (calculated_at)"
        )


def load_youth_profiles(connection: sqlite3.Connection) -> list[dict[str, object]]:
    if not _table_exists(connection, "youth_profiles"):
        raise ValueError("Missing youth_profiles table. Run youth profile ETL before scoring.")

    cursor = connection.execute(
        """
        SELECT youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
        FROM youth_profiles
        ORDER BY youth_id
        """
    )
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _truthy(value: object) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"true", "1", "yes", "y", "urgent", "sometimes", "needed"}


def load_latest_intake_answers(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not _table_exists(connection, "intake_sessions") or not _table_exists(connection, "intake_answers"):
        return {}

    rows = connection.execute(
        """
        SELECT s.youth_id, a.question_key, a.answer_value
        FROM intake_sessions s
        JOIN intake_answers a ON a.intake_session_id = s.intake_session_id
        JOIN (
            SELECT youth_id, MAX(started_at) AS latest_started_at
            FROM intake_sessions
            GROUP BY youth_id
        ) latest ON latest.youth_id = s.youth_id AND latest.latest_started_at = s.started_at
        """
    ).fetchall()

    by_youth: dict[str, dict[str, str]] = {}
    for youth_id, question_key, answer_value in rows:
        by_youth.setdefault(str(youth_id), {})[str(question_key)] = "" if answer_value is None else str(answer_value)
    return by_youth


def compute_risk_factors(youth: dict[str, object], intake_answers: dict[str, str]) -> list[FactorResult]:
    factors: list[FactorResult] = []

    housing = str(youth["housing"])
    employment = str(youth["employment"])
    education = str(youth["education"])
    mentor_status = str(youth["mentor_status"])
    placement_count = int(cast(int | float | str, youth["placement_count"]))
    prior_homelessness = str(youth["prior_homelessness"])

    if housing in {"Couch surfing", "Temporary shelter", "Transitional housing", "At risk of homelessness"}:
        factors.append(FactorResult("unstable_housing", 18, f"housing={housing}"))

    if housing in {"Temporary shelter", "Couch surfing", "At risk of homelessness"}:
        factors.append(FactorResult("homelessness_risk", 14, f"housing={housing}"))

    if employment == "Unemployed":
        factors.append(FactorResult("unemployment", 14, "employment=Unemployed"))

    if education in {"Not enrolled", "Middle school", "High school"}:
        factors.append(FactorResult("no_diploma_or_ged", 10, f"education={education}"))

    if mentor_status == "Not assigned":
        factors.append(FactorResult("no_mentor", 10, "mentor_status=Not assigned"))

    if placement_count >= 6:
        factors.append(FactorResult("high_placement_count", 12, f"placement_count={placement_count}"))
    elif placement_count >= 4:
        factors.append(FactorResult("high_placement_count", 8, f"placement_count={placement_count}"))

    if prior_homelessness == "Yes":
        factors.append(FactorResult("prior_homelessness", 12, "prior_homelessness=Yes"))

    food_shortage = _truthy(intake_answers.get("food_shortage")) or _truthy(intake_answers.get("food_insecurity"))
    if not food_shortage:
        food_security = intake_answers.get("food_security", "").strip().lower()
        food_shortage = food_security in {"no", "sometimes", "urgent"}
    if food_shortage:
        factors.append(FactorResult("food_shortage", 10, "intake indicates food insecurity"))

    mental_health_need = _truthy(intake_answers.get("mental_health")) or _truthy(
        intake_answers.get("mental_health_need")
    ) or _truthy(intake_answers.get("crisis_flag"))
    if mental_health_need:
        factors.append(FactorResult("mental_health", 12, "intake indicates mental health need"))

    health_need = _truthy(intake_answers.get("health")) or _truthy(intake_answers.get("health_need")) or _truthy(
        intake_answers.get("healthcare_needed")
    ) or _truthy(intake_answers.get("health_insurance_needed"))
    if health_need:
        factors.append(FactorResult("health", 8, "intake indicates health support need"))

    return factors


def assign_risk_level(score_0_to_100: int) -> str:
    if score_0_to_100 >= 60:
        return "High"
    if score_0_to_100 >= 30:
        return "Medium"
    return "Low"


def insert_risk_score(
    connection: sqlite3.Connection,
    youth_id: str,
    total_score: int,
    risk_level: str,
    top_factors_payload: dict[str, object],
) -> None:
    category_scores_raw = top_factors_payload.get("category_scores")
    category_scores: dict[str, int | float] = {}
    if isinstance(category_scores_raw, dict):
        category_scores = {
            str(key): float(value)
            for key, value in category_scores_raw.items()
            if isinstance(value, (int, float))
        }

    housing_component = min(float(category_scores.get("housing", 0.0)) / 40.0, 1.0)
    employment_component = min(float(category_scores.get("employment", 0.0)) / 25.0, 1.0)
    education_component = min(float(category_scores.get("education", 0.0)) / 20.0, 1.0)

    connection.execute(
        """
        INSERT INTO risk_scores (
            youth_id,
            model_name,
            model_version,
            overall_risk_score,
            housing_risk_score,
            employment_risk_score,
            education_risk_score,
            risk_level,
            risk_factors_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            youth_id,
            MODEL_NAME,
            MODEL_VERSION,
            total_score / 100.0,
            housing_component,
            employment_component,
            education_component,
            risk_level,
            json.dumps(top_factors_payload, ensure_ascii=True),
        ),
    )


def main() -> None:
    args = parse_args()

    try:
        if not args.database.exists():
            raise FileNotFoundError(f"Database not found: {args.database}. Run ETL load first.")

        with sqlite3.connect(args.database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            ensure_risk_scores_table(connection)

            youth_profiles = load_youth_profiles(connection)
            intake_by_youth = load_latest_intake_answers(connection)
            if not youth_profiles:
                raise ValueError("No youth profiles found to score.")

            low_count = 0
            medium_count = 0
            high_count = 0

            for youth in youth_profiles:
                youth_id = str(youth["youth_id"])
                factors = compute_risk_factors(youth, intake_by_youth.get(youth_id, {}))
                total_score = min(sum(factor.score for factor in factors), 100)
                risk_level = assign_risk_level(total_score)

                if risk_level == "Low":
                    low_count += 1
                elif risk_level == "Medium":
                    medium_count += 1
                else:
                    high_count += 1

                category_scores = {
                    "housing": sum(
                        factor.score
                        for factor in factors
                        if factor.name in {"unstable_housing", "homelessness_risk", "prior_homelessness"}
                    ),
                    "employment": sum(
                        factor.score for factor in factors if factor.name in {"unemployment", "high_placement_count"}
                    ),
                    "education": sum(factor.score for factor in factors if factor.name == "no_diploma_or_ged"),
                }

                sorted_factors = sorted(factors, key=lambda factor: factor.score, reverse=True)
                top_factors = [
                    {"name": factor.name, "score": factor.score, "reason": factor.reason}
                    for factor in sorted_factors[:3]
                ]
                all_factors = [
                    {"name": factor.name, "score": factor.score, "reason": factor.reason}
                    for factor in sorted_factors
                ]
                payload = {
                    "total_score_0_to_100": total_score,
                    "risk_level": risk_level,
                    "top_risk_factors": top_factors,
                    "all_triggered_factors": all_factors,
                    "category_scores": category_scores,
                }

                insert_risk_score(connection, youth_id, total_score, risk_level, payload)

            connection.commit()

            print(f"Scored youth profiles: {len(youth_profiles)}")
            print(f"Risk distribution -> Low: {low_count}, Medium: {medium_count}, High: {high_count}")
            print("Saved results to risk_scores with top risk factors in risk_factors_json")

    except (sqlite3.Error, ValueError, FileNotFoundError) as error:
        raise SystemExit(f"Risk scoring failed: {error}") from error


if __name__ == "__main__":
    main()