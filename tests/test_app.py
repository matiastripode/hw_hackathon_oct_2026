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

    def test_landing_page_keeps_main_brand_and_login_contract(self):
        login = self.client.get("/login").text
        self.assertIn("Nothing important", login)
        self.assertIn("gets lost", login)
        self.assertIn('class="landing-wrap landing-layout"', login)
        self.assertIn('src="http://testserver/static/logo.svg"', login)
        self.assertNotIn("SHIFT <em>NOTES</em>", login)
        self.assertIn('<option value="Maria"', login)
        self.assertIn('name="password" type="password" minlength="6" required', login)
        self.assertIn("Choose a caregiver", login)
        self.assertIn("Start your shift.", login)

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

    def test_two_dropdowns_assign_patient_from_roster_without_opening_board(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.client.post("/patients", data={"display_name": "Nora"})
        with open_database(database_path()) as connection:
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102'").fetchone()[0]
        roster = self.client.get("/").text
        self.assertIn('action="/roster/assign-bed"', roster)
        self.assertEqual(roster.count('action="/roster/assign-bed"'), 1)
        self.assertIn('id="assign-patient" name="patient_id"', roster)
        self.assertIn('<option value="3" >Nora · #3</option>', roster)
        self.assertIn('id="assign-bed" name="bed_id"', roster)
        self.assertIn(f'<option value="{empty_bed}">Room 102 · Bed A</option>', roster)
        self.assertNotIn('id="assign-bed" name="bed_id" required disabled', roster)
        self.assertNotIn('id="assign-bed-3"', roster)
        assigned = self.client.post(
            "/roster/assign-bed",
            data={"patient_id": "3", "bed_id": str(empty_bed), "room_page": "1", "patient_page": "1"},
            follow_redirects=False,
        )
        self.assertEqual(assigned.status_code, 303)
        self.assertEqual(assigned.headers["location"], "/?room_page=1&patient_page=1")
        updated = self.client.get(assigned.headers["location"]).text
        self.assertIn("<h4>Nora</h4>", updated)
        self.assertNotIn('action="/roster/assign-bed"', updated)
        self.assertIn("Room 102 · Bed A", self.client.get("/patients/3").text)

    def test_two_dropdown_assignment_rejects_invalid_choices_and_taken_beds(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        self.client.post("/patients", data={"display_name": "Nora"})
        for patient_id, bed_id, status, error in (
            ("", "3", 400, "Choose an unassigned patient."),
            ("invalid", "3", 400, "Choose an unassigned patient."),
            ("999", "3", 400, "Choose an unassigned patient."),
            ("1", "3", 400, "Choose an unassigned patient."),
            ("3", "", 400, "Choose an available bed."),
            ("3", "invalid", 400, "Choose an available bed."),
            ("3", "999", 400, "Choose an available bed."),
            ("3", "1", 409, "That bed is already occupied."),
        ):
            with self.subTest(patient_id=patient_id, bed_id=bed_id):
                response = self.client.post(
                    "/roster/assign-bed", data={"patient_id": patient_id, "bed_id": bed_id}
                )
                self.assertEqual(response.status_code, status)
                self.assertIn(error, response.text)
                self.assertIn('action="/roster/assign-bed"', response.text)
                self.assertNotIn('<h4>Nora</h4>', response.text)

    def test_two_dropdowns_include_all_patients_and_preserve_roster_pagination(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            connection.executemany("INSERT INTO room (label) VALUES (?)", [(str(n),) for n in range(103, 106)])
            connection.executemany("INSERT INTO patient (display_name) VALUES (?)", [(f"Demo {n}",) for n in range(4)])
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102'").fetchone()[0]
        second_page = self.client.get("/?room_page=2&patient_page=2").text
        self.assertIn('action="/roster/assign-bed"', second_page)
        self.assertIn('<option value="3" >Demo 0 · #3</option>', second_page)
        self.assertIn('<option value="6" >Demo 3 · #6</option>', second_page)
        self.assertNotIn('<h3>Demo 0</h3>', second_page)
        self.assertIn('name="room_page" value="2"', second_page)
        self.assertIn('name="patient_page" value="2"', second_page)
        assigned = self.client.post(
            "/roster/assign-bed",
            data={"patient_id": "6", "bed_id": str(empty_bed), "room_page": "2", "patient_page": "2"},
            follow_redirects=False,
        )
        self.assertEqual(assigned.headers["location"], "/?room_page=2&patient_page=2")
        self.assertIn("Rooms page 2 of 2", self.client.get(assigned.headers["location"]).text)
        no_beds = self.client.get("/").text
        self.assertIn('id="assign-bed" name="bed_id" required disabled', no_beds)
        self.assertIn('id="assign-patient" name="patient_id" required disabled', no_beds)
        self.assertIn("No beds available", no_beds)

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
        self.assertEqual(
            self.client.post("/roster/assign-bed", data={"patient_id": "1", "bed_id": "3"}, follow_redirects=False).headers["location"],
            "/login",
        )

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

    def test_optional_roster_fields_support_independent_room_and_bed_creation(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        roster = self.client.get("/").text
        self.assertIn("Add to the roster", roster)
        self.assertIn('action="/roster/add" method="post"', roster)
        self.assertEqual(roster.count('action="/roster/add" method="post"'), 1)
        self.assertNotIn('name="kind"', roster)
        self.assertIn('name="label" type="text"', roster)
        self.assertIn('name="display_name" type="text"', roster)
        self.assertIn('name="room_id"', roster)

        empty_room = self.client.post("/roster/add", data={"label": "103"})
        self.assertEqual(empty_room.status_code, 200)
        self.assertIn("No beds yet", empty_room.text)
        room_and_bed = self.client.post("/roster/add", data={"label": "104", "bed_label": "A"})
        self.assertEqual(room_and_bed.status_code, 200)
        self.assertIn("Room 104 · Bed A", room_and_bed.text)
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '104'").fetchone()[0]
        another_bed = self.client.post("/roster/add", data={"room_id": str(room_id), "bed_label": "B"})
        self.assertEqual(another_bed.status_code, 200)
        self.assertIn("104 (2 beds)</option>", another_bed.text)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '103'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '104'").fetchone()[0], 2)

    def test_combined_form_rejects_invalid_beds_and_rooms(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        for data, status in (
            ({"label": "103", "bed_label": "x" * 21}, 400),
            ({"label": "101", "bed_label": "C"}, 409),
            ({"room_id": "999", "bed_label": "A"}, 400),
            ({"room_id": "invalid", "bed_label": "A"}, 400),
            ({"room_id": "1", "bed_label": ""}, 400),
            ({"room_id": "1", "bed_label": "A"}, 409),
            ({"label": "103", "room_id": "1", "bed_label": "C"}, 400),
            ({"bed_label": "C"}, 400),
            ({"bed_id": "3"}, 400),
            ({}, 400),
        ):
            with self.subTest(data=data):
                response = self.client.post("/roster/add", data=data)
                self.assertEqual(response.status_code, status)
                self.assertIn('role="alert"', response.text)
        with open_database(database_path()) as connection:
            self.assertIsNone(connection.execute("SELECT id FROM room WHERE label = '103'").fetchone())
            self.assertIsNone(connection.execute("SELECT id FROM bed WHERE room_label = '101' AND bed_label = 'C'").fetchone())

    def test_one_roster_form_adds_patients_with_any_placement(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '102'").fetchone()[0]
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102'").fetchone()[0]
        for name, data, location in (
            ("Nora", {"bed_id": ""}, "Unassigned"),
            ("Sam", {"bed_id": str(empty_bed)}, "Room 102 · Bed A"),
            ("Pat", {"room_id": str(room_id), "bed_label": "B"}, "Room 102 · Bed B"),
            ("Dee", {"label": "103", "bed_label": "A"}, "Room 103 · Bed A"),
        ):
            with self.subTest(name=name):
                response = self.client.post(
                    "/roster/add", data={"display_name": name, **data}, follow_redirects=False
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn(location, self.client.get(response.headers["location"]).text)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM patient").fetchone()[0], 6)
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '103'").fetchone()[0], 1)
        room_and_patient = self.client.post(
            "/roster/add", data={"label": "104", "display_name": "Lee"}, follow_redirects=False
        )
        self.assertEqual(room_and_patient.status_code, 303)
        self.assertIn("Unassigned", self.client.get(room_and_patient.headers["location"]).text)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '104'").fetchone()[0], 0)

    def test_patient_can_use_available_bed_while_creating_another_empty_bed(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            empty_bed = connection.execute("SELECT id FROM bed WHERE room_label = '102'").fetchone()[0]
        response = self.client.post(
            "/roster/add",
            data={"label": "103", "bed_label": "A", "display_name": "Nora", "bed_id": str(empty_bed)},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("Room 102 · Bed A", self.client.get(response.headers["location"]).text)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '103'").fetchone()[0], 1)
            self.assertIsNone(connection.execute(
                "SELECT patient.id FROM patient JOIN bed ON bed.id = patient.bed_id WHERE bed.room_label = '103'"
            ).fetchone())

    def test_one_roster_form_rejects_invalid_patient_or_room_without_partial_records(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        for data, status in (
            ({"display_name": " ", "label": "103", "bed_label": "A"}, 400),
            ({"display_name": "Nora", "label": "101", "bed_label": "C"}, 409),
            ({"display_name": "Nora", "room_id": "999", "bed_label": "A"}, 400),
            ({"display_name": "Nora", "label": "103", "bed_label": "A", "bed_id": "999"}, 400),
            ({"display_name": "Nora", "label": "103", "bed_label": "A", "bed_id": "1"}, 409),
        ):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/roster/add", data=data).status_code, status)
        with open_database(database_path()) as connection:
            self.assertIsNone(connection.execute("SELECT id FROM room WHERE label = '103'").fetchone())
            self.assertIsNone(connection.execute("SELECT id FROM patient WHERE display_name = 'Nora'").fetchone())
        response = self.client.post("/roster/add", data={"label": "103", "room_id": "1", "display_name": "Nora"})
        self.assertIn('value="103"', response.text)
        self.assertIn('value="Nora"', response.text)

    def test_one_roster_form_requires_login(self):
        response = self.client.post(
            "/roster/add", data={"label": "103"}, follow_redirects=False
        )
        self.assertEqual(response.headers["location"], "/login")
        with open_database(database_path()) as connection:
            self.assertIsNone(connection.execute("SELECT id FROM room WHERE label = '103'").fetchone())

    def test_room_dropdown_counts_beds_and_patient_can_create_one(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        roster = self.client.get("/").text
        self.assertIn("101 (2 beds)</option>", roster)
        self.assertIn("102 (1 bed)</option>", roster)
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '102'").fetchone()[0]
        created = self.client.post(
            "/patients",
            data={"display_name": "Nora", "new_room_id": str(room_id), "new_bed_label": "B"},
            follow_redirects=False,
        )
        self.assertEqual(created.status_code, 303)
        self.assertIn("Room 102 · Bed B", self.client.get(created.headers["location"]).text)
        self.assertIn("102 (2 beds)</option>", self.client.get("/").text)

    def test_new_bed_assignment_rejects_invalid_choices_without_creating_patient(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '102'").fetchone()[0]
        for data, status in (
            ({"new_room_id": str(room_id), "new_bed_label": ""}, 400),
            ({"new_room_id": "999", "new_bed_label": "B"}, 400),
            ({"new_room_id": "not-a-room", "new_bed_label": "B"}, 400),
            ({"new_bed_label": "B"}, 400),
            ({"new_room_id": str(room_id), "new_bed_label": "A"}, 409),
            ({"bed_id": "3", "new_room_id": str(room_id), "new_bed_label": "B"}, 400),
        ):
            with self.subTest(data=data):
                response = self.client.post("/patients", data={"display_name": "Nora", **data})
                self.assertEqual(response.status_code, status)
        with open_database(database_path()) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM patient WHERE display_name = 'Nora'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM bed WHERE room_label = '102'").fetchone()[0], 1)

    def test_rooms_and_unassigned_patients_paginate_independently(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            connection.executemany("INSERT INTO room (label) VALUES (?)", [(str(n),) for n in range(103, 107)])
            connection.executemany("INSERT INTO patient (display_name) VALUES (?)", [(f"Demo {n}",) for n in range(5)])
        first = self.client.get("/").text
        self.assertEqual(first.count('class="room-group room-'), 3)
        self.assertEqual(first.count("No bed assigned</p>"), 3)
        self.assertIn("Rooms page 1 of 2", first)
        self.assertIn("Patients page 1 of 2", first)
        self.assertIn('href="/?room_page=2&amp;patient_page=1"', first)
        self.assertIn('href="/?room_page=1&amp;patient_page=2"', first)
        self.assertIn("106 (0 beds)</option>", first)
        second = self.client.get("/?room_page=2&patient_page=2").text
        self.assertEqual(second.count('class="room-group room-'), 3)
        self.assertEqual(second.count("No bed assigned</p>"), 2)
        self.assertIn("<h3>Room 106</h3>", second)
        self.assertNotIn("<h3>Room 101</h3>", second)
        self.assertIn("Demo 4", second)
        self.assertNotIn("Demo 0</h3>", second)
        self.assertIn('href="/?room_page=1&amp;patient_page=2"', second)
        self.assertIn('href="/?room_page=2&amp;patient_page=1"', second)
        self.assertIn("Rooms page 2 of 2", self.client.get("/?room_page=999").text)

    def test_beds_within_a_room_show_three_cards_per_page(self):
        self.client.post("/login", data={"caregiver": "Maria", "password": "sixchars"})
        with open_database(database_path()) as connection:
            room_id = connection.execute("SELECT id FROM room WHERE label = '101'").fetchone()[0]
            connection.executemany(
                "INSERT INTO bed (room_label, bed_label) VALUES ('101', ?)",
                [("C",), ("D",), ("E",)],
            )
        first = self.client.get("/").text
        self.assertIn("101 (5 beds)</option>", first)
        self.assertIn("Beds page 1 of 2", first)
        self.assertIn(f"bed_page_{room_id}=2", first)
        self.assertNotIn("Room 101 · Bed E</p>", first)
        second = self.client.get(f"/?bed_page_{room_id}=2").text
        self.assertIn("Room 101 · Bed E</p>", second)
        self.assertNotIn("Room 101 · Bed A</p>", second)
        self.assertIn("Beds page 2 of 2", second)

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
