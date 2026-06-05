# Future Path Demo Cheat Sheet (60 Seconds)

Labels: demo, presentation

## Goal
Deliver a clear one-minute walkthrough of the project problem, pipeline, AI workflow, and outcomes.

## 60-Second Script

1. 0:00-0:08 | Problem
- Youth support teams face fragmented data and low visibility into urgent needs.
- Future Path combines data engineering + AI-guided intake for faster, clearer support decisions.

2. 0:08-0:15 | Pipeline Run
- Command: `python3 src/run_data_pipeline.py`
- Say: "This generates synthetic data, cleans and validates it, loads SQLite tables, and prepares risk and matching outputs."

3. 0:15-0:25 | Open Dashboard
- Show Overview: `http://localhost:8501`
- Say: "This gives an operational snapshot of youth support and priorities."

4. 0:25-0:33 | Risk Score
- Point to risk-level/risk-summary areas.
- Say: "Risk scoring is explainable and helps prioritize high-need youth."

5. 0:33-0:50 | AI Assistant Intake
- Open AI Assistant and complete fast guided answers.
- Say: "The intake captures structured signals that directly inform recommendations."

6. 0:50-1:00 | Recommended Resources
- Show recommendations and contact details (phone/email/website).
- Say: "Recommendations combine risk + intake context and include actionable contact information."

## Local Run Checklist

1. `python3 -m pip install -r requirements.txt`
2. `python3 src/run_data_pipeline.py`
3. `streamlit run dashboard/overview.py`

## If Live Demo Fails (Screenshot Backup)

Use in this order:

1. [docs/screenshots/dashboard-overview.svg](docs/screenshots/dashboard-overview.svg)
2. [docs/screenshots/ai-assistant.svg](docs/screenshots/ai-assistant.svg)

Optional extra backups to capture before presenting:

- `docs/screenshots/risk-score-view.png`
- `docs/screenshots/recommended-resources-view.png`

## 10-Second Closing Line
"Future Path shows an end-to-end, testable workflow where clean data and AI-assisted intake turn into prioritized, actionable support plans for youth transitions."
