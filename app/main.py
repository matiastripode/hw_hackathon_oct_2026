"""HTTP interface for the offline demo board."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import db


HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def create_app(db_path=None):
    path = Path(db_path) if db_path else db.DEFAULT_DB

    @asynccontextmanager
    async def lifespan(app):
        db.initialize(app.state.db_path)
        yield

    app = FastAPI(title="Offline Caregiver Handoff Board", lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.db_path = path
    # A restart signs everyone out. This is deliberately a demo identity, not authentication.
    app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32),
                       same_site="lax", max_age=60 * 60 * 12)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    def identity(request):
        return request.session.get("caregiver")

    def sign_in():
        return RedirectResponse("/login", status_code=303)

    def board(request, *, error=None, status_code=200, fragment=False):
        response = templates.TemplateResponse(
            request=request,
            name="board_content.html" if fragment else "board.html",
            context={"state": db.get_state(path), "caregiver": identity(request),
                     "error": error, "type_labels": db.TYPE_LABELS},
            status_code=status_code,
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    async def form_fields(request):
        body = await request.body()
        if len(body) > 8192:
            raise db.BoardError("The form is too large.")
        if body and request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise db.BoardError("Submit the form from the board.")
        return {key: values[-1] for key, values in
                parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}

    @app.get("/")
    async def home(request: Request):
        return RedirectResponse("/board" if identity(request) else "/login", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if identity(request):
            return RedirectResponse("/board", status_code=303)
        return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

    @app.post("/login")
    async def login(request: Request):
        try:
            fields = await form_fields(request)
            choice = fields.get("caregiver", "")
            if choice not in ("Maria", "Alex", "other"):
                raise db.BoardError("Choose a caregiver.")
            name = db._text(fields.get("name", ""), "Display name", 40, required=True) if choice == "other" else choice
        except (db.BoardError, UnicodeDecodeError) as exc:
            return templates.TemplateResponse(
                request=request, name="login.html",
                context={"error": str(exc) if isinstance(exc, db.BoardError) else "Invalid form encoding."},
                status_code=400,
            )
        request.session.clear()
        request.session["caregiver"] = name
        return RedirectResponse("/board", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return sign_in()

    @app.get("/board", response_class=HTMLResponse)
    async def board_page(request: Request):
        return board(request) if identity(request) else sign_in()

    @app.get("/board/fragment", response_class=HTMLResponse)
    async def board_fragment(request: Request):
        return board(request, fragment=True) if identity(request) else sign_in()

    @app.post("/entries")
    async def add_entry(request: Request):
        if not identity(request):
            return sign_in()
        try:
            fields = await form_fields(request)
            db.create_entry(
                path, kind=fields.get("type", ""), title=fields.get("title", ""),
                details=fields.get("details", ""), severity=fields.get("severity", ""),
                status=fields.get("status", ""), value=fields.get("value", ""),
                author=identity(request),
            )
        except (db.BoardError, UnicodeDecodeError) as exc:
            return board(request, error=str(exc), status_code=400)
        return RedirectResponse("/board", status_code=303)

    @app.post("/entries/{entry_id}")
    async def edit_entry(request: Request, entry_id: int):
        if not identity(request):
            return sign_in()
        try:
            fields = await form_fields(request)
            db.update_entry(
                path, entry_id, details=fields.get("details", ""),
                severity=fields.get("severity", ""), status=fields.get("status", ""),
                value=fields.get("value", ""), author=identity(request),
            )
        except (db.BoardError, UnicodeDecodeError) as exc:
            return board(request, error=str(exc), status_code=400)
        return RedirectResponse("/board", status_code=303)

    @app.post("/handoffs")
    async def add_handoff(request: Request):
        if not identity(request):
            return sign_in()
        try:
            fields = await form_fields(request)
            db.submit_handoff(path, author=identity(request), note=fields.get("note", ""))
        except (db.BoardError, UnicodeDecodeError) as exc:
            return board(request, error=str(exc), status_code=409 if "already awaiting" in str(exc) else 400)
        return RedirectResponse("/board", status_code=303)

    @app.post("/handoffs/{handoff_id}/acknowledge")
    async def acknowledge(request: Request, handoff_id: int):
        if not identity(request):
            return sign_in()
        try:
            await form_fields(request)
            db.acknowledge_handoff(path, handoff_id, author=identity(request))
        except (db.BoardError, UnicodeDecodeError) as exc:
            return board(request, error=str(exc), status_code=409)
        return RedirectResponse("/board", status_code=303)

    @app.get("/events")
    async def events(request: Request):
        if not identity(request):
            return sign_in()

        async def changes():
            last = db.get_revision(path)
            yield f"retry: 2000\nevent: board\ndata: {last}\n\n"
            ticks = 0
            while not await request.is_disconnected():
                await asyncio.sleep(1)
                revision = db.get_revision(path)
                if revision != last:
                    last = revision
                    yield f"event: board\ndata: {revision}\n\n"
                ticks += 1
                if ticks % 15 == 0:
                    yield ": keep-alive\n\n"

        return StreamingResponse(changes(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
