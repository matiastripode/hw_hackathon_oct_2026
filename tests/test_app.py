import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from handoff.app import app
from handoff.db import database_path, open_database


class AppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"HANDOFF_DB_PATH": str(Path(directory.name) / "demo.sqlite3")})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_first_visit_shows_login_instead_of_dashboard(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        login = self.client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("Demo login — no real authentication", login.text)
        self.assertIn("Maria", login.text)
        self.assertIn("Alex", login.text)
        self.assertNotIn("Dizziness", login.text)
        self.assertNotIn("Or enter a display name", login.text)
        self.assertIn('minlength="6"', login.text)
        self.assertIn("/static/login.js", login.text)
        self.assertIn("Prototype · fictional data only · not medical advice", login.text)

    def test_seeded_caregiver_can_sign_in_and_see_dashboard(self):
        response = self.client.post("/login", data={"caregiver": "Maria", "password": "anything"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        roster = self.client.get("/")
        self.assertIn("Room 101", roster.text)
        self.assertIn("James", roster.text)
        self.assertIn("Empty bed", roster.text)
        dashboard = self.client.get("/patients/1")
        self.assertIn("Signed in as Maria", dashboard.text)
        self.assertIn("<h3>Dizziness</h3>", dashboard.text)
        self.assertIn("Evening medication", dashboard.text)
        self.assertNotIn("anything", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/login", follow_redirects=False).headers["location"], "/")

    def test_custom_caregiver_name_cannot_be_created_at_login(self):
        response = self.client.post(
            "/login",
            data={"caregiver": "Maria", "display_name": "Sam <Caregiver>", "password": "ignored"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("Signed in as Maria", self.client.get("/patients/1").text)
        with open_database(database_path()) as connection:
            self.assertIsNone(connection.execute("SELECT name FROM caregiver WHERE name = ?", ("Sam <Caregiver>",)).fetchone())

    def test_existing_custom_caregiver_remains_selectable(self):
        with open_database(database_path()) as connection:
            connection.execute("INSERT INTO caregiver (name) VALUES (?)", ("Sam <Caregiver>",))
        self.assertIn("Sam &lt;Caregiver&gt;", self.client.get("/login").text)
        self.client.post("/login", data={"caregiver": "Sam <Caregiver>", "password": "sixchars"})
        self.assertIn("Signed in as Sam &lt;Caregiver&gt;", self.client.get("/patients/1").text)

    def test_sign_out_allows_switching_caregivers(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        response = self.client.post("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/login")
        self.client.post("/login", data={"caregiver": "Alex", "password": "sixchars"})
        self.assertIn("Signed in as Alex", self.client.get("/patients/1").text)

    def test_caregivers_on_separate_devices_have_independent_sessions(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with TestClient(app) as second_device:
            self.assertEqual(second_device.get("/", follow_redirects=False).headers["location"], "/login")
            second_device.post("/login", data={"caregiver": "Alex", "password": "sixchars"})
            self.assertIn("Signed in as Maria", self.client.get("/patients/1").text)
            self.assertIn("Signed in as Alex", second_device.get("/patients/1").text)
            self.client.post("/logout")
            self.assertIn("Signed in as Alex", second_device.get("/patients/1").text)

    def test_missing_or_short_password_and_unknown_caregiver_are_rejected(self):
        for data in (
            {"caregiver": "Maria"},
            {"caregiver": "Maria", "password": ""},
            {"caregiver": "Maria", "password": "12345"},
            {"caregiver": "Unknown", "password": "sixchars"},
            {"password": "sixchars"},
        ):
            with self.subTest(data=data):
                response = self.client.post("/login", data=data)
                self.assertEqual(response.status_code, 400)
                self.assertIn("Please choose a caregiver and enter a demo password of at least 6 characters", response.text)
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/login")

    def test_six_character_password_is_accepted(self):
        response = self.client.post("/login", data={"caregiver": "Alex", "password": "123456"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("Signed in as Alex", self.client.get("/patients/1").text)

    def test_entries_are_isolated_by_patient(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.assertIn("<h3>Dizziness</h3>", self.client.get("/patients/1").text)
        other_board = self.client.get("/patients/2")
        self.assertEqual(other_board.status_code, 200)
        self.assertIn("Lena", other_board.text)
        self.assertNotIn("<h3>Dizziness</h3>", other_board.text)
        self.assertEqual(self.client.get("/patients/999").status_code, 404)

    def test_patient_creation_and_bed_reassignment(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        created = self.client.post("/patients", data={"display_name": "Nora"}, follow_redirects=False)
        self.assertEqual(created.status_code, 303)
        board_path = created.headers["location"]
        self.assertIn("Nora", self.client.get(board_path).text)
        self.assertIn("Unassigned", self.client.get(board_path).text)
        with open_database(database_path()) as connection:
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102' AND bed_label = 'A'").fetchone()[0]
        assigned = self.client.post(f"{board_path}/bed", data={"bed_id": str(empty_bed)}, follow_redirects=False)
        self.assertEqual(assigned.status_code, 303)
        self.assertIn("Room 102 · Bed A", self.client.get(board_path).text)
        self.assertIn("Nora", self.client.get("/").text)
        self.client.post(f"{board_path}/bed", data={"bed_id": ""})
        self.assertIn("Unassigned", self.client.get(board_path).text)

    def test_moving_patient_keeps_their_care_entries(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102' AND bed_label = 'A'").fetchone()[0]
            entry_count = connection.execute("SELECT count(*) FROM entry WHERE patient_id = 1").fetchone()[0]
        self.client.post("/patients/1/bed", data={"bed_id": str(empty_bed)})
        board = self.client.get("/patients/1").text
        self.assertIn("Room 102 · Bed A", board)
        self.assertIn("<h3>Dizziness</h3>", board)
        self.assertNotIn("<h3>Dizziness</h3>", self.client.get("/patients/2").text)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM entry WHERE patient_id = 1").fetchone()[0], entry_count)

    def test_occupied_and_invalid_beds_are_rejected(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            occupied = connection.execute("SELECT bed_id FROM patient WHERE display_name = 'James'").fetchone()[0]
        conflict = self.client.post("/patients/2/bed", data={"bed_id": str(occupied)})
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("already occupied", conflict.text)
        self.assertEqual(self.client.post("/patients", data={"display_name": "New", "bed_id": str(occupied)}).status_code, 409)
        self.assertEqual(self.client.post("/patients", data={"display_name": " ", "bed_id": ""}).status_code, 400)
        self.assertEqual(self.client.post("/patients/2/bed", data={"bed_id": "999"}).status_code, 400)
        self.assertEqual(self.client.post("/patients/999/bed", data={"bed_id": ""}).status_code, 404)
        self.assertNotIn("Room 101 · Bed A", self.client.get("/patients/2").text)

    def test_patient_routes_require_login(self):
        self.assertEqual(self.client.get("/patients/1", follow_redirects=False).headers["location"], "/login")
        self.assertEqual(self.client.post("/patients", data={"display_name": "Nora"}, follow_redirects=False).headers["location"], "/login")
        self.assertEqual(self.client.post("/patients/1/bed", data={"bed_id": ""}, follow_redirects=False).headers["location"], "/login")

    def test_create_empty_room_then_add_bed(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        room = self.client.post("/rooms", data={"label": " 103 "}, follow_redirects=False)
        self.assertEqual(room.status_code, 303)
        self.assertEqual(room.headers["location"], "/")
        roster = self.client.get("/").text
        self.assertIn("Room 103", roster)
        self.assertIn("No beds yet", roster)
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '103'").fetchone()[0]
        bed = self.client.post("/beds", data={"room_id": str(room_id), "bed_label": "B"}, follow_redirects=False)
        self.assertEqual(bed.status_code, 303)
        self.assertIn("Room 103 · Bed B", self.client.get("/").text)
        with open_database(database_path()) as connection:
            bed_id = connection.execute("SELECT id FROM bed WHERE room_label = '103' AND bed_label = 'B'").fetchone()[0]
        self.client.post("/patients", data={"display_name": "Nora", "bed_id": str(bed_id)})
        self.assertIn("Room 103 · Bed B", self.client.get("/patients/3").text)

    def test_room_highlights_reflect_full_partial_free_and_unconfigured_states(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        roster = self.client.get("/").text
        self.assertRegex(roster, r'room-group room-occupied">\s*<div class="room-heading">\s*<h3>Room 101</h3>\s*<span class="room-status">Occupied · 2/2 beds</span>')
        self.assertRegex(roster, r'room-group room-free">\s*<div class="room-heading">\s*<h3>Room 102</h3>\s*<span class="room-status">Free · 0/1 beds</span>')
        self.client.post("/rooms", data={"label": "103"})
        roster = self.client.get("/").text
        self.assertRegex(roster, r'room-group room-no-beds">\s*<div class="room-heading">\s*<h3>Room 103</h3>\s*<span class="room-status">No beds</span>')
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '101'").fetchone()[0]
        self.client.post("/beds", data={"room_id": str(room_id), "bed_label": "C"})
        roster = self.client.get("/").text
        self.assertRegex(roster, r'room-group room-partial">\s*<div class="room-heading">\s*<h3>Room 101</h3>\s*<span class="room-status">Partially occupied · 2/3 beds</span>')

    def test_duplicate_or_invalid_rooms_and_beds_are_rejected(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.assertEqual(self.client.post("/rooms", data={"label": "101"}).status_code, 409)
        for label in ("", "   ", "x" * 41):
            with self.subTest(label=label):
                self.assertEqual(self.client.post("/rooms", data={"label": label}).status_code, 400)
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '101'").fetchone()[0]
        self.assertEqual(self.client.post("/beds", data={"room_id": str(room_id), "bed_label": "A"}).status_code, 409)
        for room, bed in (("", "C"), ("999", "C"), ("invalid", "C"), (str(room_id), " "), (str(room_id), "x" * 21)):
            with self.subTest(room=room, bed=bed):
                self.assertEqual(self.client.post("/beds", data={"room_id": room, "bed_label": bed}).status_code, 400)

    def test_room_and_bed_creation_requires_login(self):
        self.assertEqual(self.client.post("/rooms", data={"label": "103"}, follow_redirects=False).headers["location"], "/login")
        self.assertEqual(self.client.post("/beds", data={"room_id": "1", "bed_label": "C"}, follow_redirects=False).headers["location"], "/login")

    def test_log_symptom_and_task_for_selected_patient(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        board = self.client.get("/patients/1").text
        self.assertIn("Log symptom", board)
        self.assertIn("Add task", board)
        for entry_type, title in (("symptom", "New dizziness"), ("task", "Bring water")):
            with self.subTest(entry_type=entry_type):
                response = self.client.post(
                    "/patients/1/entries",
                    data={"type": entry_type, "title": title, "details": "Fictional update", "severity": "medium", "status": "open", "author": "Alex"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/patients/1")
                with open_database(database_path()) as connection:
                    entry = connection.execute("SELECT patient_id, author, type FROM entry WHERE title = ?", (title,)).fetchone()
                    self.assertEqual(tuple(entry), (1, "Maria", entry_type))
                self.assertIn(title, self.client.get("/patients/1").text)
                self.assertNotIn(title, self.client.get("/patients/2").text)

    def test_quick_titles_are_available_for_both_log_forms(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        board = self.client.get("/patients/1").text
        self.assertIn('id="task-preset"', board)
        self.assertIn('value="Confirm transportation"', board)
        self.assertIn('value="Prepare appointment notes"', board)
        self.assertIn('id="symptom-preset"', board)
        self.assertIn('value="Dizziness"', board)
        self.assertIn('value="Poor sleep"', board)
        self.assertIn("/static/entry-presets.js", board)
        self.assertEqual(self.client.get("/static/entry-presets.js").status_code, 200)

    def test_only_author_can_edit_symptoms_and_tasks(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.client.post(
            "/patients/1/entries",
            data={"type": "task", "title": "Original task", "details": "Before", "severity": "low", "status": "open"},
        )
        with open_database(database_path()) as connection:
            entry_id = connection.execute("SELECT id FROM entry WHERE title = 'Original task'").fetchone()[0]
        edit_path = f"/patients/1/entries/{entry_id}"
        self.assertIn(f'action="{edit_path}"', self.client.get("/patients/1").text)
        self.client.post("/logout")
        self.client.post("/login", data={"caregiver": "Alex", "password": "sixchars"})
        self.assertNotIn(f'action="{edit_path}"', self.client.get("/patients/1").text)
        data = {"title": "Changed task", "details": "After", "severity": "high", "status": "done"}
        self.assertEqual(self.client.post(edit_path, data=data).status_code, 403)
        self.assertEqual(self.client.post(f"/patients/2/entries/{entry_id}", data=data).status_code, 404)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT title FROM entry WHERE id = ?", (entry_id,)).fetchone()[0], "Original task")
        self.client.post("/logout")
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.assertEqual(self.client.post(edit_path, data=data, follow_redirects=False).status_code, 303)
        with open_database(database_path()) as connection:
            self.assertEqual(tuple(connection.execute("SELECT title, details, severity, status FROM entry WHERE id = ?", (entry_id,)).fetchone()), ("Changed task", "After", "high", "done"))

    def test_invalid_edit_does_not_change_entry(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            symptom_id = connection.execute("SELECT id FROM entry WHERE type = 'symptom' AND patient_id = 1").fetchone()[0]
        response = self.client.post(
            f"/patients/1/entries/{symptom_id}",
            data={"title": "New title", "details": "Update", "severity": "critical", "status": "open"},
        )
        self.assertEqual(response.status_code, 400)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT title FROM entry WHERE id = ?", (symptom_id,)).fetchone()[0], "Dizziness")

    def test_noneditable_entry_types_and_invalid_log_data_are_rejected(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            medication_id = connection.execute("SELECT id FROM entry WHERE type = 'medication' AND patient_id = 1").fetchone()[0]
        data = {"title": "Changed", "details": "", "severity": "low", "status": "open"}
        self.assertEqual(self.client.post(f"/patients/1/entries/{medication_id}", data=data).status_code, 403)
        for fields in (
            {"type": "medication", "title": "No"},
            {"type": "task", "title": "  "},
            {"type": "symptom", "title": "No", "severity": "critical"},
            {"type": "task", "title": "No", "status": "archived"},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.client.post("/patients/1/entries", data=fields).status_code, 400)
        self.client.post("/logout")
        self.assertEqual(self.client.post("/patients/1/entries", data={"type": "task", "title": "No"}, follow_redirects=False).headers["location"], "/login")

    def test_tampered_session_does_not_open_dashboard(self):
        self.client.cookies.set("session", "tampered")
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/login")

    def test_health_and_local_assets_work_without_login(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 404)

    def test_brand_mark_and_favicon_are_local_on_every_page(self):
        pages = [self.client.get("/login").text]
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        pages.extend((self.client.get("/").text, self.client.get("/patients/1").text))
        for page in pages:
            with self.subTest(page=page.split("<title>")[1].split("</title>")[0]):
                self.assertIn('href="http://testserver/static/logo.svg"', page)
                self.assertIn('src="http://testserver/static/logo.svg"', page)
                self.assertIn('class="brand"', page)
                self.assertIn('<span class="brand-name">Caregiver<strong>Handoff Board</strong></span>', page)
        logo = self.client.get("/static/logo.svg")
        self.assertEqual(logo.status_code, 200)
        self.assertIn("image/svg+xml", logo.headers["content-type"])
        svg = ElementTree.fromstring(logo.content)
        self.assertEqual(svg.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(svg.findall("{http://www.w3.org/2000/svg}rect"), [])
        self.assertIsNotNone(svg.find("{http://www.w3.org/2000/svg}path[@id='heart-shape']"))
