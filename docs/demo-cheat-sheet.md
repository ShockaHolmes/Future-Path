# Future Path Demo Cheat Sheet (60 Seconds)

Labels: demo, presentation

## Goal
Deliver a clear one-minute walkthrough of the project problem, pipeline, operational overview, AI-assisted intake, caseworker workflow, and youth-facing outcomes — moving through the live dashboards as you speak.

---

## Pre-Demo Setup (Do This Before Presenting)

1. Run the pipeline: `python3 src/run_data_pipeline.py`
2. Start all dashboards: `./start.command` (all five open automatically)
3. Have these five browser tabs open and ready:
   - **Overview** → `http://localhost:8601`
   - **AI Assistant** → `http://localhost:8603`
   - **Caseworker Dashboard** → `http://localhost:8604`
   - **Youth Dashboard** → `http://localhost:8605`
   - **Youth Profiles** → `http://localhost:8602`
4. Start on the Overview tab before you begin speaking.

---

## 60-Second Presentation Script

### 0:00 – 0:08 | The Problem *(stay on Overview — top of page)*

**URL:** `http://localhost:8601`

> "Youth transition programs run on fragmented data. Caseworkers don't know who needs help first,
> intake is inconsistent, and support decisions are often made without clear evidence.
> Future Path fixes that with a reproducible data pipeline, explainable risk scoring, and AI-guided intake."

**Point at:** the header and badge — "Youth Transition Support Dashboard."

---

### 0:08 – 0:18 | Pipeline Output + KPI Cards *(scroll to KPIs)*

**URL:** `http://localhost:8601#overview-metrics`

> "The pipeline runs end to end — generating synthetic profiles, cleaning records, loading SQLite tables,
> scoring risk, and generating resource recommendations. These KPI cards reflect that output live."

**Point at:** total youth count, high-risk count, avg risk score, active resources.

---

### 0:18 – 0:26 | Risk Breakdown *(scroll to risk chart)*

**URL:** `http://localhost:8601#risk-score-breakdown`

> "Risk scoring is transparent and auditable. Each score is broken down by housing, employment,
> and education factors. You can see exactly why a youth is flagged as high priority."

**Point at:** the risk-level pie chart and the breakdown columns beside it.

---

### 0:26 – 0:34 | County Insights + Candidate Queue *(scroll down)*

**URL:** `http://localhost:8601#county-insights`

> "County-level insights let supervisors prioritize outreach by region.
> The candidate queue at the bottom shows pre-intake records ready for promotion into full profiles."

**Point at:** county chart, then scroll briefly to `#candidate-queue`.

---

### 0:34 – 0:45 | AI Assistant Intake *(switch tab)*

**URL:** `http://localhost:8603#ai-start-intake`

> "When a youth or caseworker starts intake, the AI Assistant walks through ten structured questions —
> housing, employment, education, safety, and primary need.
> Each answer is stored, validated, and used immediately in the recommendation engine."

**Point at:** the Start Intake section and the progress bar as questions advance. Answer one or two questions live if time allows.

---

### 0:45 – 0:53 | Recommendations + Contact Details *(scroll after intake or show completed state)*

**URL:** `http://localhost:8603#ai-summary`

> "After intake, recommendations are generated from the combined risk score and intake answers.
> Each one includes a priority level and direct contact information — phone, email, and website —
> so the next action is always one click away."

**Point at:** priority badges, resource names, and contact fields in the summary cards.

---

### 0:53 – 1:00 | Caseworker View — Assigned Youth + Resource Assignment *(switch tab)*

**URL:** `http://localhost:8604#assigned-youth`

> "Caseworkers see their full caseload, assigned resources, and follow-up status in one place.
> From here they can assign cases, review AI intake results, and track outreach — all traceable back
> to the same data pipeline that started this demo."

**Point at:** assigned youth table and the resource assignment anchor (`#resource-assignment` if time allows).

---

## Closing Line *(spoken while on Caseworker Dashboard)*

> "Future Path is an end-to-end, testable system — clean data, explainable scoring, AI-guided intake,
> and a full caseworker workflow — all built to be auditable, reproducible, and production-ready."

---

## Navigation Reference

| Dashboard | URL | Key Anchors |
|---|---|---|
| Overview | `http://localhost:8601` | `#overview-metrics` · `#risk-score-breakdown` · `#county-insights` · `#top-supports` · `#candidate-queue` · `#insight-callouts` |
| Youth Profiles | `http://localhost:8602` | *(scroll to find a profile)* |
| AI Assistant | `http://localhost:8603` | `#ai-start-intake` · `#ai-current-intake` · `#ai-summary` |
| Caseworker | `http://localhost:8604` | `#caseload-overview` · `#assigned-youth` · `#candidate-promotion` · `#case-workspace` · `#ai-results` · `#resource-assignment` · `#outreach-queue` · `#follow-up-tracker` |
| Youth Dashboard | `http://localhost:8605` | `#youth-profile` · `#youth-kpis` · `#youth-intake` · `#youth-needs` · `#youth-resources` · `#youth-next-steps` · `#youth-caseworker` |

---

## If Live Demo Fails (Screenshot Backup)

Use in this order:

1. [docs/screenshots/dashboard-overview.svg](docs/screenshots/dashboard-overview.svg)
2. [docs/screenshots/ai-assistant.svg](docs/screenshots/ai-assistant.svg)

Recommended extras to screenshot before presenting:

- `docs/screenshots/risk-score-view.png` (Overview `#risk-score-breakdown`)
- `docs/screenshots/recommended-resources-view.png` (AI Assistant `#ai-summary`)
- `docs/screenshots/caseworker-assigned-youth.png` (Caseworker `#assigned-youth`)
