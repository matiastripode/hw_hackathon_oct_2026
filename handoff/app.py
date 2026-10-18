import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from handoff.db import database_path, initialize_database, open_database


ASSETS = Path(__file__).resolve().parent
EDITABLE_ENTRY_TYPES = {"symptom", "task"}
SEVERITIES = {"low", "medium", "high"}
ENTRY_STATUSES = {"open", "done"}
ROSTER_PAGE_SIZE = 3
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


def render_patient_login(request, error=None, selected_patient=""):
    with open_database(database_path()) as connection:
        patients = connection.execute(
            "SELECT id, display_name FROM patient WHERE active = 1 ORDER BY display_name, id"
        ).fetchall()
    return templates.TemplateResponse(
        request, "patient_login.html",
        {"patients": patients, "error": error, "selected_patient": selected_patient},
        status_code=400 if error else 200,
    )


@app.get("/patient/login", response_class=HTMLResponse)
def patient_login_page(request: Request):
    if request.session.get("patient_id"):
        return RedirectResponse("/patient", status_code=303)
    return render_patient_login(request)


@app.post("/patient/login")
def patient_login(request: Request, patient_id: str = Form(""), password: str = Form("")):
    error = "Please choose a patient and enter a demo password of at least 6 characters."
    if len(password) < 6:
        return render_patient_login(request, error, patient_id)
    with open_database(database_path()) as connection:
        patient = connection.execute(
            "SELECT id FROM patient WHERE id = ? AND active = 1", (patient_id,)
        ).fetchone()
    if patient is None:
        return render_patient_login(request, error, patient_id)
    request.session.clear()
    request.session["patient_id"] = patient["id"]
    return RedirectResponse("/patient", status_code=303)


@app.get("/patient", response_class=HTMLResponse)
def patient_home(request: Request):
    patient_id = request.session.get("patient_id")
    if not patient_id:
        return RedirectResponse("/patient/login", status_code=303)
    with open_database(database_path()) as connection:
        patient = connection.execute(
            "SELECT id, display_name FROM patient WHERE id = ? AND active = 1", (patient_id,)
        ).fetchone()
    if patient is None:
        request.session.clear()
        return RedirectResponse("/patient/login", status_code=303)
    return templates.TemplateResponse(
        request, "patient_home.html",
        {
            "patient": patient,
            "called": request.session.get("call_video", False),
            "open_video": request.session.pop("open_video", False),
        },
    )


