# Future-Path

## Project Goal

Future-Path is a youth transition data engineering project focused on building a safe, practical decision-support pipeline for case teams.

The goal is to simulate a real-world environment where teams can:

- analyze youth transition patterns without exposing private personal information,
- maintain a restricted caseworker view for identity-linked support,
- organize and clean a Delaware youth resource catalog,
- and generate data-driven resource recommendations for each youth.

## What This Project Builds

This project builds an end-to-end pipeline that:

1. Generates synthetic youth datasets (public and caseworker views).
2. Cleans youth and resource data for reliable analysis and loading.
3. Loads cleaned data into SQLite tables for downstream querying.
4. Matches youth needs to eligible Delaware resources.
5. Runs automated tests to validate core pipeline behavior.

In short: Future-Path is a foundation for privacy-aware analytics and referral matching for youth transition support.

## Setup

Run these commands from the project root:

```bash
python3 -m pip install -r requirements.txt
```

## Pipeline Commands

Generate the synthetic raw datasets:

```bash
python3 src/generate_synthetic_youth_data.py
```

Clean the de-identified youth dataset:

```bash
python3 src/clean_synthetic_youth_data.py
```

Clean the caseworker dataset:

```bash
python3 src/clean_caseworker_youth_data.py
```

Clean the youth resource catalog:

```bash
python3 src/clean_youth_resource_catalog.py
```

Load the cleaned datasets into SQLite:

```bash
python3 src/load_youth_data_to_database.py
```

Load cleaned youth records into relational `youth_profiles` (ETL step):

```bash
python3 src/load_youth_profiles_etl.py
```

Build youth-to-resource matches from cleaned data:

```bash
python3 src/match_youth_to_resources.py
```

Run the full pipeline (generate, clean, load, match, and test) with one command:

```bash
python3 src/run_data_pipeline.py
```

Run the full data pipeline step by step:

```bash
python3 src/generate_synthetic_youth_data.py
python3 src/clean_synthetic_youth_data.py
python3 src/clean_caseworker_youth_data.py
python3 src/clean_youth_resource_catalog.py
python3 src/load_youth_data_to_database.py
python3 src/match_youth_to_resources.py
```

## Test Commands

Run the pipeline test suite:

```bash
pytest tests/test_data_pipeline.py -q
```

Run the dedicated resource cleaner/loader/matcher tests:

```bash
pytest tests/test_resource_catalog.py tests/test_resource_pipeline.py -q
```

Run all tests:

```bash
pytest -q
```

## Synthetic Data Files

The project generates two synthetic CSV files in `data/raw/`:

- `synthetic_youth_transition_data.csv`: De-identified dataset for analytics, dashboards, and model development.
- `synthetic_youth_caseworker_data.csv`: Restricted dataset containing PII (`first_name`, `last_name`) mapped to `youth_id` for caseworker use only.

## Youth Resource Catalog

- `future_path_delaware_youth_resources.csv`: Delaware youth resource directory for referrals, eligibility screening, and AI-assisted resource matching.
- File location: `data/raw/future_path_delaware_youth_resources.csv`

## PII Handling

- Use `synthetic_youth_transition_data.csv` for reporting and visualization.
- Do not expose `synthetic_youth_caseworker_data.csv` in dashboards, shared exports, or public reports.
- Treat the caseworker file as sensitive data and limit access to authorized staff.