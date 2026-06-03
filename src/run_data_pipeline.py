from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from clean_caseworker_youth_data import clean_data as clean_caseworker_data
from clean_youth_resource_catalog import clean_data as clean_resource_data
from clean_synthetic_youth_data import clean_data as clean_public_data
from clean_synthetic_youth_data import load_data as load_public_csv
from clean_synthetic_youth_data import write_data
from generate_synthetic_youth_data import generate_caseworker_records, generate_records, write_caseworker_csv, write_csv
from load_youth_data_to_database import validate_relationships, write_tables
from match_youth_to_resources import build_matches, write_matches


DEFAULT_RAW_PUBLIC = Path("data/raw/synthetic_youth_transition_data.csv")
DEFAULT_RAW_CASEWORKER = Path("data/raw/synthetic_youth_caseworker_data.csv")
DEFAULT_RAW_RESOURCE = Path("data/raw/future_path_delaware_youth_resources.csv")
DEFAULT_CLEAN_PUBLIC = Path("data/clean/synthetic_youth_transition_data_clean.csv")
DEFAULT_CLEAN_CASEWORKER = Path("data/clean/synthetic_youth_caseworker_data_clean.csv")
DEFAULT_CLEAN_RESOURCE = Path("data/clean/future_path_delaware_youth_resources_clean.csv")
DEFAULT_MATCH_OUTPUT = Path("data/processed/youth_resource_matches.csv")
DEFAULT_DATABASE = Path("database/future_path.db")


def generate_raw_data(count: int, seed: int, raw_public_path: Path, raw_caseworker_path: Path) -> None:
    public_records = generate_records(count=count, seed=seed)
    caseworker_records = generate_caseworker_records(public_records, seed=seed + 1)
    write_csv(public_records, raw_public_path)
    write_caseworker_csv(caseworker_records, raw_caseworker_path)
    print(f"Generated {len(public_records)} public records at {raw_public_path}")
    print(f"Generated {len(caseworker_records)} caseworker records at {raw_caseworker_path}")


def clean_generated_data(
    raw_public_path: Path,
    raw_caseworker_path: Path,
    raw_resource_path: Path,
    clean_public_path: Path,
    clean_caseworker_path: Path,
    clean_resource_path: Path,
) -> tuple[int, int, int]:
    public_frame = load_public_csv(raw_public_path)
    caseworker_frame = load_public_csv(raw_caseworker_path)
    resource_frame = load_public_csv(raw_resource_path)

    cleaned_public = clean_public_data(public_frame)
    cleaned_caseworker = clean_caseworker_data(caseworker_frame)
    cleaned_resource = clean_resource_data(resource_frame)

    write_data(cleaned_public, clean_public_path)
    write_data(cleaned_caseworker, clean_caseworker_path)
    write_data(cleaned_resource, clean_resource_path)

    print(f"Cleaned public data to {clean_public_path}")
    print(f"Cleaned caseworker data to {clean_caseworker_path}")
    print(f"Cleaned resource catalog to {clean_resource_path}")
    return len(cleaned_public), len(cleaned_caseworker), len(cleaned_resource)


def load_database(
    clean_public_path: Path,
    clean_caseworker_path: Path,
    clean_resource_path: Path,
    database_path: Path,
) -> None:
    public_frame = load_public_csv(clean_public_path)
    caseworker_frame = load_public_csv(clean_caseworker_path)
    resource_frame = load_public_csv(clean_resource_path)
    validate_relationships(public_frame, caseworker_frame)
    write_tables(public_frame, caseworker_frame, resource_frame, database_path)
    print(f"Loaded cleaned data into {database_path}")


def create_resource_matches(
    clean_public_path: Path,
    clean_resource_path: Path,
    match_output_path: Path,
    top_n: int,
) -> int:
    youth_frame = load_public_csv(clean_public_path)
    resource_frame = load_public_csv(clean_resource_path)
    matches = build_matches(youth_frame, resource_frame, top_n=top_n)
    write_matches(matches, match_output_path)
    print(f"Saved {len(matches)} youth-resource matches to {match_output_path}")
    return len(matches)


def run_tests(test_path: str) -> None:
    exit_code = pytest.main([test_path, "-q"])
    if exit_code != 0:
        raise SystemExit(exit_code)
    print(f"Test suite passed: {test_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Future-Path data pipeline end to end.")
    parser.add_argument("--count", type=int, default=500, help="Number of synthetic youth records to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for repeatable synthetic data.")
    parser.add_argument("--raw-public", type=Path, default=DEFAULT_RAW_PUBLIC, help="Raw public CSV output path.")
    parser.add_argument(
        "--raw-caseworker",
        type=Path,
        default=DEFAULT_RAW_CASEWORKER,
        help="Raw caseworker CSV output path.",
    )
    parser.add_argument(
        "--raw-resource",
        type=Path,
        default=DEFAULT_RAW_RESOURCE,
        help="Raw youth resource catalog path.",
    )
    parser.add_argument(
        "--clean-public",
        type=Path,
        default=DEFAULT_CLEAN_PUBLIC,
        help="Clean public CSV output path.",
    )
    parser.add_argument(
        "--clean-caseworker",
        type=Path,
        default=DEFAULT_CLEAN_CASEWORKER,
        help="Clean caseworker CSV output path.",
    )
    parser.add_argument(
        "--clean-resource",
        type=Path,
        default=DEFAULT_CLEAN_RESOURCE,
        help="Clean resource catalog CSV output path.",
    )
    parser.add_argument(
        "--match-output",
        type=Path,
        default=DEFAULT_MATCH_OUTPUT,
        help="Youth-resource match output CSV path.",
    )
    parser.add_argument(
        "--match-top-n",
        type=int,
        default=5,
        help="Maximum number of resource matches to keep per youth.",
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite output path.")
    parser.add_argument(
        "--test-path",
        default="tests/test_data_pipeline.py",
        help="Pytest target to run after loading data.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise ValueError("--count must be at least 1")
    if args.match_top_n < 1:
        raise ValueError("--match-top-n must be at least 1")

    generate_raw_data(args.count, args.seed, args.raw_public, args.raw_caseworker)
    public_rows, caseworker_rows, resource_rows = clean_generated_data(
        args.raw_public,
        args.raw_caseworker,
        args.raw_resource,
        args.clean_public,
        args.clean_caseworker,
        args.clean_resource,
    )
    print(
        "Prepared "
        f"{public_rows} cleaned public rows, "
        f"{caseworker_rows} cleaned caseworker rows, and "
        f"{resource_rows} cleaned resource rows"
    )
    load_database(args.clean_public, args.clean_caseworker, args.clean_resource, args.database)
    match_rows = create_resource_matches(
        args.clean_public,
        args.clean_resource,
        args.match_output,
        top_n=args.match_top_n,
    )
    print(f"Prepared {match_rows} youth-resource matches")
    run_tests(args.test_path)
    print("Pipeline completed successfully")


if __name__ == "__main__":
    main()