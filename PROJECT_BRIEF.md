# Future Path: 60-Second Project Brief

## What This Project Is

Future Path is a portfolio-ready data engineering and analytics system for youth transition support. It combines synthetic data generation, ETL, risk scoring, recommendation logic, an AI-assisted intake flow, and Streamlit dashboards.

## Why It Matters

Teams supporting youth often work with fragmented information. Future Path demonstrates how to turn messy inputs into clean, actionable decisions with transparency and test coverage.

## What I Built

- End-to-end data pipeline from raw synthetic data to analytics-ready tables
- SQLite relational model for profiles, risks, recommendations, intake, and assignments
- Rules-based risk scoring with explainable factors
- Recommendation and assignment logic linked to intake responses
- Streamlit experience with:
  - dashboard overview
  - youth profile lookup
  - AI Assistant intake workflow
- Automated pytest suite for core logic and insert behavior

## Technical Highlights

- Python, pandas, SQLite, Streamlit, pytest
- Data quality checks before load
- Schema integrity and migration-safe table handling
- Risk and recommendation outputs persisted for auditability
- Intake workflow designed for privacy-aware decision support

## Outcomes

- Reproducible pipeline that runs end to end
- Clear operational metrics and visual insights for instructors/employers
- Traceable recommendation path from answers to assigned resources
- Strong quality baseline with passing automated tests

## What This Demonstrates

- Practical data engineering execution
- Analytics and recommendation system thinking
- Product-minded dashboard and assistant design
- Testing discipline and maintainable project structure

## Where To Start

- Full documentation: [README.md](README.md)
- Dashboard overview: [dashboard/overview.py](dashboard/overview.py)
- Profile lookup: [dashboard/profile_lookup.py](dashboard/profile_lookup.py)
- AI Assistant UI: [dashboard/ai_assistant.py](dashboard/ai_assistant.py)
