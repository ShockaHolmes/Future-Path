from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from clean_youth_resource_catalog import clean_data as clean_resource_data
from load_youth_data_to_database import RESOURCE_TABLE, write_tables


def test_clean_resource_catalog_standardizes_and_filters_rows() -> None:
    raw_frame = pd.DataFrame(
        [
            {
                "Resource ID": "R100",
                "Resource Name": "Housing Helper",
                "Category": "Housing",
                "Need Tags": "housing;case_management",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": "",
                "Eligibility Age Min": "14",
                "Eligibility Age Max": "21",
                "Description": "Housing and support resource.",
                "Referral Method": "Call",
                "Contact Phone": "",
                "Website": "https://example.org/housing",
                "AI Match Rules": "needs_housing",
                "Default Priority": "",
                "Caseworker Notes": None,
            },
            {
                "Resource ID": "R100",
                "Resource Name": "Housing Helper Duplicate",
                "Category": "Housing",
                "Need Tags": "housing",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": "DE",
                "Eligibility Age Min": "14",
                "Eligibility Age Max": "21",
                "Description": "Duplicate row.",
                "Referral Method": "Call",
                "Contact Phone": "302-000-0000",
                "Website": "https://example.org/dup",
                "AI Match Rules": "needs_housing",
                "Default Priority": "High",
                "Caseworker Notes": "",
            },
            {
                "Resource ID": "R101",
                "Resource Name": "Bad Ages",
                "Category": "Other",
                "Need Tags": "other",
                "Service Area": "Statewide",
                "County": "Statewide",
                "City": "Statewide",
                "State": "DE",
                "Eligibility Age Min": "30",
                "Eligibility Age Max": "10",
                "Description": "Invalid age range.",
                "Referral Method": "Apply",
                "Contact Phone": "",
                "Website": "https://example.org/bad",
                "AI Match Rules": "other",
                "Default Priority": "Low",
                "Caseworker Notes": "",
            },
        ]
    )

    cleaned = clean_resource_data(raw_frame)

    assert list(cleaned.columns) == [
        "resource_id",
        "resource_name",
        "category",
        "need_tags",
        "service_area",
        "county",
        "city",
        "state",
        "eligibility_age_min",
        "eligibility_age_max",
        "description",
        "referral_method",
        "contact_phone",
        "website",
        "ai_match_rules",
        "default_priority",
        "caseworker_notes",
    ]
    assert cleaned["resource_id"].tolist() == ["R100"]
    assert cleaned.loc[0, "state"] == "DE"
    assert cleaned.loc[0, "default_priority"] == "Medium"
    assert cleaned.loc[0, "contact_phone"] == "Not listed"


def test_write_tables_inserts_resource_rows(tmp_path: Path) -> None:
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
                "resource_id": "R900",
                "resource_name": "Mentor Connect",
                "category": "Mentorship",
                "need_tags": "mentorship;community",
                "service_area": "Statewide",
                "county": "Statewide",
                "city": "Statewide",
                "state": "DE",
                "eligibility_age_min": 12,
                "eligibility_age_max": 24,
                "description": "Mentoring support.",
                "referral_method": "Referral",
                "contact_phone": "",
                "website": "https://example.org/mentor",
                "ai_match_rules": "needs_mentor",
                "default_priority": "High",
                "caseworker_notes": "",
            }
        ]
    )
    database_path = tmp_path / "resources_loader_test.db"

    write_tables(public_frame, caseworker_frame, resource_frame, database_path)

    with sqlite3.connect(database_path) as connection:
        resource_count = connection.execute(f"SELECT COUNT(*) FROM {RESOURCE_TABLE}").fetchone()[0]
        row = connection.execute(
            f"SELECT resource_id, contact_phone FROM {RESOURCE_TABLE} WHERE resource_id = 'R900'"
        ).fetchone()

    assert resource_count == 1
    assert row == ("R900", "")