@app.post("/patient/call")
def call_nurse(request: Request):
    patient_id = request.session.get("patient_id")
    if not patient_id:
        return RedirectResponse("/patient/login", status_code=303)
    with open_database(database_path()) as connection:
        patient = connection.execute(
            "SELECT id, display_name FROM patient WHERE id = ? AND active = 1", (patient_id,)
        ).fetchone()
        if patient is None:
            request.session.clear()
            return RedirectResponse("/patient/login", status_code=303)
        existing = connection.execute(
            """SELECT 1 FROM entry WHERE patient_id = ? AND type = 'concern'
               AND title = 'Patient requested a nurse' AND status = 'open' LIMIT 1""",
            (patient_id,),
        ).fetchone()
        if not existing:
            connection.execute(
                """INSERT INTO entry (patient_id, type, title, details, severity, status, author)
                   VALUES (?, 'concern', 'Patient requested a nurse', 'Requested from the patient demo page.', 'high', 'open', ?)""",
                (patient_id, patient["display_name"]),
            )
    request.session["call_video"] = True
    request.session["open_video"] = True
    return RedirectResponse("/patient", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.post("/patient/logout")
def patient_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/patient/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def roster(request: Request, room_page: int = Query(1, ge=1), patient_page: int = Query(1, ge=1)):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    return render_roster(request, room_page=room_page, patient_page=patient_page)


def paginate(items, page):
    pages = max(1, (len(items) + ROSTER_PAGE_SIZE - 1) // ROSTER_PAGE_SIZE)
    page = min(page, pages)
    start = (page - 1) * ROSTER_PAGE_SIZE
    return items[start:start + ROSTER_PAGE_SIZE], page, pages


def render_roster(request, error=None, status_code=200, room_error=None, bed_error=None,
                  room_page=1, patient_page=1, form_values=None,
                  assignment_error=None, assignment_patient_id=None):
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
        available_beds = [bed for bed in beds if not bed["patient_id"]]
        unassigned = connection.execute(
            "SELECT id, display_name FROM patient WHERE bed_id IS NULL AND active = 1 ORDER BY display_name, id"
        ).fetchall()
    visible_rooms, room_page, room_pages = paginate(rooms, room_page)
    visible_patients, patient_page, patient_pages = paginate(unassigned, patient_page)
    visible_beds, bed_pages = {}, {}
    for room in visible_rooms:
        room_beds = [bed for bed in beds if bed["room_label"] == room["label"]]
        try:
            requested_page = max(1, int(request.query_params.get(f"bed_page_{room['id']}", "1")))
        except ValueError:
            requested_page = 1
        visible_beds[room["id"]], current_page, total_pages = paginate(room_beds, requested_page)
        bed_pages[room["id"]] = (current_page, total_pages)
    return templates.TemplateResponse(
        request,
        "roster.html",
        {
            "caregiver": request.session["caregiver"],
            "rooms": rooms,
            "visible_rooms": visible_rooms,
            "room_page": room_page,
            "room_pages": room_pages,
            "beds": beds,
            "available_beds": available_beds,
            "visible_beds": visible_beds,
            "bed_pages": bed_pages,
            "unassigned": visible_patients,
            "all_unassigned": unassigned,
            "patient_page": patient_page,
            "patient_pages": patient_pages,
            "unassigned_count": len(unassigned),
            "error": error,
            "room_error": room_error,
            "bed_error": bed_error,
            "form_values": form_values or {},
            "assignment_error": assignment_error,
            "assignment_patient_id": assignment_patient_id,
        },
        status_code=status_code,
    )


def render_patient(request, patient_id, error=None, status_code=200, entry_error=None, handoff_error=None):
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
        handoff = connection.execute(
            "SELECT * FROM handoff WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
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
            "handoff": handoff,
            "handoff_error": handoff_error,
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


@app.post("/roster/add")
def add_to_roster(request: Request, room_id: str = Form(""),
                  label: str = Form(""), bed_label: str = Form(""),
                  display_name: str = Form(""), bed_id: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    values = dict(room_id=room_id, label=label, bed_label=bed_label, display_name=display_name, bed_id=bed_id)

    def invalid(message, status=400):
        return render_roster(request, error=message, status_code=status, form_values=values)

    label, bed_label, name = label.strip(), bed_label.strip(), display_name.strip()
    if not any((label, room_id, bed_label, name, bed_id)):
        return invalid("Enter a room, bed, or fictional patient to add.")
    if values["label"] and (not 1 <= len(label) <= 40 or not label.isprintable()):
        return invalid("Enter a room label (up to 40 characters).")
    if values["bed_label"] and (not 1 <= len(bed_label) <= 20 or not bed_label.isprintable()):
        return invalid("Enter a bed label (up to 20 characters).")
    if values["display_name"] and (not 1 <= len(name) <= 80 or not name.isprintable()):
        return invalid("Enter a fictional patient name (up to 80 characters).")
    if label and room_id:
        return invalid("Choose a new room label or an existing room, not both.")
    if room_id and not bed_label:
        return invalid("Enter a bed label for the selected room.")
    if bed_label and not (label or room_id):
        return invalid("Enter a new room label or choose an existing room for the bed.")
    if bed_id and not name:
        return invalid("Enter a fictional patient name to assign a bed.")

    stage = "room"
    try:
        with open_database(database_path()) as connection:
            room_label = label
            if label:
                connection.execute("INSERT INTO room (label) VALUES (?)", (label,))
            elif room_id:
                try:
                    selected_room = int(room_id)
                except ValueError as exc:
                    raise ValueError("Choose an existing room for the new bed.") from exc
                room = connection.execute("SELECT label FROM room WHERE id = ?", (selected_room,)).fetchone()
                if room is None:
                    raise ValueError("Choose an existing room for the new bed.")
                room_label = room["label"]
            new_bed_id = None
            if bed_label:
                stage = "bed"
                new_bed_id = connection.execute(
                    "INSERT INTO bed (room_label, bed_label) VALUES (?, ?)", (room_label, bed_label)
                ).lastrowid
            patient_id = None
            if name:
                stage = "patient"
                assigned_bed = selected_bed(connection, bed_id) if bed_id else new_bed_id
                patient_id = connection.execute(
                    "INSERT INTO patient (display_name, bed_id) VALUES (?, ?)", (name, assigned_bed)
                ).lastrowid
    except ValueError as exc:
        return invalid(str(exc))
    except sqlite3.IntegrityError:
        message = {
            "room": "That room already exists.",
            "bed": "That bed already exists in this room.",
            "patient": "That bed is already occupied.",
        }[stage]
        return invalid(message, 409)
    return RedirectResponse(f"/patients/{patient_id}" if patient_id else "/", status_code=303)


@app.post("/rooms")
def create_room(request: Request, label: str = Form(""), room_id: str = Form("new"),
                bed_label: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    if room_id != "new":
        return create_bed(request, room_id, bed_label)
    label = label.strip()
    bed_label = bed_label.strip()
    if not 1 <= len(label) <= 40 or not label.isprintable():
        return render_roster(request, status_code=400, room_error="Enter a room label (up to 40 characters).")
    if bed_label and (len(bed_label) > 20 or not bed_label.isprintable()):
        return render_roster(request, status_code=400, room_error="Enter a bed label (up to 20 characters).")
    try:
        with open_database(database_path()) as connection:
            connection.execute("INSERT INTO room (label) VALUES (?)", (label,))
            if bed_label:
                connection.execute("INSERT INTO bed (room_label, bed_label) VALUES (?, ?)", (label, bed_label))
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


@app.post("/patients/{patient_id}/handoffs")
def submit_handoff(request: Request, patient_id: int, note: str = Form("")):
    caregiver = request.session.get("caregiver")
    if not caregiver:
        return RedirectResponse("/login", status_code=303)
    note = note.strip()
    if not 1 <= len(note) <= 1000:
        return render_patient(request, patient_id, status_code=400,
                              handoff_error="Enter a handoff note (up to 1000 characters).")
    with open_database(database_path()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM patient WHERE id = ? AND active = 1", (patient_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")
        pending = connection.execute(
            "SELECT 1 FROM handoff WHERE patient_id = ? AND acknowledged_at IS NULL LIMIT 1", (patient_id,)
        ).fetchone()
        if not pending:
            connection.execute(
                "INSERT INTO handoff (patient_id, author, note) VALUES (?, ?, ?)",
                (patient_id, caregiver, note),
            )
    if pending:
        return render_patient(request, patient_id, status_code=409,
                              handoff_error="A handoff is already awaiting acknowledgement.")
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


@app.post("/patients/{patient_id}/handoffs/{handoff_id}/acknowledge")
def acknowledge_handoff(request: Request, patient_id: int, handoff_id: int):
    caregiver = request.session.get("caregiver")
    if not caregiver:
        return RedirectResponse("/login", status_code=303)
    with open_database(database_path()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not connection.execute("SELECT 1 FROM patient WHERE id = ? AND active = 1", (patient_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Patient not found")
        latest = connection.execute(
            "SELECT id, author, acknowledged_at FROM handoff WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
            (patient_id,),
        ).fetchone()
        if latest is None or latest["id"] != handoff_id or latest["acknowledged_at"]:
            error = "This handoff is no longer awaiting acknowledgement."
        elif latest["author"] == caregiver:
            error = "Another caregiver needs to acknowledge this handoff."
        else:
            error = None
            connection.execute(
                """UPDATE handoff SET acknowledged_by = ?,
                       acknowledged_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                   WHERE id = ? AND patient_id = ?""",
                (caregiver, handoff_id, patient_id),
            )
    if error:
        return render_patient(request, patient_id, status_code=409, handoff_error=error)
    return RedirectResponse(f"/patients/{patient_id}", status_code=303)


@app.post("/patients")
def create_patient(request: Request, display_name: str = Form(""), bed_id: str = Form(""),
                   new_room_id: str = Form(""), new_bed_label: str = Form("")):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    name = display_name.strip()
    if not name or len(name) > 80 or not name.isprintable():
        return render_roster(request, "Enter a fictional patient name (up to 80 characters).", 400)
    new_bed_label = new_bed_label.strip()
    if bed_id and (new_room_id or new_bed_label):
        return render_roster(request, "Choose an available bed or create a new one, not both.", 400)
    if new_bed_label and not new_room_id:
        return render_roster(request, "Choose a room for the new bed.", 400)
    if new_room_id and (not 1 <= len(new_bed_label) <= 20 or not new_bed_label.isprintable()):
        return render_roster(request, "Enter a bed label (up to 20 characters).", 400)
    try:
        with open_database(database_path()) as connection:
            assigned_bed = selected_bed(connection, bed_id)
            if new_room_id:
                try:
                    room_id = int(new_room_id)
                except ValueError as exc:
                    raise ValueError("Choose an existing room for the new bed.") from exc
                room = connection.execute("SELECT label FROM room WHERE id = ?", (room_id,)).fetchone()
                if room is None:
                    raise ValueError("Choose an existing room for the new bed.")
                assigned_bed = connection.execute(
                    "INSERT INTO bed (room_label, bed_label) VALUES (?, ?)", (room["label"], new_bed_label)
                ).lastrowid
            patient_id = connection.execute(
                "INSERT INTO patient (display_name, bed_id) VALUES (?, ?)", (name, assigned_bed)
            ).lastrowid
    except ValueError as exc:
        return render_roster(request, str(exc), 400)
    except sqlite3.IntegrityError:
        message = "That bed already exists in this room." if new_room_id else "That bed is already occupied."
        return render_roster(request, message, 409)
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


@app.post("/roster/assign-bed")
def assign_from_roster(request: Request, patient_id: str = Form(""), bed_id: str = Form(""),
                       room_page: int = Form(1), patient_page: int = Form(1)):
    if not request.session.get("caregiver"):
        return RedirectResponse("/login", status_code=303)
    room_page, patient_page = max(1, room_page), max(1, patient_page)

    def invalid(message, status=400):
        return render_roster(
            request, status_code=status, room_page=room_page, patient_page=patient_page,
            assignment_error=message, assignment_patient_id=patient_id,
        )

    try:
        try:
            selected_patient = int(patient_id)
        except ValueError as exc:
            raise ValueError("Choose an unassigned patient.") from exc
        if not bed_id:
            raise ValueError("Choose an available bed.")
        with open_database(database_path()) as connection:
            patient = connection.execute(
                "SELECT bed_id FROM patient WHERE id = ? AND active = 1", (selected_patient,)
            ).fetchone()
            if patient is None or patient["bed_id"] is not None:
                raise ValueError("Choose an unassigned patient.")
            assigned_bed = selected_bed(connection, bed_id)
            result = connection.execute(
                "UPDATE patient SET bed_id = ? WHERE id = ? AND bed_id IS NULL AND active = 1",
                (assigned_bed, selected_patient),
            )
            if result.rowcount != 1:
                raise ValueError("Choose an unassigned patient.")
    except ValueError as exc:
        return invalid(str(exc))
    except sqlite3.IntegrityError:
        return invalid("That bed is already occupied.", 409)
    return RedirectResponse(f"/?room_page={room_page}&patient_page={patient_page}", status_code=303)


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
