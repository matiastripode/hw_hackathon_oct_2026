from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import db


def render(name, **context):
    directory = Path(__file__).resolve().parents[1] / "app" / "templates"
    env = Environment(loader=FileSystemLoader(directory), autoescape=select_autoescape(["html"]))
    env.globals["url_for"] = lambda route, path: "/static" + path
    return env.get_template(name).render(**context)


def test_login_and_board_render_with_escaped_snapshot(tmp_path):
    path = tmp_path / "demo.db"
    db.initialize(path)
    assert "Demo login - no real authentication" in render("login.html", caregiver=None, error=None)
    db.create_entry(path, kind="concern", title="<script>alert(1)</script>", details="Watch this",
                    severity="high", status="open", value="", author="Maria")
    db.submit_handoff(path, author="Maria", note="<img src=x onerror=alert(1)>")
    state = db.get_state(path)
    html = render("board.html", caregiver="Maria", state=state, error=None, type_labels=db.TYPE_LABELS)
    assert "Needs acknowledgement" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<script>alert(1)</script>" not in html
    db.acknowledge_handoff(path, state["handoff"]["id"], author="Alex")
    html = render("board_content.html", caregiver="Alex", state=db.get_state(path),
                  error=None, type_labels=db.TYPE_LABELS)
    assert "Acknowledged by Alex" in html


def test_handoff_leads_with_open_attention_and_keeps_acknowledgement_clear(tmp_path):
    path = tmp_path / "demo.db"
    db.initialize(path)
    db.create_entry(path, kind="concern", title="Check balance", details="Unsteady after dinner.",
                    severity="high", status="open", value="", author="Maria")
    db.submit_handoff(path, author="Maria", note="Please check in first thing.")

    html = render("board_content.html", caregiver="Alex", state=db.get_state(path),
                  error=None, type_labels=db.TYPE_LABELS)
    assert html.index("Still needs attention") < html.index("Changed") < html.index("Completed")
    assert "Unsteady after dinner." in html
    assert "I've read this handoff" in html
