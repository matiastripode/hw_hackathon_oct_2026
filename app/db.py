"""SQLite storage and immutable shift snapshots for the demo board."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


TYPES = ("medication", "symptom", "task", "appointment", "concern")
SEVERITIES = ("low", "medium", "high")
STATUSES = ("open", "done")
TYPE_LABELS = {
    "concern": "Concerns",
    "medication": "Medications",
    "symptom": "Symptoms",
    "appointment": "Appointments",
    "task": "Tasks",
}
DEFAULT_DB = Path(os.environ.get("HANDOFF_DB", "data/handoff.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 0,
    baseline_event_id INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO meta (id) VALUES (1);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL CHECK (type IN ('medication','symptom','task','appointment','concern')),
    title TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    status TEXT NOT NULL CHECK (status IN ('open','done')),
    value TEXT NOT NULL DEFAULT '',
    previous_value TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entry_events (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    action TEXT NOT NULL,
    before_value TEXT NOT NULL DEFAULT '',
    snapshot TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entry_events_entry_id ON entry_events(entry_id);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY,
    author TEXT NOT NULL,
    note TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    acknowledged_by TEXT,
    acknowledged_at TEXT,
    covered_event_id INTEGER NOT NULL,
    snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS handoffs_acknowledged_at ON handoffs(acknowledged_at);
"""


