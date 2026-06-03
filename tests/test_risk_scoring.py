from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from calculate_risk_scores import (
    MODEL_NAME,
    MODEL_VERSION,
    assign_risk_level,
    compute_risk_factors,
    ensure_risk_scores_table,
    insert_risk_score,
)


def test_assign_risk_level_thresholds() -> None:
    assert assign_risk_level(0) == "Low"
    assert assign_risk_level(29) == "Low"
    assert assign_risk_level(30) == "Medium"
    assert assign_risk_level(59) == "Medium"
    assert assign_risk_level(60) == "High"
    assert assign_risk_level(100) == "High"


def test_compute_risk_factors_includes_expected_flags() -> None:
    youth = {
        "youth_id": "YP-0001",
        "age": 18,
        "county": "Kent",
        "education": "Not enrolled",
        "employment": "Unemployed",
        "housing": "Couch surfing",
        "mentor_status": "Not assigned",
        "placement_count": 6,
        "prior_homelessness": "Yes",
    }
    intake_answers = {
        "food_shortage": "yes",
        "mental_health_need": "true",
        "health_insurance_needed": "yes",
    }

    factors = compute_risk_factors(youth, intake_answers)
    names = {factor.name for factor in factors}

    assert {
        "unstable_housing",
        "homelessness_risk",
        "unemployment",
        "no_diploma_or_ged",
        "no_mentor",
        "high_placement_count",
        "prior_homelessness",
        "food_shortage",
        "mental_health",
        "health",
    }.issubset(names)


def test_insert_risk_score_persists_top_factors_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "risk_scoring_test.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE youth_profiles (
                youth_id TEXT PRIMARY KEY,
                age INTEGER NOT NULL,
                county TEXT NOT NULL,
                education TEXT NOT NULL,
                employment TEXT NOT NULL,
                housing TEXT NOT NULL,
                mentor_status TEXT NOT NULL,
                placement_count INTEGER NOT NULL,
                prior_homelessness TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO youth_profiles (
                youth_id, age, county, education, employment, housing, mentor_status, placement_count, prior_homelessness
            ) VALUES ('YP-0001', 18, 'Kent', 'Not enrolled', 'Unemployed', 'Couch surfing', 'Not assigned', 6, 'Yes')
            """
        )

        ensure_risk_scores_table(connection)
        payload = {
            "total_score_0_to_100": 80,
            "risk_level": "High",
            "top_risk_factors": [
                {"name": "unstable_housing", "score": 18, "reason": "housing=Couch surfing"},
                {"name": "unemployment", "score": 14, "reason": "employment=Unemployed"},
            ],
            "all_triggered_factors": [],
            "category_scores": {"housing": 30, "employment": 14, "education": 10},
        }
        insert_risk_score(connection, "YP-0001", 80, "High", payload)
        connection.commit()

        row = connection.execute(
            """
            SELECT model_name, model_version, overall_risk_score, risk_level, risk_factors_json
            FROM risk_scores
            WHERE youth_id = 'YP-0001'
            """
        ).fetchone()

    assert row is not None
    assert row[0] == MODEL_NAME
    assert row[1] == MODEL_VERSION
    assert row[2] == 0.8
    assert row[3] == "High"
    parsed = json.loads(row[4])
    assert parsed["top_risk_factors"][0]["name"] == "unstable_housing"