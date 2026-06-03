from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from random import Random

import csv

COUNTIES = [
    "New Castle",
    "Kent",
    "Sussex",
]

COUNTY_WEIGHTS = [0.58, 0.2, 0.22]

EDUCATION_LEVELS = [
    "Not enrolled",
    "Middle school",
    "High school",
    "GED/HiSET",
    "Some college",
    "Associate degree",
]

EMPLOYMENT_STATUSES = [
    "Unemployed",
    "Part-time",
    "Full-time",
    "Seasonal",
    "Training / internship",
]

HOUSING_STATUSES = [
    "Stable housing",
    "Couch surfing",
    "Temporary shelter",
    "Transitional housing",
    "At risk of homelessness",
]

FIRST_NAMES = [
    "Aaliyah",
    "Amir",
    "Ava",
    "Camila",
    "Carter",
    "Daniel",
    "Elijah",
    "Emma",
    "Ethan",
    "Gabriella",
    "Isaiah",
    "Jayden",
    "Jordan",
    "Liam",
    "Maya",
    "Mia",
    "Noah",
    "Olivia",
    "Sophia",
    "Zoe",
]

LAST_NAMES = [
    "Anderson",
    "Bailey",
    "Brown",
    "Carter",
    "Davis",
    "Flores",
    "Garcia",
    "Green",
    "Hill",
    "Johnson",
    "Lee",
    "Lopez",
    "Martinez",
    "Miller",
    "Moore",
    "Nguyen",
    "Robinson",
    "Smith",
    "Taylor",
    "Williams",
]


@dataclass(frozen=True)
class YouthRecord:
    youth_id: str
    age: int
    county: str
    education: str
    employment: str
    housing: str
    mentor_status: str
    placement_count: int
    prior_homelessness: str

    def as_row(self) -> dict[str, object]:
        return {
            "youth_id": self.youth_id,
            "age": self.age,
            "county": self.county,
            "education": self.education,
            "employment": self.employment,
            "housing": self.housing,
            "mentor_status": self.mentor_status,
            "placement_count": self.placement_count,
            "prior_homelessness": self.prior_homelessness,
        }


@dataclass(frozen=True)
class CaseworkerRecord:
    youth_id: str
    first_name: str
    last_name: str

    def as_row(self) -> dict[str, str]:
        return {
            "youth_id": self.youth_id,
            "first_name": self.first_name,
            "last_name": self.last_name,
        }


def build_record(index: int, rng: Random) -> YouthRecord:
    age = rng.randint(13, 19)
    education = rng.choice(EDUCATION_LEVELS)

    if age <= 16:
        employment_weights = [0.7, 0.15, 0.02, 0.08, 0.05]
    elif age <= 18:
        employment_weights = [0.4, 0.25, 0.1, 0.15, 0.1]
    else:
        employment_weights = [0.15, 0.25, 0.35, 0.1, 0.15]

    employment = rng.choices(EMPLOYMENT_STATUSES, weights=employment_weights, k=1)[0]

    housing_weights = [0.55, 0.12, 0.08, 0.1, 0.15]
    housing = rng.choices(HOUSING_STATUSES, weights=housing_weights, k=1)[0]

    mentor_status = rng.choices(["Assigned", "Not assigned"], weights=[0.68, 0.32], k=1)[0]
    placement_count = min(rng.randint(0, 6) + (1 if housing != "Stable housing" else 0), 8)
    prior_homelessness = rng.choices(["Yes", "No"], weights=[0.28, 0.72], k=1)[0]

    return YouthRecord(
        youth_id=f"YP-{index:04d}",
        age=age,
        county=rng.choices(COUNTIES, weights=COUNTY_WEIGHTS, k=1)[0],
        education=education,
        employment=employment,
        housing=housing,
        mentor_status=mentor_status,
        placement_count=placement_count,
        prior_homelessness=prior_homelessness,
    )


def generate_records(count: int, seed: int) -> list[YouthRecord]:
    rng = Random(seed)
    return [build_record(index + 1, rng) for index in range(count)]


def generate_caseworker_records(records: list[YouthRecord], seed: int) -> list[CaseworkerRecord]:
    rng = Random(seed)
    return [
        CaseworkerRecord(
            youth_id=record.youth_id,
            first_name=rng.choice(FIRST_NAMES),
            last_name=rng.choice(LAST_NAMES),
        )
        for record in records
    ]


def write_csv(records: list[YouthRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].as_row().keys()))
        writer.writeheader()
        writer.writerows(record.as_row() for record in records)


def write_caseworker_csv(records: list[CaseworkerRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].as_row().keys()))
        writer.writeheader()
        writer.writerows(record.as_row() for record in records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic youth transition data.")
    parser.add_argument("--count", type=int, default=500, help="Number of synthetic records to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for repeatable output.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/synthetic_youth_transition_data.csv"),
        help="Destination CSV path.",
    )
    parser.add_argument(
        "--caseworker-output",
        type=Path,
        default=Path("data/raw/synthetic_youth_caseworker_data.csv"),
        help="Destination CSV path for caseworker PII mapping.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise ValueError("--count must be at least 1")

    records = generate_records(args.count, args.seed)
    caseworker_records = generate_caseworker_records(records, seed=args.seed + 1)
    write_csv(records, args.output)
    write_caseworker_csv(caseworker_records, args.caseworker_output)
    print(f"Wrote {len(records)} synthetic youth records to {args.output}")
    print(f"Wrote {len(caseworker_records)} caseworker records to {args.caseworker_output}")


if __name__ == "__main__":
    main()