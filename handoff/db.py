import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS caregiver (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0)
);

CREATE TABLE IF NOT EXISTS room (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE CHECK (length(trim(label)) > 0)
);

CREATE TABLE IF NOT EXISTS bed (
    id INTEGER PRIMARY KEY,
    room_label TEXT NOT NULL REFERENCES room(label) CHECK (length(trim(room_label)) > 0),
    bed_label TEXT NOT NULL CHECK (length(trim(bed_label)) > 0),
    UNIQUE (room_label, bed_label)
);

CREATE TABLE IF NOT EXISTS patient (
    id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
    bed_id INTEGER UNIQUE REFERENCES bed(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS entry (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patient(id),
    type TEXT NOT NULL CHECK (type IN ('medication', 'symptom', 'task', 'appointment', 'concern')),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    details TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('open', 'done')),
    value TEXT,
    previous_value TEXT,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS handoff (
    id INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patient(id),
    author TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    acknowledged_by TEXT,
    acknowledged_at TEXT
);
"""

DEMO_ENTRIES = (
    ("James", "medication", "Evening medication", "8:00 PM dose recorded as taken.", "low", "done", "Taken", None, "Maria"),
    ("James", "symptom", "Dizziness", "Check in first thing in the morning.", "medium", "open", "Moderate", "Mild", "Maria"),
    ("James", "appointment", "Cardiology appointment", "Transportation needs confirmation.", "medium", "open", None, None, "Maria"),
    ("James", "task", "Prepare appointment notes", "Bring the fictional symptom log.", "low", "open", None, None, "Alex"),
    ("James", "concern", "Unsteady after dinner", "Keep an eye on balance.", "medium", "open", None, None, "Maria"),
    ("Lena", "task", "Check morning supplies", "Review the fictional supply list.", "low", "open", None, None, "Alex"),
)


def database_path():
    return Path(os.environ.get("HANDOFF_DB_PATH", Path(__file__).resolve().parent.parent / "data" / "handoff.sqlite3")).expanduser()


@contextmanager
def open_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    finally:
        connection.close()


def create_schema(connection):
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(SCHEMA)
    connection.execute("INSERT OR IGNORE INTO room (label) SELECT DISTINCT room_label FROM bed")
    for action in ("INSERT", "UPDATE OF room_label"):
        connection.execute(
            f"""CREATE TRIGGER IF NOT EXISTS require_bed_room_on_{action.split()[0].lower()}
                BEFORE {action} ON bed
                WHEN NOT EXISTS (SELECT 1 FROM room WHERE label = NEW.room_label)
                BEGIN SELECT RAISE(ABORT, 'room required'); END"""
        )
    for table in ("entry", "handoff"):
        columns = {column["name"] for column in connection.execute(f"PRAGMA table_info({table})")}
        if "patient_id" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN patient_id INTEGER REFERENCES patient(id)")


def seed_patients(connection):
    connection.executemany("INSERT OR IGNORE INTO room (label) VALUES (?)", (("101",), ("102",)))
    connection.executemany(
        "INSERT OR IGNORE INTO bed (room_label, bed_label) VALUES (?, ?)",
        (("101", "A"), ("101", "B"), ("102", "A")),
    )
    for name, room, bed in (("James", "101", "A"), ("Lena", "101", "B")):
        bed_id = connection.execute(
            "SELECT id FROM bed WHERE room_label = ? AND bed_label = ?", (room, bed)
        ).fetchone()["id"]
        connection.execute("INSERT INTO patient (display_name, bed_id) VALUES (?, ?)", (name, bed_id))


def enforce_patient_references(connection):
    for table in ("entry", "handoff"):
        for action in ("INSERT", "UPDATE"):
            connection.execute(
                f"""CREATE TRIGGER IF NOT EXISTS require_{table}_patient_on_{action.lower()}
                    BEFORE {action} ON {table}
                    WHEN NEW.patient_id IS NULL
                    BEGIN SELECT RAISE(ABORT, 'patient required'); END"""
            )


def seed_demo_data(connection):
    connection.executemany("INSERT INTO caregiver (name) VALUES (?)", (("Maria",), ("Alex",)))
    seed_patients(connection)
    patients = {row["display_name"]: row["id"] for row in connection.execute("SELECT id, display_name FROM patient")}
    connection.executemany(
        """INSERT INTO entry (patient_id, type, title, details, severity, status, value, previous_value, author)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ((patients[entry[0]], *entry[1:]) for entry in DEMO_ENTRIES),
    )


def initialize_database(path):
    with open_database(path) as connection:
        create_schema(connection)
        if not any(
            connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            for table in ("caregiver", "entry", "handoff", "patient")
        ):
            seed_demo_data(connection)
        else:
            if not connection.execute("SELECT 1 FROM patient LIMIT 1").fetchone():
                seed_patients(connection)
            for table in ("entry", "handoff"):
                if connection.execute(f"SELECT 1 FROM {table} WHERE patient_id IS NULL LIMIT 1").fetchone():
                    james_id = connection.execute("SELECT id FROM patient WHERE display_name = 'James'").fetchone()["id"]
                    connection.execute(f"UPDATE {table} SET patient_id = ? WHERE patient_id IS NULL", (james_id,))
        enforce_patient_references(connection)


def reset_demo_database(path):
    with open_database(path) as connection:
        create_schema(connection)
        for table in ("handoff", "entry", "patient", "bed", "room", "caregiver"):
            connection.execute(f"DELETE FROM {table}")
        seed_demo_data(connection)
        enforce_patient_references(connection)
