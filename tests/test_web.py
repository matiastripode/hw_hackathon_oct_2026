import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "board.db")) as test_client:
        yield test_client


def login(client, name="Maria"):
    return client.post("/login", data={"caregiver": name, "password": "anything"},
                       follow_redirects=False)


def test_login_and_caregiver_switch(client):
    assert client.get("/board", follow_redirects=False).headers["location"] == "/login"
    assert "no real authentication" in client.get("/login").text
    assert login(client).status_code == 303
    assert "On shift as" in client.get("/board").text
    assert "Maria" in client.get("/board").text
    assert client.post("/logout", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/board", follow_redirects=False).status_code == 303


def test_post_handoff_acknowledge_and_escaping(client):
    login(client)
    response = client.post("/entries", data={
        "type": "concern", "title": "<script>alert(1)</script>", "details": "Check tomorrow",
        "severity": "high", "status": "open", "value": "",
    }, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/board/fragment").text
    assert "&lt;script&gt;" in page
    assert "<script>alert(1)</script>" not in page
    response = client.post("/handoffs", data={"note": "Check this first"}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/board").text
    assert "Needs acknowledgement" in page
    assert client.post("/handoffs", data={"note": "Again"}, follow_redirects=False).status_code == 409
    assert client.post("/handoffs/1/acknowledge", data={}, follow_redirects=False).status_code == 409
    login(client, "Alex")
    assert client.post("/handoffs/1/acknowledge", data={}, follow_redirects=False).status_code == 303
    assert "Acknowledged by Alex" in client.get("/board").text


def test_invalid_login_and_entry(client):
    assert client.post("/login", data={"caregiver": "other", "name": ""}).status_code == 400
    login(client)
    assert client.post("/entries", data={"type": "bad", "title": "Something",
                                         "severity": "high", "status": "open"}).status_code == 400


def test_two_devices_keep_separate_caregiver_sessions(tmp_path):
    app = create_app(tmp_path / "two-devices.db")
    with TestClient(app) as maria, TestClient(app) as alex:
        login(maria, "Maria")
        login(alex, "Alex")
        assert maria.post("/entries", data={
            "type": "task", "title": "Check transportation", "details": "",
            "severity": "high", "status": "open", "value": "",
        }).status_code == 200
        assert "Check transportation" in alex.get("/board/fragment").text
        assert maria.post("/handoffs", data={"note": "Please confirm the ride."}).status_code == 200
        assert "From Maria" in alex.get("/board").text
        assert alex.post("/handoffs/1/acknowledge", data={}).status_code == 200
        assert "Acknowledged by Alex" in maria.get("/board").text


def test_fragment_revision_changes_only_after_board_updates(client):
    login(client)
    initial = client.get("/board/fragment").text
    assert 'data-board-revision="1"' in initial
    assert 'data-board-revision="1"' in client.get("/board/fragment").text

    client.post("/entries", data={
        "type": "concern", "title": "Watch dizziness", "details": "",
        "severity": "high", "status": "open", "value": "",
    })
    assert 'data-board-revision="2"' in client.get("/board/fragment").text


def test_board_has_mobile_actions_and_persistent_update_notice(client):
    login(client)
    page = client.get("/board").text
    assert 'href="#add-title"' in page
    assert 'href="#shift-title"' in page
    assert 'id="board-updates"' in page
    assert 'id="apply-board-updates"' in page
    assert page.index('id="board-updates"') < page.index('data-board-content')
