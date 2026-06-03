from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from clean_youth_resource_catalog import clean_data as clean_resource_data
from load_youth_data_to_database import RESOURCE_TABLE, prepare_frame_for_sql, write_tables
from match_youth_to_resources import build_matches, derive_youth_need_tags


def test_clean_resource_catalog_standardizes_defaults_and_filters_invalid_rows() -> None:
    raw_frame = pd.DataFrame(
        [
            {
                "Resource ID": "R001",
                "Resource Name": "Delaware 211",
                "Category": "Resource Navigation",
                "Need Tags": "housing;food",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": None,
                "Eligibility Age Min": "",
                "Eligibility Age Max": None,
                "Description": "Navigation resource",
                "Referral Method": "Call 211",
                "Contact Phone": "",
                "Website": "https://delaware211.org/",
                "AI Match Rules": "any_unmet_need",
                "Default Priority": "",
                "Caseworker Notes": None,
            },
            {
                "Resource ID": "R001",
                "Resource Name": "Duplicate Delaware 211",
                "Category": "Resource Navigation",
                "Need Tags": "housing",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": "DE",
                "Eligibility Age Min": 0,
                "Eligibility Age Max": 99,
                "Description": "Duplicate",
                "Referral Method": "Call 211",
                "Contact Phone": "211",
                "Website": "https://delaware211.org/",
                "AI Match Rules": "any_unmet_need",
                "Default Priority": "High",
                "Caseworker Notes": "",
            },
            {
                "Resource ID": "",
                "Resource Name": "Invalid Resource",
                "Category": "Other",
                "Need Tags": "housing",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": "DE",
                "Eligibility Age Min": 50,
                "Eligibility Age Max": 10,
                "Description": "Bad age range",
                "Referral Method": "Call",
                "Contact Phone": "",
                "Website": "",
                "AI Match Rules": "",
                "Default Priority": "Low",
                "Caseworker Notes": "",
            },
        ]
    )

    cleaned = clean_resource_data(raw_frame)

    assert cleaned["resource_id"].tolist() == ["R001"]
    assert cleaned.loc[0, "state"] == "DE"
    assert cleaned.loc[0, "default_priority"] == "Medium"
    assert cleaned.loc[0, "contact_phone"] == "Not listed"
    assert cleaned.loc[0, "eligibility_age_min"] == 0
    assert cleaned.loc[0, "eligibility_age_max"] == 99


def test_prepare_frame_for_sql_replaces_string_nulls() -> None:
    frame = pd.DataFrame([{"resource_id": "R001", "caseworker_notes": None, "contact_phone": None}])

    prepared = prepare_frame_for_sql(frame)

    assert prepared.loc[0, "caseworker_notes"] == ""
    assert prepared.loc[0, "contact_phone"] == ""


def test_resource_table_loads_into_sqlite(tmp_path: Path) -> None:
    public_frame = pd.DataFrame(
        [
            {
                "youth_id": "YP-0001",
                "age": 16,
                "county": "Kent",
                "education": "High school",
                "employment": "Part-time",
                "housing": "Stable housing",
                "mentor_status": "Assigned",
                "placement_count": 2,
                "prior_homelessness": "No",
            }
        ]
    )
    caseworker_frame = pd.DataFrame(
        [{"youth_id": "YP-0001", "first_name": "Mia", "last_name": "Lopez"}]
    )
    resource_frame = pd.DataFrame(
        [
            {
                "resource_id": "R001",
                "resource_name": "Delaware 211",
                "category": "Resource Navigation",
                "need_tags": "general_support;housing",
                "service_area": "Statewide",
                "county": "Statewide",
                "city": "Statewide",
                "state": "DE",
                "eligibility_age_min": 0,
                "eligibility_age_max": 99,
                "description": "Statewide resource navigation.",
                "referral_method": "Call 211",
                "contact_phone": "211",
                "website": "https://delaware211.org/",
                "ai_match_rules": "any_unmet_need",
                "default_priority": "High",
                "caseworker_notes": None,
            }
        ]
    )
    database_path = tmp_path / "resource_test.db"

    write_tables(public_frame, caseworker_frame, resource_frame, database_path)

    with sqlite3.connect(database_path) as connection:
        resource_count = connection.execute(f"SELECT COUNT(*) FROM {RESOURCE_TABLE}").fetchone()[0]
        notes_value = connection.execute(
            f"SELECT caseworker_notes FROM {RESOURCE_TABLE} WHERE resource_id = 'R001'"
        ).fetchone()[0]

    assert resource_count == 1
    assert notes_value == ""


def test_derive_youth_need_tags_reflects_observed_risk_factors() -> None:
    youth_row = pd.Series(
        {
            "age": 18,
            "housing": "Couch surfing",
            "prior_homelessness": "Yes",
            "employment": "Unemployed",
            "education": "Not enrolled",
            "mentor_status": "Not assigned",
            "placement_count": 6,
        }
    )

    tags = derive_youth_need_tags(youth_row)

    assert {"housing", "homelessness", "employment", "mentorship", "life_skills"}.issubset(tags)


def test_build_matches_joins_youth_to_age_and_county_eligible_resources() -> None:
    youth_frame = pd.DataFrame(
        [
            {
                "youth_id": "YP-0001",
                "age": 18,
                "county": "New Castle",
                "education": "Not enrolled",
                "employment": "Unemployed",
                "housing": "Couch surfing",
                "mentor_status": "Not assigned",
                "placement_count": 6,
                "prior_homelessness": "Yes",
            }
        ]
    )
    resource_frame = pd.DataFrame(
        [
            {
                "resource_id": "R001",
                "resource_name": "Housing Support",
                "category": "Housing",
                "need_tags": "housing;case_management;life_skills",
                "service_area": "Statewide",
                "county": "Statewide",
                "city": "Statewide",
                "state": "DE",
                "eligibility_age_min": 16,
                "eligibility_age_max": 24,
                "description": "Housing resource",
                "referral_method": "Call",
                "contact_phone": "211",
                "website": "https://example.org/housing",
                "ai_match_rules": "needs_housing",
                "default_priority": "High",
                "caseworker_notes": "",
            },
            {
                "resource_id": "R002",
                "resource_name": "Sussex Resource",
                "category": "Housing",
                "need_tags": "housing",
                "service_area": "Sussex",
                "county": "Sussex",
                "city": "Georgetown",
                "state": "DE",
                "eligibility_age_min": 16,
                "eligibility_age_max": 24,
                "description": "County-only resource",
                "referral_method": "Call",
                "contact_phone": "211",
                "website": "https://example.org/sussex",
                "ai_match_rules": "needs_housing",
                "default_priority": "High",
                "caseworker_notes": "",
            },
            {
                "resource_id": "R003",
                "resource_name": "Young Teen Only",
                "category": "Mentorship",
                "need_tags": "mentorship;community",
                "service_area": "Statewide",
                "county": "Statewide",
                "city": "Statewide",
                "state": "DE",
                "eligibility_age_min": 13,
                "eligibility_age_max": 17,
                "description": "Too young for this youth",
                "referral_method": "Enroll",
                "contact_phone": "211",
                "website": "https://example.org/mentor",
                "ai_match_rules": "needs_mentorship",
                "default_priority": "Medium",
                "caseworker_notes": "",
            },
        ]
    )

    matches = build_matches(youth_frame, resource_frame, top_n=5)

    assert matches["resource_id"].tolist() == ["R001"]
    assert matches.loc[0, "youth_id"] == "YP-0001"
    assert "housing" in matches.loc[0, "matched_need_tags"].split(";")
    assert matches.loc[0, "match_score"] > 0