"""
history_store.py

Persistent history of past RFP analyses, stored in a local SQLite file
(data/history.db). Nothing here talks to Streamlit — it's a plain data
layer, matching the same separation-of-concerns pattern as
decision_rules.py / knowledge_base.py, so it can be tested or reused
(e.g. from api.py) independent of the UI.

WHY SQLITE: no server to run, no extra dependency (sqlite3 is in Python's
standard library), and a single file is easy to back up or move. This is
the right amount of infrastructure for "one analyst's local history" —
a full database server would be solving a problem this app doesn't have.

The full analysis result (everything shown across the app's tabs) is stored
as one JSON blob per row, alongside a handful of pulled-out columns
(verdict tag, score, deadline, etc.) so the history list can be displayed
and sorted without re-parsing JSON for every row.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "history.db")


def _ensure_db_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def _connect():
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us access columns by name, e.g. row["score"]
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Creates the history table if it doesn't exist yet. Safe to call on
    every app startup — CREATE TABLE IF NOT EXISTS is a no-op once the
    table already exists."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rfp_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                rfp_identifier TEXT,
                source_label TEXT,
                source_docs_json TEXT,
                verdict_tag TEXT,
                verdict_score INTEGER,
                submission_deadline TEXT,
                contract_value_usd REAL,
                analysis_json TEXT NOT NULL,
                generated_responses_json TEXT,
                status_overrides_json TEXT
            )
            """
        )


def save_analysis(analysis: dict, source_label: str, source_docs: list, generated_responses: list = None, status_overrides: dict = None) -> int:
    """Saves one completed analysis as a new history row. Returns the new
    row's id (used later if you want to jump straight back to it).

    Pulls out a few fields (tag, score, deadline, contract value) into their
    own columns purely so the history list view can sort/filter without
    parsing analysis_json for every row — the full analysis is still kept
    intact in analysis_json so "load this one back" restores everything
    exactly as it was, not just the summary fields.
    """
    v = analysis.get("verdict", {}) or {}
    kdb = analysis.get("keyDatesBudget", {}) or {}

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO rfp_history (
                created_at, rfp_identifier, source_label, source_docs_json,
                verdict_tag, verdict_score, submission_deadline, contract_value_usd,
                analysis_json, generated_responses_json, status_overrides_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                analysis.get("rfpIdentifier"),
                source_label,
                json.dumps(source_docs or []),
                v.get("tag"),
                v.get("score"),
                kdb.get("submissionDeadline"),
                kdb.get("contractValueUSD"),
                json.dumps(analysis),
                json.dumps(generated_responses or []),
                json.dumps(status_overrides or {}),
            ),
        )
        return cur.lastrowid


def list_history(limit: int = 200) -> list:
    """Returns summary rows (NOT the full analysis_json — that would be
    wasteful to load for every row just to show a table), newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, rfp_identifier, source_label,
                   verdict_tag, verdict_score, submission_deadline, contract_value_usd
            FROM rfp_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_history_entry(history_id: int) -> dict:
    """Returns the FULL stored record for one history row, including the
    complete analysis dict and generated responses — used when the user
    clicks "Load" on a past entry to restore it into the current session."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rfp_history WHERE id = ?", (history_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["analysis"] = json.loads(record.pop("analysis_json"))
        record["source_docs"] = json.loads(record.pop("source_docs_json") or "[]")
        record["generated_responses"] = json.loads(record.pop("generated_responses_json") or "[]")
        record["status_overrides"] = json.loads(record.pop("status_overrides_json") or "{}")
        return record


def update_generated_responses(history_id: int, generated_responses: list):
    """Called after AI Response Generation runs for an already-saved
    analysis, so reloading that history entry later brings the drafted
    answers back too, not just the original verdict/checklist."""
    with _connect() as conn:
        conn.execute(
            "UPDATE rfp_history SET generated_responses_json = ? WHERE id = ?",
            (json.dumps(generated_responses or []), history_id),
        )


def update_status_overrides(history_id: int, status_overrides: dict):
    """Called whenever the user applies a manual GO/NO-GO/REVIEW correction
    on an already-saved analysis, so those human corrections survive a
    reload instead of reverting to the AI's original checklist output."""
    with _connect() as conn:
        conn.execute(
            "UPDATE rfp_history SET status_overrides_json = ? WHERE id = ?",
            (json.dumps(status_overrides or {}), history_id),
        )


def delete_history_entry(history_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM rfp_history WHERE id = ?", (history_id,))
