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

Calculate youth risk scores and store them in `risk_scores`:

```bash
python3 src/calculate_risk_scores.py
```

Generate database recommendations from youth needs and risk factors:

```bash
python3 src/generate_recommendations.py
```

Automatically assign resources from completed AI intake responses:

```bash
python3 src/assign_resources_from_intake.py --session-id intake-REPLACE_WITH_SESSION_ID --top-n 5
```

If `--session-id` is omitted, the script uses the latest completed intake session.
It prints a clear summary with identified needs, total risk points, assigned resources, match reason, and priority level.

Run the Future Path AI Assistant intake (guided one-question-at-a-time flow):

```bash
python3 src/future_path_ai_intake.py --youth-id YP-0001
```

The intake flow shows a privacy and safety notice before any questions begin. It uses synthetic or demo data and is a decision-support tool, not a crisis service or replacement for professional case management.

Run intake for a pre-enrollment candidate profile:

```bash
python3 src/future_path_ai_intake.py --candidate-id CP-9001
```

Intake persistence details for case manager review:

- Each session is stored in `intake_sessions` with `session_status`, `completed_at`, and `top_need_category`.
- Each question/answer is stored in `intake_answers` with `question_text` and `answer_value`.
- Sessions can be linked to either `youth_id` or `candidate_profile_id`.
- Recommendations are linked back to intake context through `recommendations.intake_session_id`.

Example queries:

```sql
-- Review completed intake sessions with top need
SELECT intake_session_id, youth_id, candidate_profile_id, top_need_category, completed_at
FROM intake_sessions
WHERE session_status = 'completed'
ORDER BY completed_at DESC;

-- Review all captured answers for a specific session
SELECT question_key, question_text, answer_value, answered_at
FROM intake_answers
WHERE intake_session_id = 'intake-REPLACE_WITH_SESSION_ID'
ORDER BY intake_answer_id;

-- Join intake context to recommendations for follow-up
SELECT r.youth_id, r.resource_id, r.recommendation_reason, r.priority_rank, r.intake_session_id
FROM recommendations r
WHERE r.intake_session_id IS NOT NULL
ORDER BY r.youth_id, r.priority_rank;
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

Run risk scoring tests:

```bash
pytest tests/test_risk_scoring.py -q
```

Run recommendation logic tests:

```bash
pytest tests/test_recommendations.py -q
```

Run AI intake flow tests:

```bash
pytest tests/test_ai_intake.py -q
```

Run intake-to-resource assignment tests:

```bash
pytest tests/test_intake_resource_assignment.py -q
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

## Intake Assignment Queries

Use these queries to review what was assigned from intake results:

```sql
-- View assigned resources with priority and reason for a session
SELECT intake_session_id, profile_type, youth_id, candidate_profile_id, resource_id, priority_level, match_score, match_reason
FROM assigned_resources
WHERE intake_session_id = 'intake-REPLACE_WITH_SESSION_ID'
ORDER BY priority_level, match_score DESC;

-- View all recent assignments generated by AI intake matching
SELECT assignment_id, intake_session_id, assigned_by, assigned_at, priority_level, resource_id
FROM assigned_resources
WHERE assigned_by = 'ai_intake_matcher_v1'
ORDER BY assigned_at DESC;
```