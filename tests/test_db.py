import pytest

from app import db


@pytest.fixture
def board_path(tmp_path):
    path = tmp_path / "board.db"
    db.initialize(path)
    return path


def test_demo_seed_is_idempotent_and_starts_without_handoff(board_path):
    state = db.get_state(board_path)
    assert state["open_count"] == 3
    assert state["handoff"] is None
    db.initialize(board_path)
    assert db.get_state(board_path)["open_count"] == 3


def test_handoff_freezes_shift_changes_and_preserves_later_edits(board_path):
    state = db.get_state(board_path)
    entries = {entry["title"]: entry for group in state["groups"] for entry in group["items"]}
    medication = entries["8:00 PM medication"]
    dizziness = entries["Dizziness"]

    start_revision = db.get_revision(board_path)
    db.update_entry(board_path, medication["id"], details=medication["details"], severity="medium",
                    status="done", value="taken at 8:00 PM", author="Maria")
    db.update_entry(board_path, dizziness["id"], details="More unsteady after dinner.",
                    severity="high", status="open", value="moderate", author="Maria")
    db.create_entry(board_path, kind="concern", title="Watch for a fall", details="Check in first thing.",
                    severity="high", status="open", value="", author="Maria")
    assert db.get_revision(board_path) == start_revision + 3
    assert db.get_state(board_path)["groups"][0]["type"] == "concern"

    handoff_id = db.submit_handoff(board_path, author="Maria", note="Please check dizziness first.")
    handoff = db.get_state(board_path)["handoff"]
    assert handoff["id"] == handoff_id
    assert [entry["title"] for entry in handoff["snapshot"]["completed"]] == ["8:00 PM medication"]
    assert {entry["title"] for entry in handoff["snapshot"]["changed"]} == {"Dizziness", "Watch for a fall"}
    dizziness_snapshot = next(entry for entry in handoff["snapshot"]["changed"] if entry["title"] == "Dizziness")
    assert (dizziness_snapshot["from_value"], dizziness_snapshot["value"]) == ("mild", "moderate")
    assert any(item["title"] == "Cardiology appointment tomorrow"
               for item in handoff["snapshot"]["attention"])

    with pytest.raises(db.BoardError, match="already awaiting"):
        db.submit_handoff(board_path, author="Alex", note="Overlapping shift")
    with pytest.raises(db.BoardError, match="Another caregiver"):
        db.acknowledge_handoff(board_path, handoff_id, author="Maria")

    db.create_entry(board_path, kind="task", title="Bring water", details="", severity="low",
                    status="open", value="", author="Maria")
    db.acknowledge_handoff(board_path, handoff_id, author="Alex")
    assert all(item["title"] != "Bring water" for item in db.get_state(board_path)["handoff"]["snapshot"]["attention"])
    with pytest.raises(db.BoardError, match="no longer awaiting"):
        db.acknowledge_handoff(board_path, handoff_id, author="Alex")

    db.submit_handoff(board_path, author="Alex", note="A new note")
    next_snapshot = db.get_state(board_path)["handoff"]["snapshot"]
    assert [item["title"] for item in next_snapshot["changed"]] == ["Bring water"]
    assert next_snapshot["completed"] == []


def test_repeated_updates_keep_first_shift_value(board_path):
    dizziness = next(item for group in db.get_state(board_path)["groups"] for item in group["items"]
                     if item["title"] == "Dizziness")
    for value in ("moderate", "severe"):
        db.update_entry(board_path, dizziness["id"], details="", severity="high",
                        status="open", value=value, author="Maria")
    db.submit_handoff(board_path, author="Maria", note="Trend")
    changed = db.get_state(board_path)["handoff"]["snapshot"]["changed"]
    assert (changed[0]["from_value"], changed[0]["value"]) == ("mild", "severe")


def test_invalid_data_and_noop_update_do_not_change_revision(board_path):
    entry = db.get_state(board_path)["groups"][0]["items"][0]
    revision = db.get_revision(board_path)
    with pytest.raises(db.BoardError):
        db.create_entry(board_path, kind="invalid", title="Test", details="", severity="low",
                        status="open", value="", author="Alex")
    assert db.update_entry(board_path, entry["id"], details=entry["details"],
                           severity=entry["severity"], status=entry["status"],
                           value=entry["value"], author="Alex") is False
    assert db.get_revision(board_path) == revision


def test_explicit_reset_restores_only_fictional_starting_state(board_path):
    db.submit_handoff(board_path, author="Maria", note="First shift")
    db.reset_demo(board_path)
    state = db.get_state(board_path)
    assert state["handoff"] is None
    assert state["open_count"] == 3
    db.submit_handoff(board_path, author="Maria", note="Fresh start")
    assert db.get_state(board_path)["handoff"]["snapshot"]["changed"] == []
