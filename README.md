# Offline Caregiver Handoff Board

An offline-first Raspberry Pi 4 prototype for handing off fictional caregiving
updates. The [project plan](Offline%20Caregiver%20Handoff%20Board.html) describes
the original single-patient workflow. The app has a demo login, a room/bed
roster, basic fictional patient and bed-assignment management, and patient
boards with symptom and task logging. Handoffs, live updates and GPIO are not
implemented yet.
Do not enter real patient information.

## Run

Requires Python 3.10+ on the Pi. Install dependencies during setup, before
disconnecting from the internet:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./start.sh
```

`start.sh` uses `.venv/bin/python` if present, otherwise `python3`. It listens on
`127.0.0.1:${PORT:-3000}` by default for nginx to proxy over plain HTTP. Set
`HOST=0.0.0.0` if direct access on the local network is needed. The running app
does not call outside services or load remote assets. Visit
`http://127.0.0.1:3000/` locally, or the Pi's nginx address from another device.
Choose a caregiver and enter any demo password of at least 6 characters. Only
its length is checked; the password is not stored. Sign out from the dashboard
to switch caregivers. This is not real authentication. The signed session
cookie expires after 24 hours, and sessions also end on app restart unless
`HANDOFF_SESSION_SECRET` is set to a private, stable value in the app's
environment before launch.

After sign-in, the roster shows occupied and empty beds, grouped by room.
Rooms, beds within each room, and unassigned patients show up to three items
per page with separate navigation. Room dropdowns always include every room
and show its current bed count, even when that room is on another roster page.
Room headers show **Occupied** when all beds are taken, **Free** when all are
empty, **Partially occupied** when mixed and **No beds** for an empty room.
Use **Add to the roster** to enter any combination of a new room, bed and
fictional patient. Leave fields blank to add just one or two: an empty room
needs only a room label; a new bed needs a new room label or an existing room
selection; a patient may remain unassigned, take an available bed, or be
assigned the new bed entered in the same form. A patient can be moved to an
available bed using the patient and bed dropdowns above the unassigned roster,
or from their care board; occupied beds cannot be taken without
first unassigning the current patient. Care entries stay with the patient
when their bed changes.
On a patient board, expand **Log symptom** or **Add task** to record a fictional
update. Common symptom and task choices fill an editable title only; details,
priority and status are set by the caregiver before saving. Choosing a starter
does not create an entry on its own. The signed-in caregiver is recorded as its
author. Only that author
can edit symptom and task logs; medication, appointment and concern records
remain read-only. Other caregivers can add their own updates but cannot change
someone else's entries. There is no end-of-shift lock yet: authors can still
edit their own entries after signing back in. This is a demo restriction, not
real identity verification, since any caregiver can select another name on
the dummy login screen.

On first launch, the app creates `data/handoff.sqlite3` in WAL mode. The demo
starts with James in Room 101/Bed A, Lena in Room 101/Bed B, and an empty bed
in Room 102/Bed A. Existing databases are upgraded in place: rooms are
backfilled from bed labels without changing bed IDs or patient assignments,
and older single-patient entries and handoffs are linked to James without
resetting them.
Set `HANDOFF_DB_PATH` to use a different SQLite file. The database and `.venv`
are ignored by Git. The Pi's system clock must be set for accurate timestamps
when offline; clock syncing is not part of this scaffold.

To replace **all** entries, handoffs, patients, beds, rooms and caregivers in the
chosen database with fictional demo data (this permanently discards existing
records):

```sh
.venv/bin/python -m handoff.seed --reset
```

If not using a virtual environment, use `python3 -m handoff.seed --reset`.
Do not run this against a database containing information you want to keep.

## Tests

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

## Raspberry Pi auto-start setup

Copy the project to the Pi without the macOS `.venv`, `.git` or local `data`
directory. Run this as the normal Pi user from the project folder while the Pi
has internet access for the one-time dependency installation:

```sh
./deploy/setup-pi.sh --check
./deploy/setup-pi.sh --install
```

The installer creates a Linux `.venv`, a private session secret in the ignored
`data/` directory and a `caregiver-handoff.service` systemd unit. It starts the
app on `127.0.0.1:3000` and checks `/health`. It does **not** install or change
nginx: if port 80 does not already forward to the app, inspect nginx before
editing its configuration. It refuses to overwrite a different existing
systemd service. Keep `data/` when updating the app so its SQLite database and
session secret persist. Never copy real patient information into this demo.
For diagnostics on the Pi, use `./deploy/setup-pi.sh --check`,
`systemctl status caregiver-handoff.service --no-pager` and
`journalctl -u caregiver-handoff.service -n 50 --no-pager`.
