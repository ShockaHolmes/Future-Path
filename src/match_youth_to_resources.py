from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PRIORITY_SCORES = {"High": 3, "Medium": 2, "Low": 1}
UNSTABLE_HOUSING_STATUSES = {
    "Couch surfing",
    "Temporary shelter",
    "Transitional housing",
    "At risk of homelessness",
}


def load_csv(input_path: Path) -> pd.DataFrame:
    return pd.read_csv(input_path)


def split_tags(raw_value: str) -> set[str]:
    if pd.isna(raw_value):
        return set()
    return {tag.strip() for tag in str(raw_value).split(";") if tag.strip()}


def derive_youth_need_tags(youth_row: pd.Series) -> set[str]:
    tags = {"general_support"}

    if youth_row["housing"] in UNSTABLE_HOUSING_STATUSES:
        tags.update({"housing", "homelessness", "case_management", "independent_living"})

    if youth_row["prior_homelessness"] == "Yes":
        tags.update({"housing", "homelessness", "case_management"})

    if youth_row["employment"] == "Unemployed":
        tags.update({"employment", "job_training", "career", "paid_work"})
    elif youth_row["employment"] == "Part-time":
        tags.update({"employment", "career", "resume", "professional_skills"})
    elif youth_row["employment"] == "Training / internship":
        tags.update({"employment", "career", "internship", "professional_skills"})

    if youth_row["education"] == "Not enrolled":
        tags.update({"education", "GED", "credentials", "postsecondary"})
    elif youth_row["education"] in {"Middle school", "High school"}:
        tags.update({"education", "academic_support", "career_readiness"})
    elif youth_row["education"] == "GED/HiSET":
        tags.update({"education", "postsecondary", "career_pathway"})
    else:
        tags.update({"postsecondary", "college", "career_pathway"})

    if youth_row["mentor_status"] == "Not assigned":
        tags.update({"mentorship", "community", "positive_adult"})

    if int(youth_row["placement_count"]) >= 5:
        tags.update({"case_management", "life_skills", "transition_support", "financial_literacy"})

    if int(youth_row["age"]) >= 18:
        tags.update({"young_adults", "independent_living"})

    return tags


def resource_is_eligible_for_youth(youth_row: pd.Series, resource_row: pd.Series) -> bool:
    age = int(youth_row["age"])
    county = youth_row["county"]

    if not (int(resource_row["eligibility_age_min"]) <= age <= int(resource_row["eligibility_age_max"])):
        return False

    resource_county = resource_row["county"]
    service_area = resource_row["service_area"]
    return resource_county in {county, "Statewide"} or service_area == "Statewide"


def build_matches(youth_frame: pd.DataFrame, resource_frame: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for _, youth_row in youth_frame.iterrows():
        youth_need_tags = derive_youth_need_tags(youth_row)
        candidate_rows: list[dict[str, object]] = []

        for _, resource_row in resource_frame.iterrows():
            if not resource_is_eligible_for_youth(youth_row, resource_row):
                continue

            resource_tags = split_tags(resource_row["need_tags"])
            matched_tags = sorted(youth_need_tags & resource_tags)
            if not matched_tags:
                continue

            priority_score = PRIORITY_SCORES.get(str(resource_row["default_priority"]), 0)
            match_score = len(matched_tags) * 10 + priority_score
            candidate_rows.append(
                {
                    "youth_id": youth_row["youth_id"],
                    "age": int(youth_row["age"]),
                    "youth_county": youth_row["county"],
                    "youth_need_tags": ";".join(sorted(youth_need_tags)),
                    "resource_id": resource_row["resource_id"],
                    "resource_name": resource_row["resource_name"],
                    "resource_category": resource_row["category"],
                    "resource_county": resource_row["county"],
                    "eligibility_age_min": int(resource_row["eligibility_age_min"]),
                    "eligibility_age_max": int(resource_row["eligibility_age_max"]),
                    "matched_need_tags": ";".join(matched_tags),
                    "match_score": match_score,
                    "default_priority": resource_row["default_priority"],
                    "referral_method": resource_row["referral_method"],
                    "website": resource_row["website"],
                }
            )

        candidate_rows.sort(
            key=lambda row: (-int(row["match_score"]), row["resource_name"], row["resource_id"])
        )
        rows.extend(candidate_rows[:top_n])

    return pd.DataFrame(rows)


def write_matches(frame: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match youth records to eligible Delaware youth resources.")
    parser.add_argument(
        "--youth-input",
        type=Path,
        default=Path("data/clean/synthetic_youth_transition_data_clean.csv"),
        help="Path to the cleaned youth CSV.",
    )
    parser.add_argument(
        "--resource-input",
        type=Path,
        default=Path("data/clean/future_path_delaware_youth_resources_clean.csv"),
        help="Path to the cleaned resource catalog CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/youth_resource_matches.csv"),
        help="Path to the matched youth-resource CSV.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Maximum number of resource matches to keep per youth.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_n < 1:
        raise ValueError("--top-n must be at least 1")

    youth_frame = load_csv(args.youth_input)
    resource_frame = load_csv(args.resource_input)
    matched = build_matches(youth_frame, resource_frame, top_n=args.top_n)
    write_matches(matched, args.output)
    print(f"Loaded {len(youth_frame)} youth rows from {args.youth_input}")
    print(f"Loaded {len(resource_frame)} resource rows from {args.resource_input}")
    print(f"Saved {len(matched)} youth-resource matches to {args.output}")


if __name__ == "__main__":
    main()