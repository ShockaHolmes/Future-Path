from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from clean_caseworker_youth_data import clean_data as clean_caseworker_data
from clean_synthetic_youth_data import clean_data as clean_public_data
from generate_synthetic_youth_data import COUNTIES, generate_caseworker_records, generate_records
from load_youth_data_to_database import (
    CASEWORKER_TABLE,
    PUBLIC_TABLE,
    RESOURCE_TABLE,
    validate_public_data_for_load,
    validate_relationships,
    write_tables,
)


def test_generate_records_produces_delaware_teens() -> None:
    records = generate_records(count=25, seed=7)

    assert len(records) == 25
    assert all(13 <= record.age <= 19 for record in records)
    assert {record.county for record in records}.issubset(set(COUNTIES))
    assert len({record.youth_id for record in records}) == 25


def test_generate_caseworker_records_match_public_ids() -> None:
    public_records = generate_records(count=10, seed=11)
    caseworker_records = generate_caseworker_records(public_records, seed=12)

    assert [record.youth_id for record in caseworker_records] == [record.youth_id for record in public_records]
    assert all(record.first_name for record in caseworker_records)
    assert all(record.last_name for record in caseworker_records)


def test_clean_public_data_standardizes_and_filters_rows() -> None:
    raw_frame = pd.DataFrame(
        [
            {
                "Youth ID": "YP-0001",
                "Age": "16",
                "County": "Kent",
                "Education": "High school",
                "Employment": "Part-time",
                "Housing": "Stable housing",
                "Mentor Status": "Assigned",
                "Placement Count": "2",
                "Prior Homelessness": "No",
            },
            {
                "Youth ID": "YP-0001",
                "Age": "17",
                "County": "Sussex",
                "Education": "GED/HiSET",
                "Employment": "Unemployed",
                "Housing": "Couch surfing",
                "Mentor Status": "Not assigned",
                "Placement Count": "5",
                "Prior Homelessness": "Yes",
            },
            {
                "Youth ID": "YP-0002",
                "Age": None,
                "County": None,
                "Education": None,
                "Employment": None,
                "Housing": None,
                "Mentor Status": None,
                "Placement Count": None,
                "Prior Homelessness": None,
            },
            {
                "Youth ID": "YP-0003",
                "Age": "22",
                "County": "New Castle",
                "Education": "Some college",
                "Employment": "Full-time",
                "Housing": "Stable housing",
                "Mentor Status": "Assigned",
                "Placement Count": "1",
                "Prior Homelessness": "No",
            },
        ]
    )

    cleaned = clean_public_data(raw_frame)

    assert list(cleaned.columns) == [
        "youth_id",
        "age",
        "county",
        "education",
        "employment",
        "housing",
        "mentor_status",
        "placement_count",
        "prior_homelessness",
    ]
    assert cleaned["youth_id"].tolist() == ["YP-0001", "YP-0002"]
    assert cleaned.loc[cleaned["youth_id"] == "YP-0002", "age"].item() == 13
    assert cleaned.loc[cleaned["youth_id"] == "YP-0002", "placement_count"].item() == 0
    assert cleaned.loc[cleaned["youth_id"] == "YP-0002", "county"].item() == "Unknown"


def test_clean_caseworker_data_deduplicates_and_drops_missing_ids() -> None:
    raw_frame = pd.DataFrame(
        [
            {"Youth ID": "YP-0001", "First Name": "Mia", "Last Name": "Lopez"},
            {"Youth ID": "YP-0001", "First Name": "Ava", "Last Name": "Smith"},
            {"Youth ID": "", "First Name": "", "Last Name": None},
            {"Youth ID": "YP-0002", "First Name": None, "Last Name": "Taylor"},
        ]
    )

    cleaned = clean_caseworker_data(raw_frame)

    assert cleaned["youth_id"].tolist() == ["YP-0001", "YP-0002"]
    assert cleaned.loc[cleaned["youth_id"] == "YP-0002", "first_name"].item() == "Unknown"
    assert cleaned.loc[cleaned["youth_id"] == "YP-0002", "last_name"].item() == "Taylor"


def test_validate_relationships_rejects_mismatched_ids() -> None:
    public_frame = pd.DataFrame([{"youth_id": "YP-0001"}])
    caseworker_frame = pd.DataFrame([{"youth_id": "YP-9999"}])

    with pytest.raises(ValueError):
        validate_relationships(public_frame, caseworker_frame)


def test_validate_public_data_for_load_accepts_valid_frame() -> None:
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

    validate_public_data_for_load(public_frame)


def test_validate_public_data_for_load_rejects_invalid_values() -> None:
    public_frame = pd.DataFrame(
        [
            {
                "youth_id": "",
                "age": 22,
                "county": "Kent",
                "education": "High school",
                "employment": "Gig",
                "housing": "Car",
                "mentor_status": "Assigned",
                "placement_count": 2,
                "prior_homelessness": "No",
            }
        ]
    )

    with pytest.raises(ValueError):
        validate_public_data_for_load(public_frame)


def test_write_tables_loads_both_datasets_into_sqlite(tmp_path: Path) -> None:
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
            },
            {
                "youth_id": "YP-0002",
                "age": 18,
                "county": "New Castle",
                "education": "GED/HiSET",
                "employment": "Unemployed",
                "housing": "Transitional housing",
                "mentor_status": "Not assigned",
                "placement_count": 4,
                "prior_homelessness": "Yes",
            },
        ]
    )
    caseworker_frame = pd.DataFrame(
        [
            {"youth_id": "YP-0001", "first_name": "Mia", "last_name": "Lopez"},
            {"youth_id": "YP-0002", "first_name": "Noah", "last_name": "Taylor"},
        ]
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
                "caseworker_notes": "",
            }
        ]
    )
    database_path = tmp_path / "future_path_test.db"

    validate_relationships(public_frame, caseworker_frame)
    write_tables(public_frame, caseworker_frame, resource_frame, database_path)

    with sqlite3.connect(database_path) as connection:
        public_count = connection.execute(f"SELECT COUNT(*) FROM {PUBLIC_TABLE}").fetchone()[0]
        caseworker_count = connection.execute(f"SELECT COUNT(*) FROM {CASEWORKER_TABLE}").fetchone()[0]
        resource_count = connection.execute(f"SELECT COUNT(*) FROM {RESOURCE_TABLE}").fetchone()[0]
        joined_count = connection.execute(
            f"SELECT COUNT(*) FROM {PUBLIC_TABLE} p JOIN {CASEWORKER_TABLE} c ON p.youth_id = c.youth_id"
        ).fetchone()[0]

    assert public_count == 2
    assert caseworker_count == 2
    assert resource_count == 1
    assert joined_count == 2