from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from assign_resources_from_intake import AssignmentResult, assign_resources_from_intake
from future_path_ai_intake import QUESTIONS, infer_summary_needs, resolve_profile_link, save_answer
from future_path_ai_intake import ensure_intake_tables as ensure_intake_tables_base


DEFAULT_DB_PATH = Path("database/future_path.db")


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_intake_tables(connection: sqlite3.Connection) -> None:
    ensure_intake_tables_base(connection)


def initialize_state() -> None:
    defaults = {
        "assistant_started": False,
        "assistant_completed": False,
        "assistant_session_id": "",
        "assistant_profile_type": "youth",
        "assistant_youth_id": "",
        "assistant_candidate_id": "",
        "assistant_current_index": 0,
        "assistant_answers": {},
        "assistant_summary_needs": [],
        "assistant_assignment_result": None,
        "assistant_error": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_assistant_state() -> None:
    st.session_state["assistant_started"] = False
    st.session_state["assistant_completed"] = False
    st.session_state["assistant_session_id"] = ""
    st.session_state["assistant_current_index"] = 0
    st.session_state["assistant_answers"] = {}
    st.session_state["assistant_summary_needs"] = []
    st.session_state["assistant_assignment_result"] = None
    st.session_state["assistant_error"] = ""


def render_notice() -> None:
    st.info(
        "Future Path AI Assistant is a decision-support tool that uses synthetic/demo data. "
        "It does not replace emergency services or professional case management."
    )


def profile_exists(connection: sqlite3.Connection, profile_type: str, youth_id: str, candidate_id: str) -> tuple[bool, str]:
    if profile_type == "candidate":
        if not candidate_id.strip():
            return False, "Candidate profile ID is required."
        return True, ""

    if not youth_id.strip():
        return False, "Youth ID is required."
    if not table_exists(connection, "youth_profiles"):
        return False, "youth_profiles table not found in database."

    row = connection.execute("SELECT 1 FROM youth_profiles WHERE youth_id = ?", (youth_id.strip(),)).fetchone()
    if row is None:
        return False, f"Youth ID not found: {youth_id.strip()}"
    return True, ""


def start_intake_session(connection: sqlite3.Connection, session_id: str, profile_type: str, youth_id: str, candidate_id: str) -> None:
    ensure_intake_tables(connection)

    resolved_type, resolved_youth_id, resolved_candidate_id = resolve_profile_link(
        connection,
        youth_id.strip() if profile_type == "youth" else None,
        candidate_id.strip() if profile_type == "candidate" else None,
    )

    connection.execute(
        """
        INSERT INTO intake_sessions (
            intake_session_id,
            youth_id,
            candidate_profile_id,
            profile_type,
            session_status,
            assistant_version,
            channel,
            top_need_category
        ) VALUES (?, ?, ?, ?, 'in_progress', 'future_path_ai_assistant_streamlit_v1', 'streamlit', NULL)
        ON CONFLICT(intake_session_id) DO UPDATE SET
            youth_id = excluded.youth_id,
            candidate_profile_id = excluded.candidate_profile_id,
            profile_type = excluded.profile_type,
            session_status = 'in_progress',
            assistant_version = excluded.assistant_version,
            channel = excluded.channel,
            top_need_category = excluded.top_need_category
        """,
        (session_id, resolved_youth_id, resolved_candidate_id, resolved_type),
    )


def complete_intake_session(connection: sqlite3.Connection, session_id: str, answers: dict[str, str]) -> list[str]:
    summary_needs = infer_summary_needs(answers)
    top_need_category = answers.get("primary_need") or "general_support"
    connection.execute(
        """
        UPDATE intake_sessions
        SET session_status = 'completed',
            completed_at = ?,
            top_need_category = ?
        WHERE intake_session_id = ?
        """,
        (datetime.now(UTC).isoformat(timespec="seconds"), top_need_category, session_id),
    )
    return summary_needs


def render_chat_history() -> None:
    answered = st.session_state["assistant_answers"]
    for question in QUESTIONS:
        key = question["key"]
        if key not in answered:
            break
        with st.chat_message("assistant"):
            st.write(question["prompt"])
        with st.chat_message("user"):
            st.write(answered[key])


def render_progress() -> None:
    current_index = st.session_state["assistant_current_index"]
    total = len(QUESTIONS)
    st.progress(current_index / total)
    st.caption(f"Question {min(current_index + 1, total)} of {total}")


def render_question_input(connection: sqlite3.Connection, db_path: Path) -> None:
    current_index = st.session_state["assistant_current_index"]
    question = QUESTIONS[current_index]
    key = question["key"]

    with st.chat_message("assistant"):
        st.write(question["prompt"])

    choice = st.radio(
        "Choose your answer",
        options=question["options"],
        key=f"assistant_choice_{current_index}",
        horizontal=False,
        label_visibility="collapsed",
    )

    action_col1, action_col2 = st.columns([1, 1])
    with action_col1:
        submit = st.button("Save Answer", type="primary", width="stretch")
    with action_col2:
        stop = st.button("Stop Intake", width="stretch")

    if stop:
        session_id = st.session_state["assistant_session_id"]
        with sqlite3.connect(db_path) as stop_connection:
            stop_connection.execute(
                """
                UPDATE intake_sessions
                SET session_status = 'abandoned',
                    completed_at = ?
                WHERE intake_session_id = ?
                """,
                (datetime.now(UTC).isoformat(timespec="seconds"), session_id),
            )
            stop_connection.commit()
        st.warning("Intake stopped. Session marked as abandoned.")
        reset_assistant_state()
        st.rerun()

    if not submit:
        return

    session_id = st.session_state["assistant_session_id"]
    save_answer(connection, session_id, key, question["prompt"], choice)
    st.session_state["assistant_answers"][key] = choice

    if key == "safety_concern" and choice == "yes":
        st.error(
            "Emergency support warning: This assistant cannot provide crisis intervention. "
            "If there is immediate danger, contact emergency services now. In the U.S., call or text 988."
        )

    next_index = current_index + 1
    if next_index < len(QUESTIONS):
        st.session_state["assistant_current_index"] = next_index
        st.rerun()

    summary_needs = complete_intake_session(connection, session_id, st.session_state["assistant_answers"])
    assignment_result = assign_resources_from_intake(
        connection,
        intake_session_id=session_id,
        top_n=5,
        assigned_by="streamlit_ai_assistant_v1",
    )

    st.session_state["assistant_summary_needs"] = summary_needs
    st.session_state["assistant_assignment_result"] = assignment_result
    st.session_state["assistant_completed"] = True
    st.rerun()


def render_final_summary() -> None:
    st.success("Intake complete. Recommendations generated.")
    session_id = st.session_state["assistant_session_id"]
    st.write(f"Session ID: {session_id}")

    st.subheader("Summary of Needs")
    summary_needs = st.session_state["assistant_summary_needs"]
    if summary_needs:
        for need in summary_needs:
            st.write(f"- {need}")
    else:
        st.write("- General support")

    st.subheader("Recommended Resources")
    result = st.session_state["assistant_assignment_result"]
    if not result:
        st.info("No recommendations were generated.")
    else:
        assignments = result["assignments"]
        if not assignments:
            st.info("No eligible resources matched this intake.")
        else:
            for item in assignments:
                with st.container(border=True):
                    st.write(f"{item['resource_name']} ({item['resource_id']})")
                    st.write(f"Priority: {item['priority_level']} | Score: {item['match_score']:.1f}")
                    st.caption(item["match_reason"])

    if st.button("Start New Intake", width="stretch"):
        reset_assistant_state()
        st.rerun()


def render() -> None:
    st.set_page_config(page_title="Future Path AI Assistant", page_icon="FP", layout="wide")
    initialize_state()

    st.title("Future Path AI Assistant")
    st.caption("Chat-style intake assessment and recommendation support")
    render_notice()

    db_path = Path(st.sidebar.text_input("Database Path", str(DEFAULT_DB_PATH))).expanduser()

    if not db_path.exists():
        st.error(f"Database not found at: {db_path}")
        st.info("Run the data pipeline first, then return to this page.")
        return

    if not st.session_state["assistant_started"]:
        st.subheader("Start Intake")
        st.write("Select whether this intake is for a youth profile or a candidate profile.")

        profile_type = st.radio("Profile Type", options=["youth", "candidate"], horizontal=True)
        youth_id = st.text_input("Youth ID", value=st.session_state["assistant_youth_id"], disabled=profile_type != "youth")
        candidate_id = st.text_input(
            "Candidate Profile ID",
            value=st.session_state["assistant_candidate_id"],
            disabled=profile_type != "candidate",
        )

        start_disabled = False
        with sqlite3.connect(db_path) as connection:
            ok, validation_message = profile_exists(connection, profile_type, youth_id, candidate_id)
            start_disabled = not ok
            if validation_message:
                st.warning(validation_message)

        if st.button("Start Future Path AI Assistant", type="primary", width="stretch", disabled=start_disabled):
            session_id = f"intake-{uuid4()}"
            st.session_state["assistant_session_id"] = session_id
            st.session_state["assistant_profile_type"] = profile_type
            st.session_state["assistant_youth_id"] = youth_id.strip()
            st.session_state["assistant_candidate_id"] = candidate_id.strip()
            st.session_state["assistant_started"] = True
            st.session_state["assistant_completed"] = False
            st.session_state["assistant_current_index"] = 0
            st.session_state["assistant_answers"] = {}
            st.session_state["assistant_summary_needs"] = []
            st.session_state["assistant_assignment_result"] = None

            with sqlite3.connect(db_path) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                start_intake_session(
                    connection,
                    session_id=session_id,
                    profile_type=profile_type,
                    youth_id=youth_id,
                    candidate_id=candidate_id,
                )
                connection.commit()

            st.rerun()
        return

    if st.session_state["assistant_completed"]:
        render_final_summary()
        return

    render_progress()
    render_chat_history()

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        render_question_input(connection, db_path)
        connection.commit()


if __name__ == "__main__":
    render()
