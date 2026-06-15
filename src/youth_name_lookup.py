from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASEWORKER_CSV = PROJECT_ROOT / "data/clean/synthetic_youth_caseworker_data_clean.csv"


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _normalize_name_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    for column in ("youth_id", "first_name", "last_name"):
        if column not in normalized.columns:
            normalized[column] = ""
        normalized[column] = normalized[column].fillna("").astype(str).str.strip()

    normalized["display_name"] = (
        normalized["first_name"].str.strip() + " " + normalized["last_name"].str.strip()
    ).str.strip()
    normalized["display_name"] = normalized["display_name"].replace("", pd.NA).fillna(normalized["youth_id"])
    return normalized[["youth_id", "first_name", "last_name", "display_name"]]


def load_youth_name_map(
    connection: sqlite3.Connection,
    youth_ids: list[str] | None = None,
    csv_path: Path = DEFAULT_CASEWORKER_CSV,
) -> dict[str, str]:
    frames: list[pd.DataFrame] = []
    normalized_ids = [str(youth_id).strip() for youth_id in youth_ids or [] if str(youth_id).strip()]

    if _table_exists(connection, "caseworker_youth"):
        if normalized_ids:
            placeholders = ",".join("?" for _ in normalized_ids)
            db_frame = pd.read_sql_query(
                f"""
                SELECT youth_id, first_name, last_name
                FROM caseworker_youth
                WHERE youth_id IN ({placeholders})
                """,
                connection,
                params=normalized_ids,
            )
        else:
            db_frame = pd.read_sql_query(
                """
                SELECT youth_id, first_name, last_name
                FROM caseworker_youth
                """,
                connection,
            )
        if not db_frame.empty:
            frames.append(_normalize_name_frame(db_frame))

    if csv_path.exists():
        csv_frame = pd.read_csv(csv_path)
        if not csv_frame.empty:
            if normalized_ids:
                csv_frame = csv_frame[csv_frame["youth_id"].astype(str).isin(normalized_ids)]
            if not csv_frame.empty:
                frames.append(_normalize_name_frame(csv_frame))

    if not frames:
        return {}

    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["youth_id"], keep="first")
    return dict(zip(combined["youth_id"].astype(str), combined["display_name"].astype(str)))