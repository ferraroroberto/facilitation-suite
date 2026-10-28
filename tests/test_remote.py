"""The phone remote's gate (step 14): this PC always, other devices with the token."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.webapp.errors import is_local
from tests.conftest import write_test_config

PHONE = ("100.64.0.9", 5000)


@pytest.fixture
def app(isolated_env: Path, monkeypatch: pytest.MonkeyPatch):
    from app.webapp.routers import settings as settings_router
    from app.webapp.server import create_app

    monkeypatch.setattr(settings_router, "cert_hostname", lambda: "pc.example.ts.net")
    return create_app()


def _token(app) -> str:
    with TestClient(app, client=("127.0.0.1", 50000), base_url="https://127.0.0.1:8449") as pc:
        body = pc.post("/api/settings/remote/token").json()["remote"]
    assert body["enabled"] and body["https"] and body["link"].startswith("https://pc.example.ts.net:")
    return body["link"].split("token=")[1]


def test_this_pc_is_loopback_or_its_own_address() -> None:
    assert is_local({"client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8449)})
    assert is_local({"client": ("100.107.0.1", 1), "server": ("100.107.0.1", 8449)})  # the PC via its tailnet name
    assert not is_local({"client": ("100.64.0.9", 1), "server": ("100.107.0.1", 8449)})
    assert not is_local({"client": None, "server": None})


def test_with_the_remote_off_other_devices_only_reach_the_open_pages(app) -> None:
    with TestClient(app, client=PHONE) as phone:
        r = phone.get("/api/live")
        assert r.status_code == 401 and r.json()["error"]["code"] == "remote_off"
        assert phone.get("/remote").status_code == 200  # tells the phone how to pair
        assert phone.get("/healthz").status_code == 200
        assert phone.get("/static/js/remote.js").status_code == 200
        assert phone.get("/").status_code == 401
        assert phone.post("/api/actions/next").status_code == 401
        with pytest.raises(WebSocketDisconnect) as closed, phone.websocket_connect("/ws?role=remote"):
            pass
        assert closed.value.code == 4401


def test_the_pairing_link_pairs_the_phone_by_cookie(app) -> None:
    token = _token(app)
    with TestClient(app, client=PHONE, base_url="https://pc.example.ts.net:8449") as phone:
        assert phone.get("/api/live?token=nope").json()["error"]["code"] == "remote_token_required"
        r = phone.get(f"/remote?token={token}")
        cookie = r.headers["set-cookie"]
        assert "fs_remote=" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
        # from now on the cookie alone is enough — pages, API and the WebSocket
        assert phone.get("/api/live").status_code == 200
        assert phone.get("/").status_code == 200
        with phone.websocket_connect("/ws?role=remote", headers={"cookie": f"fs_remote={token}"}) as ws:  # as a browser sends it
            assert ws.receive_json()["type"] in ("plan", "state")
    with TestClient(app, client=PHONE) as other:
        assert other.get("/api/live", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert other.get("/api/live", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_the_phone_cannot_manage_the_remote_or_the_pc(app) -> None:
    token = _token(app)
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(app, client=PHONE, base_url="https://pc.example.ts.net:8449") as phone:
        body = phone.get("/api/settings", headers=auth).json()["remote"]
        assert body["enabled"] and body["link"] is None  # the link is shown on the PC only
        assert phone.post("/api/settings/remote/token", headers=auth).json()["error"]["code"] == "local_only"
        assert phone.post("/api/pick", json={"kind": "pptx"}, headers=auth).json()["error"]["code"] == "local_only"
        quiz = phone.post("/api/sessions/any/quiz-import", json={"path": "C:/quiz.xlsx"}, headers=auth)
        assert quiz.json()["error"]["code"] == "local_only"
        assert phone.post("/api/chat/messages", json={"messages": []}, headers=auth).json()["error"]["code"] == "local_only"


def test_a_page_of_another_site_cannot_act_on_this_pc(app) -> None:
    own = {"origin": "https://127.0.0.1:8449"}
    other = {"origin": "https://elsewhere.example"}
    with TestClient(app, client=("127.0.0.1", 50000), base_url="https://127.0.0.1:8449") as pc:
        refused = pc.post("/api/actions/blackout", headers=other)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "foreign_origin"
        assert pc.post("/api/actions/blackout", headers={"origin": "null"}).status_code == 403
        # the app's own pages, and callers that are not a browser page (Stream Deck, scripts), still act
        assert pc.post("/api/actions/blackout", headers=own).json()["error"]["code"] == "not_live"
        assert pc.post("/api/actions/blackout").json()["error"]["code"] == "not_live"
        assert pc.get("/api/live", headers=other).status_code == 200  # a read the browser never hands to that page
        with pytest.raises(WebSocketDisconnect) as closed, pc.websocket_connect("/ws?role=app", headers=other):
            pass
        assert closed.value.code == 4403
        # the test client always says "testserver" for a socket; a browser sends the address it opened
        with pc.websocket_connect("/ws?role=app", headers={**own, "host": "127.0.0.1:8449"}) as ws:
            assert ws.receive_json()["type"] in ("plan", "state")


def test_a_new_link_unpairs_and_off_locks_out(app) -> None:
    old = _token(app)
    new = _token(app)
    assert old != new
    with TestClient(app, client=PHONE) as phone:
        assert phone.get("/api/live", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert phone.get("/api/live", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        assert pc.delete("/api/settings/remote/token").json()["remote"] == {**pc.get("/api/settings").json()["remote"], "enabled": False}
    with TestClient(app, client=PHONE) as phone:
        r = phone.get("/api/live", headers={"Authorization": f"Bearer {new}"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "remote_off"


def test_no_https_no_link(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.webapp.routers import settings as settings_router
    from app.webapp.server import create_app

    monkeypatch.setattr(settings_router, "cert_hostname", lambda: "pc.example.ts.net")  # a cert on disk…
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as pc:  # …but this instance serves plain HTTP
        body = pc.post("/api/settings/remote/token").json()["remote"]
        assert body["enabled"] and body["https"] is False and body["link"] is None
        assert body["base_url"].startswith("http://127.0.0.1:")


def test_loopback_bind_offers_no_pairing_link(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With ``host: 127.0.0.1`` a phone can never reach the listener, however good the cert on
    disk — the payload must say the card is unavailable instead of handing out a dead tailnet
    link (#101), the same bind check ``WebappManager.public_url`` already makes (#39)."""
    from app.webapp.routers import settings as settings_router
    from app.webapp.server import create_app

    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), host="127.0.0.1")
    monkeypatch.setattr(settings_router, "cert_hostname", lambda: "pc.example.ts.net")  # a cert exists on disk…
    with TestClient(create_app(), client=("127.0.0.1", 50000), base_url="https://127.0.0.1:8449") as pc:  # …and this instance answers over https
        body = pc.post("/api/settings/remote/token").json()["remote"]
        assert body["enabled"] is True
        assert body["bind_loopback"] is True
        assert body["link"] is None
        assert "ts.net" not in body["base_url"]