class BoardError(Exception):
    """A user-facing validation or workflow error."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def database(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _revision(conn):
    conn.execute("UPDATE meta SET revision = revision + 1 WHERE id = 1")


def _event(conn, entry, action, before_value=""):
    conn.execute(
        """INSERT INTO entry_events (entry_id, action, before_value, snapshot, occurred_at)
           VALUES (?, ?, ?, ?, ?)""",
        (entry["id"], action, before_value, json.dumps(dict(entry)), entry["updated_at"]),
    )


def _check_choice(value, choices, field):
    if value not in choices:
        raise BoardError(f"Choose a valid {field}.")
    return value


def _text(value, field, limit, required=False):
    value = value.strip()
    if required and not value:
        raise BoardError(f"{field} is required.")
    if len(value) > limit:
        raise BoardError(f"{field} must be {limit} characters or fewer.")
    return value


def initialize(path=DEFAULT_DB):
    with database(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]:
            return
        _seed(conn)


def _seed(conn):
    examples = (
        ("medication", "8:00 PM medication", "Evening dose due; record when taken.", "medium", "", "open"),
        ("symptom", "Dizziness", "Mild after lunch; keep an eye on it.", "medium", "mild", "open"),
        ("appointment", "Cardiology appointment tomorrow", "Confirm transportation.", "medium", "", "open"),
    )
    for kind, title, details, severity, value, status in examples:
        now = utc_now()
        cursor = conn.execute(
            """INSERT INTO entries
               (type, title, details, severity, status, value, author, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'Alex', ?, ?)""",
            (kind, title, details, severity, status, value, now, now),
        )
        _event(conn, conn.execute("SELECT * FROM entries WHERE id = ?", (cursor.lastrowid,)).fetchone(), "created")
    # Seed rows represent the starting state, not changes made during the demo.
    conn.execute("UPDATE meta SET baseline_event_id = (SELECT MAX(id) FROM entry_events) WHERE id = 1")
    _revision(conn)


def reset_demo(path=DEFAULT_DB):
    """Replace only this board's fictional entries and handoffs."""
    with database(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM handoffs")
        conn.execute("DELETE FROM entry_events")
        conn.execute("DELETE FROM entries")
        conn.execute("UPDATE meta SET baseline_event_id = 0 WHERE id = 1")
        _seed(conn)


def create_entry(path, *, kind, title, details, severity, status, value, author):
    kind = _check_choice(kind, TYPES, "type")
    severity = _check_choice(severity, SEVERITIES, "severity")
    status = _check_choice(status, STATUSES, "status")
    title = _text(title, "Title", 100, required=True)
    details = _text(details, "Details", 600)
    value = _text(value, "Value", 100)
    with database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = utc_now()
        cursor = conn.execute(
            """INSERT INTO entries
               (type, title, details, severity, status, value, author, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, title, details, severity, status, value, author, now, now),
        )
        _event(conn, conn.execute("SELECT * FROM entries WHERE id = ?", (cursor.lastrowid,)).fetchone(), "created")
        _revision(conn)
        return cursor.lastrowid


def update_entry(path, entry_id, *, details, severity, status, value, author):
    severity = _check_choice(severity, SEVERITIES, "severity")
    status = _check_choice(status, STATUSES, "status")
    details = _text(details, "Details", 600)
    value = _text(value, "Value", 100)
    with database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if old is None:
            raise BoardError("That item no longer exists.")
        if all(old[key] == new for key, new in (
            ("details", details), ("severity", severity), ("status", status), ("value", value)
        )):
            return False
        previous_value = old["value"] if value != old["value"] else old["previous_value"]
        conn.execute(
            """UPDATE entries SET details=?, severity=?, status=?, value=?, previous_value=?,
               author=?, updated_at=? WHERE id=?""",
            (details, severity, status, value, previous_value, author, utc_now(), entry_id),
        )
        current = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        _event(conn, current, "updated", old["value"])
        _revision(conn)
        return True


def _snapshot(conn, baseline, covered):
    events = conn.execute(
        "SELECT * FROM entry_events WHERE id > ? AND id <= ? ORDER BY id",
        (baseline, covered),
    ).fetchall()
    changes = {}
    for event in events:
        entry = json.loads(event["snapshot"])
        key = event["entry_id"]
        if key not in changes:
            changes[key] = {
                "before_value": event["before_value"] if event["action"] != "created" else "",
                "created": event["action"] == "created",
            }
        changes[key]["entry"] = entry

    completed, changed = [], []
    for change in changes.values():
        entry = change["entry"]
        entry["from_value"] = change["before_value"] if not change["created"] else ""
        (completed if entry["status"] == "done" else changed).append(entry)

    attention = [
        dict(row) for row in conn.execute(
            """SELECT * FROM entries WHERE status = 'open'
               ORDER BY CASE WHEN type = 'concern' THEN 0 ELSE 1 END,
                        CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        updated_at DESC"""
        )
    ]
    return {"completed": completed, "changed": changed, "attention": attention}


def submit_handoff(path, *, author, note):
    note = _text(note, "Handoff note", 1000, required=True)
    with database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM handoffs WHERE acknowledged_at IS NULL LIMIT 1").fetchone():
            raise BoardError("A handoff is already awaiting acknowledgement.")
        last = conn.execute(
            "SELECT covered_event_id FROM handoffs WHERE acknowledged_at IS NOT NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        baseline = last["covered_event_id"] if last else conn.execute(
            "SELECT baseline_event_id FROM meta WHERE id = 1"
        ).fetchone()[0]
        covered = conn.execute("SELECT COALESCE(MAX(id), 0) FROM entry_events").fetchone()[0]
        snapshot = _snapshot(conn, baseline, covered)
        cursor = conn.execute(
            """INSERT INTO handoffs (author, note, submitted_at, covered_event_id, snapshot)
               VALUES (?, ?, ?, ?, ?)""",
            (author, note, utc_now(), covered, json.dumps(snapshot)),
        )
        _revision(conn)
        return cursor.lastrowid


def acknowledge_handoff(path, handoff_id, *, author):
    with database(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        latest = conn.execute("SELECT * FROM handoffs ORDER BY id DESC LIMIT 1").fetchone()
        if latest is None or latest["id"] != handoff_id or latest["acknowledged_at"]:
            raise BoardError("This handoff is no longer awaiting acknowledgement.")
        if latest["author"] == author:
            raise BoardError("Another caregiver needs to acknowledge this handoff.")
        conn.execute(
            "UPDATE handoffs SET acknowledged_by = ?, acknowledged_at = ? WHERE id = ?",
            (author, utc_now(), handoff_id),
        )
        _revision(conn)


def get_revision(path):
    with database(path) as conn:
        return conn.execute("SELECT revision FROM meta WHERE id = 1").fetchone()[0]


def get_state(path):
    with database(path) as conn:
        entries = [dict(row) for row in conn.execute(
            """SELECT * FROM entries ORDER BY
               CASE WHEN type = 'concern' AND status = 'open' THEN 0 ELSE 1 END,
               CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
               updated_at DESC"""
        )]
        latest = conn.execute("SELECT * FROM handoffs ORDER BY id DESC LIMIT 1").fetchone()
        handoff = dict(latest) if latest else None
        if handoff:
            handoff["snapshot"] = json.loads(handoff["snapshot"])
        groups = [
            {"name": name, "type": kind, "items": [entry for entry in entries
                                                     if entry["type"] == kind and entry["status"] == "open"]}
            for kind, name in TYPE_LABELS.items()
        ]
        return {
            "groups": [group for group in groups if group["items"]],
            "done": [entry for entry in entries if entry["status"] == "done"],
            "open_count": sum(entry["status"] == "open" for entry in entries),
            "concern_count": sum(entry["type"] == "concern" and entry["status"] == "open" for entry in entries),
            "handoff": handoff,
            "revision": conn.execute("SELECT revision FROM meta WHERE id = 1").fetchone()[0],
        }
