import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from handoff.db import database_path, initialize_database, open_database


ASSETS = Path(__file__).resolve().parent
EDITABLE_ENTRY_TYPES = {"symptom", "task"}
SEVERITIES = {"low", "medium", "high"}
ENTRY_STATUSES = {"open", "done"}
ENTRY_PRESETS = {
    "symptom": ("Dizziness", "Pain or discomfort", "Poor sleep", "Reduced appetite"),
    "task": ("Confirm transportation", "Prepare appointment notes", "Check supplies", "Confirm follow-up appointment"),
}
templates = Jinja2Templates(directory=str(ASSETS / "templates"))


@asynccontextmanager
async def lifespan(app):
    initialize_database(database_path())
    yield


app = FastAPI(
    title="Offline Caregiver Handoff Board",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("HANDOFF_SESSION_SECRET") or secrets.token_urlsafe(32),
    max_age=24 * 60 * 60,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=ASSETS / "static"), name="static")


def render_login(request, error=None, caregiver=""):
    with open_database(database_path()) as connection:
        caregivers = connection.execute("SELECT name FROM caregiver ORDER BY id").fetchall()
    return templates.TemplateResponse(
        request,
        "login.html",
        {"caregivers": caregivers, "error": error, "selected_caregiver": caregiver},
        status_code=400 if error else 200,
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("caregiver"):
        return RedirectResponse("/", status_code=303)
    return render_login(request)


@app.post("/login")
def login(request: Request, caregiver: str = Form(""), password: str = Form("")):
    error = "Please choose a caregiver and enter a demo password of at least 6 characters."
    if len(password) < 6:
        return render_login(request, error, caregiver)
    with open_database(database_path()) as connection:
        selected = connection.execute("SELECT name FROM caregiver WHERE name = ?", (caregiver,)).fetchone()
    if not selected:
        return render_login(request, error, caregiver)
    request.session.clear()
    request.session["caregiver"] = selected["name"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def roster(request: Request):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    return render_roster(request)


def render_roster(request, error=None, status_code=200, room_error=None, bed_error=None):
    with open_database(database_path()) as connection:
        rooms = []
        for row in connection.execute(
            """SELECT room.id, room.label, count(bed.id) AS bed_count,
                      count(patient.id) AS occupied_count
               FROM room LEFT JOIN bed ON bed.room_label = room.label
               LEFT JOIN patient ON patient.bed_id = bed.id
               GROUP BY room.id ORDER BY room.label"""
        ):
            room = dict(row)
            occupied, total = room["occupied_count"], room["bed_count"]
            if total == 0:
                room["state"], room["status"] = "no-beds", "No beds"
            elif occupied == 0:
                room["state"], room["status"] = "free", f"Free · {occupied}/{total} beds"
            elif occupied == total:
                room["state"], room["status"] = "occupied", f"Occupied · {occupied}/{total} beds"
            else:
                room["state"], room["status"] = "partial", f"Partially occupied · {occupied}/{total} beds"
            rooms.append(room)
        beds = connection.execute(
            """SELECT bed.id, bed.room_label, bed.bed_label, patient.id AS patient_id,
                      patient.display_name
               FROM bed LEFT JOIN patient ON patient.bed_id = bed.id
               ORDER BY bed.room_label, bed.bed_label"""
        ).fetchall()
        unassigned = connection.execute(
            "SELECT id, display_name FROM patient WHERE bed_id IS NULL AND active = 1 ORDER BY display_name, id"
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "roster.html",
        {
            "caregiver": request.session["caregiver"],
            "rooms": rooms,
            "beds": beds,
            "unassigned": unassigned,
            "error": error,
            "room_error": room_error,
            "bed_error": bed_error,
        },
        status_code=status_code,
    )


def render_patient(request, patient_id, error=None, status_code=200, entry_error=None):
    with open_database(database_path()) as connection:
        patient = connection.execute(
            """SELECT patient.id, patient.display_name, patient.bed_id, bed.room_label, bed.bed_label
               FROM patient LEFT JOIN bed ON bed.id = patient.bed_id WHERE patient.id = ?""",
            (patient_id,),
        ).fetchone()
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        entries = connection.execute(
            """SELECT * FROM entry WHERE patient_id = ?
               ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                        CASE WHEN type = 'concern' AND status = 'open' THEN 0 ELSE 1 END,
                        CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                        updated_at DESC, id DESC""",
            (patient_id,),
        ).fetchall()
        available_beds = connection.execute(
            """SELECT bed.id, bed.room_label, bed.bed_label FROM bed
               LEFT JOIN patient ON patient.bed_id = bed.id
               WHERE patient.id IS NULL OR patient.id = ?
               ORDER BY bed.room_label, bed.bed_label""",
            (patient_id,),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "entries": entries,
            "patient": patient,
            "available_beds": available_beds,
            "caregiver": request.session["caregiver"],
            "error": error,
            "entry_error": entry_error,
            "entry_presets": ENTRY_PRESETS,
        },
        status_code=status_code,
    )


def selected_bed(connection, bed_id):
    if not bed_id:
        return None
    try:
        selected_id = int(bed_id)
    except ValueError as exc:
        raise ValueError("Choose an available bed.") from exc
    if not connection.execute("SELECT 1 FROM bed WHERE id = ?", (selected_id,)).fetchone():
        raise ValueError("Choose an available bed.")
    return selected_id


@app.post("/rooms")
def create_room(request: Request, label: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    label = label.strip()
    if not 1 <= len(label) <= 40 or not label.isprintable():
        return render_roster(request, status_code=400, room_error="Enter a room label (up to 40 characters).")
    try:
        with open_database(database_path()) as connection:
            connection.execute("INSERT INTO room (label) VALUES (?)", (label,))
    except sqlite3.IntegrityError:
        return render_roster(request, status_code=409, room_error="That room already exists.")
    return RedirectResponse("/", status_code=303)


@app.post("/beds")
def create_bed(request: Request, room_id: str = Form(""), bed_label: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    bed_label = bed_label.strip()
    if not 1 <= len(bed_label) <= 20 or not bed_label.isprintable():
        return render_roster(request, status_code=400, bed_error="Enter a bed label (up to 20 characters).")
    try:
        selected_room = int(room_id)
    except ValueError:
        return render_roster(request, status_code=400, bed_error="Choose an existing room.")
    try:
        with open_database(database_path()) as connection:
            room = connection.execute("SELECT label FROM room WHERE id = ?", (selected_room,)).fetchone()
            if room is not None:
                connection.execute(
                    "INSERT INTO bed (room_label, bed_label) VALUES (?, ?)", (room["label"], bed_label)
                )
    except sqlite3.IntegrityError:
        return render_roster(request, status_code=409, bed_error="That bed already exists in this room.")
    if room is None:
        return render_roster(request, status_code=400, bed_error="Choose an existing room.")
    return RedirectResponse("/", status_code=303)


@app.get("/patients/{patient_id}", response_class=HTMLResponse)
def patient_board(request: Request, patient_id: int):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    return render_patient(request, patient_id)


@app.post("/patients")
def create_patient(request: Request, display_name: str = Form(""), bed_id: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    name = display_name.strip()
    if not name or len(name) > 80 or not name.isprintable():
        return render_roster(request, "Enter a fictional patient name (up to 80 characters).", 400)
    try:
        with open_database(database_path()) as connection:
            assigned_bed = selected_bed(connection, bed_id)
            patient_id = connection.execute(
                "INSERT INTO patient (display_name, bed_id) VALUES (?, ?)", (name, assigned_bed)
            ).lastrowid
    except ValueError as exc:
        return render_roster(request, str(exc), 400)
    except sqlite3.IntegrityError:
        return render_roster(request, "That bed is already occupied.", 409)
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


@app.post("/patients/{patient_id}/bed")
def assign_bed(request: Request, patient_id: int, bed_id: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    try:
        with open_database(database_path()) as connection:
            if not connection.execute("SELECT 1 FROM patient WHERE id = ?", (patient_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Patient not found")
            assigned_bed = selected_bed(connection, bed_id)
            connection.execute("UPDATE patient SET bed_id = ? WHERE id = ?", (assigned_bed, patient_id))
    except ValueError as exc:
        return render_patient(request, patient_id, str(exc), 400)
    except sqlite3.IntegrityError:
        return render_patient(request, patient_id, "That bed is already occupied.", 409)
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


def valid_entry(title, details, severity, status):
    return (
        1 <= len(title) <= 120
        and title.isprintable()
        and len(details) <= 2000
        and severity in SEVERITIES
        and status in ENTRY_STATUSES
    )


@app.post("/patients/{patient_id}/entries")
def create_entry(
    request: Request,
    patient_id: int,
    type: str = Form(""),
    title: str = Form(""),
    details: str = Form(""),
    severity: str = Form("low"),
    status: str = Form("open"),
):
    caregiver = request.session.get("caregiver")
    if not caregiver:
        return RedirectResponse("/login", status_code=303)
    title, details = title.strip(), details.strip()
    if type not in EDITABLE_ENTRY_TYPES or not valid_entry(title, details, severity, status):
        return render_patient(request, patient_id, status_code=400, entry_error="Choose a symptom or task with a title, valid priority and status.")
    with open_database(database_path()) as connection:
        if not connection.execute("SELECT 1 FROM patient WHERE id = ? AND active = 1", (patient_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")
        connection.execute(
            """INSERT INTO entry (patient_id, type, title, details, severity, status, author)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (patient_id, type, title, details, severity, status, caregiver),
        )
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


@app.post("/patients/{patient_id}/entries/{entry_id}")
def edit_entry(
    request: Request,
    patient_id: int,
    entry_id: int,
    title: str = Form(""),
    details: str = Form(""),
    severity: str = Form(""),
    status: str = Form(""),
):
    caregiver = request.session.get("caregiver")
    if not caregiver:
        return RedirectResponse("/login", status_code=303)
    with open_database(database_path()) as connection:
        entry = connection.execute(
            "SELECT type, author FROM entry WHERE id = ? AND patient_id = ?", (entry_id, patient_id)
        ).fetchone()
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        if entry["author"] != caregiver or entry["type"] not in EDITABLE_ENTRY_TYPES:
            raise HTTPException(status_code=403, detail="Only the original author can edit symptom and task logs")
        title, details = title.strip(), details.strip()
        if not valid_entry(title, details, severity, status):
            return render_patient(request, patient_id, status_code=400, entry_error="Enter a title, valid priority and status.")
        connection.execute(
            """UPDATE entry SET title = ?, details = ?, severity = ?, status = ?,
                                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               WHERE id = ? AND patient_id = ? AND author = ? AND type IN ('symptom', 'task')""",
            (title, details, severity, status, entry_id, patient_id, caregiver),
        )
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


@app.get("/health")
def health():
    with open_database(database_path()) as connection:
        connection.execute("SELECT 1")
    return {"status": "ok"}