def test_the_request_log_never_shows_the_token() -> None:
    import logging

    from app.webapp.auth import RedactTokens

    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("100.64.0.9:5000", "GET", "/remote?token=s3cret-Value_1&x=1", "1.1", 200), None)
    RedactTokens().filter(rec)
    assert "s3cret" not in rec.getMessage() and "/remote?token=<redacted>&x=1" in rec.getMessage()
    assert isinstance(rec.args, tuple) and len(rec.args) == 5  # uvicorn's access formatter unpacks all five
    assert rec.args[2] == "/remote?token=<redacted>&x=1"


def test_the_local_page_still_offers_the_tailnet_pairing_link(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A page on the local listener (plain HTTP on this PC) builds the link for the phone: the
    tailnet HTTPS URL on the main port, never the page's own ``http://127.0.0.1:<local_port>``."""
    from app.webapp.routers import settings as settings_router
    from app.webapp.server import create_app

    with socket.socket() as probe:  # a free port: the listener really binds it, never the live :8456
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(settings_router, "cert_hostname", lambda: "pc.example.ts.net")
    write_test_config(isolated_env / "config.json", session_root=str(isolated_env / "sessions"), local_port=port)
    with TestClient(create_app(), client=("127.0.0.1", 50000), base_url=f"http://127.0.0.1:{port}") as pc:
        body = pc.post("/api/settings/remote/token").json()["remote"]
        assert body["https"] is True
        assert body["base_url"] == "https://pc.example.ts.net:8449"
        assert body["link"].startswith("https://pc.example.ts.net:8449/remote?token=")
    # A plain-HTTP page on any other port is still not given an https link.
    with TestClient(create_app(), client=("127.0.0.1", 50000), base_url="http://127.0.0.1:9999") as dev:
        body = dev.get("/api/settings").json()["remote"]
        assert body["https"] is False and body["link"] is None
