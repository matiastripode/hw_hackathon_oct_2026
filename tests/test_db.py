import sqlite3
import tempfile
import unittest
from pathlib import Path

from handoff.db import DEMO_ENTRIES, initialize_database, open_database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "demo.sqlite3"

    def test_initialization_seeds_demo_only_once(self):
        initialize_database(self.path)
        initialize_database(self.path)
        with open_database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM caregiver").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM entry").fetchone()[0], len(DEMO_ENTRIES))
            self.assertEqual(connection.execute("SELECT count(*) FROM bed").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM room").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM patient").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT count(*) FROM entry WHERE patient_id IS NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_schema_rejects_invalid_entry_type(self):
        initialize_database(self.path)
        with open_database(self.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO entry (type, title, severity, status, author) VALUES (?, ?, ?, ?, ?)",
                    ("invalid", "Example", "low", "open", "Maria"),
                )

    def test_existing_records_are_not_reseeded(self):
        initialize_database(self.path)
        with open_database(self.path) as connection:
            connection.execute("DELETE FROM entry")
        initialize_database(self.path)
        with open_database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM entry").fetchone()[0], 0)

    def test_legacy_database_is_migrated_without_losing_records(self):
        with sqlite3.connect(self.path) as connection:
            connection.executescript("""
                CREATE TABLE caregiver (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE entry (
                    id INTEGER PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL, status TEXT NOT NULL,
                    value TEXT, previous_value TEXT, author TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE handoff (
                    id INTEGER PRIMARY KEY, author TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    acknowledged_by TEXT, acknowledged_at TEXT
                );
                INSERT INTO caregiver (name) VALUES ('Existing caregiver');
                INSERT INTO entry (type, title, severity, status, author)
                    VALUES ('concern', 'Keep this note', 'high', 'open', 'Existing caregiver');
                INSERT INTO handoff (author, note) VALUES ('Existing caregiver', 'Keep this handoff');
            """)
        initialize_database(self.path)
        initialize_database(self.path)
        with open_database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM entry").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM handoff").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT display_name FROM patient WHERE id = (SELECT patient_id FROM entry)").fetchone()[0], "James")
            self.assertEqual(connection.execute("SELECT display_name FROM patient WHERE id = (SELECT patient_id FROM handoff)").fetchone()[0], "James")
            self.assertEqual(connection.execute("SELECT name FROM caregiver").fetchone()[0], "Existing caregiver")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_patient_assignment_is_unique_and_entries_require_a_patient(self):
        initialize_database(self.path)
        with open_database(self.path) as connection:
            bed_id = connection.execute("SELECT bed_id FROM patient WHERE display_name = 'James'").fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO patient (display_name, bed_id) VALUES (?, ?)", ("Duplicate", bed_id))
        with open_database(self.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO entry (type, title, severity, status, author) VALUES (?, ?, ?, ?, ?)",
                    ("task", "No patient", "low", "open", "Maria"),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO handoff (author, note) VALUES (?, ?)", ("Maria", "No patient"))

    def test_existing_bed_ids_and_assignments_survive_room_migration(self):
        with sqlite3.connect(self.path) as connection:
            connection.executescript("""
                CREATE TABLE caregiver (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE bed (
                    id INTEGER PRIMARY KEY, room_label TEXT NOT NULL, bed_label TEXT NOT NULL,
                    UNIQUE (room_label, bed_label)
                );
                CREATE TABLE patient (
                    id INTEGER PRIMARY KEY, display_name TEXT NOT NULL,
                    bed_id INTEGER UNIQUE REFERENCES bed(id), active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE entry (
                    id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patient(id),
                    type TEXT NOT NULL, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL, status TEXT NOT NULL, value TEXT, previous_value TEXT,
                    author TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE handoff (
                    id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patient(id),
                    author TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    acknowledged_by TEXT, acknowledged_at TEXT
                );
                INSERT INTO caregiver (name) VALUES ('Maria');
                INSERT INTO bed (id, room_label, bed_label) VALUES (42, '201', 'C');
                INSERT INTO patient (id, display_name, bed_id) VALUES (5, 'Existing patient', 42);
                INSERT INTO entry (patient_id, type, title, severity, status, author)
                    VALUES (5, 'task', 'Existing note', 'low', 'open', 'Maria');
            """)
        initialize_database(self.path)
        initialize_database(self.path)
        with open_database(self.path) as connection:
            self.assertEqual(connection.execute("SELECT label FROM room").fetchone()[0], "201")
            self.assertEqual(connection.execute("SELECT id FROM bed WHERE room_label = '201'").fetchone()[0], 42)
            self.assertEqual(connection.execute("SELECT bed_id FROM patient WHERE id = 5").fetchone()[0], 42)
            self.assertEqual(connection.execute("SELECT patient_id FROM entry WHERE title = 'Existing note'").fetchone()[0], 5)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO bed (room_label, bed_label) VALUES ('999', 'A')")

    def test_empty_room_remains_after_restart(self):
        initialize_database(self.path)
        with open_database(self.path) as connection:
            connection.execute("INSERT INTO room (label) VALUES ('104')")
        initialize_database(self.path)
        with open_database(self.path) as connection:
            self.assertIsNotNone(connection.execute("SELECT id FROM room WHERE label = '104'").fetchone())
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '104'").fetchone()[0], 0)
