"""The usage log (#244): the privacy contract, the gate, the file's home, and the pages that must stay silent.

The component itself is vendored (``src/usage_log.py``, tested upstream in project-scaffolding); this pins
what is this app's: what lands on disk when a hostile envelope arrives, who may post, where the file goes,
that the stage / phone remote / quiz player never load the logger, and that control ids are constants.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import REPO_ROOT, paired_remote

STATIC = REPO_ROOT / "app" / "webapp" / "static"
STORED_COLUMNS = {"id", "ts", "session_id", "kind", "view_id", "control_id", "device_class", "os_class"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
SID = "11111111-2222-3333-4444-555555555555"
SECRETS = ("Ada Lovelace", "ada@example.com", "pass-the-salt", "10.1.2.3", "Mozilla/5.0", "/api/sessions?q=ada")


def _rows(data_dir: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(data_dir / "usage.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    finally:
        conn.close()


def _hostile_envelope() -> dict:
    """A well-formed batch plus every field a careless client could add; only the contract may survive."""
    return {
        "session_id": SID,
        "device_class": "desktop",
        "os_class": "windows",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0)",
        "ip": "10.1.2.3",
        "name": "Ada Lovelace",
        "events": [
            {"kind": "session_start", "text": "Ada Lovelace", "email": "ada@example.com"},
            {"kind": "view", "id": "plan", "url": "/api/sessions?q=ada"},
            {"kind": "tap", "id": "sessions.new", "view": "plan", "value": "pass-the-salt", "name": "Ada Lovelace"},
            {"kind": "tap", "id": "Ada Lovelace", "view": "plan"},  # element text is not an id: dropped
            {"kind": "tap", "id": "/api/sessions?q=ada", "view": "plan"},  # a URL with a query is not an id: dropped
            {"kind": "message", "id": "chat", "text": "pass-the-salt"},  # not a kind: dropped
        ],
    }


def test_the_stored_event_fields_are_only_the_contract(client: TestClient, isolated_env: Path) -> None:
    r = client.post("/api/usage/events", json=_hostile_envelope())
    assert r.status_code == 200 and r.json()["accepted"] == 3 and r.json()["rejected"] == 3

    data = isolated_env / "data"
    conn = sqlite3.connect(data / "usage.sqlite3")
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    finally:
        conn.close()
    # kind, view id, control id, session id, device class, timestamp (+ the vendored table's coarse os_class).
    assert columns == STORED_COLUMNS

    rows = _rows(data)
    assert [(x["kind"], x["view_id"], x["control_id"]) for x in rows] == [
        ("session_start", None, None),
        ("view", "plan", None),
        ("tap", "plan", "sessions.new"),
    ]
    assert {x["session_id"] for x in rows} == {SID}
    assert {(x["device_class"], x["os_class"]) for x in rows} == {("desktop", "windows")}
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*Z?", x["ts"]) for x in rows)

    # Nothing from the hostile fields reached the file, in any column or free page.
    # Close the WAL into the main file first, so the byte scan reads everything.
    conn = sqlite3.connect(data / "usage.sqlite3")
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    blob = b"".join(p.read_bytes() for p in data.glob("usage.sqlite3*"))
    for secret in SECRETS:
        assert secret.encode() not in blob, secret


def test_the_file_lives_in_the_data_dir_and_nowhere_else(client: TestClient, isolated_env: Path) -> None:
    client.post("/api/usage/events", json={"session_id": SID, "device_class": "phone", "os_class": "ios",
                                          "events": [{"kind": "session_start"}]})
    assert (isolated_env / "data" / "usage.sqlite3").is_file()
    assert not list(REPO_ROOT.glob("*.sqlite3")) and not list((REPO_ROOT / "app").rglob("*.sqlite3"))


def test_another_device_needs_the_phone_token(isolated_env: Path) -> None:
    from app.webapp.server import create_app

    app = create_app()
    body = {"session_id": SID, "device_class": "phone", "os_class": "ios", "events": [{"kind": "session_start"}]}
    with TestClient(app, client=("100.64.0.9", 5000)) as stranger:
        assert stranger.post("/api/usage/events", json=body).status_code == 401
        assert stranger.get("/api/usage/summary").status_code == 401
    with paired_remote(app) as phone:
        assert phone.post("/api/usage/events", json=body).status_code == 200
    with TestClient(app, client=("127.0.0.1", 50000)) as pc:
        assert pc.get("/api/usage/summary").status_code == 200


def test_the_quiz_player_app_has_no_usage_route(client: TestClient) -> None:
    """The public app (the only surface the tunnel publishes) mounts nothing of the usage log."""
    from app.player.app import create_player_app

    player = create_player_app(client.app.state.quiz)
    with TestClient(player) as public:
        assert public.post("/api/usage/events", json={}).status_code == 404
        assert public.get("/api/usage/summary").status_code == 404
    assert not any("usage" in getattr(r, "path", "") for r in player.routes)


def test_the_stage_the_remote_and_the_player_never_load_the_logger() -> None:
    """Stage, phone remote and quiz player: no import of the component, no file of theirs reaches it."""
    silent = [
        STATIC / "stage.html", STATIC / "remote.html",
        STATIC / "js" / "stage.js", STATIC / "js" / "remote.js",
        STATIC / "js" / "stage-render.js", STATIC / "js" / "stage-words.js", STATIC / "js" / "live.js",
        STATIC / "js" / "ui.js", STATIC / "js" / "lettering.js",
        *sorted((REPO_ROOT / "app" / "player").rglob("*.html")),
        *sorted((REPO_ROOT / "app" / "player" / "static").rglob("*.js")),
    ]
    assert len(silent) > 8
    for path in silent:
        text = path.read_text(encoding="utf-8")
        assert "usage-log" not in text and "initUsageLog" not in text and "/api/usage" not in text, path.name

    loaders = [p.relative_to(STATIC).as_posix() for p in [*(STATIC / "js").rglob("*.js"), *STATIC.glob("*.html")]
               if "usage-log/usage-log.js" in p.read_text(encoding="utf-8")]
    assert sorted(loaders) == ["js/app.js", "js/presenter.js"]


def test_control_ids_are_constants_within_the_bounded_pattern() -> None:
    """Never built from element text or data: a literal id in markup, valid for both sides of the contract."""
    seen: set[str] = set()
    for path in [*(STATIC / "js").rglob("*.js"), *STATIC.glob("*.html")]:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""data-track=(?:"([^"]*)"|'([^']*)')""", text):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            assert "${" not in value, f"{path.name}: data-track built at run time: {value}"
            assert ID_PATTERN.fullmatch(value), f"{path.name}: {value!r}"
            seen.add(value)
        for match in re.finditer(r"""setAttribute\('data-track', '([^']*)'\)""", text):
            assert ID_PATTERN.fullmatch(match.group(1)), match.group(1)
            seen.add(match.group(1))
        assert not re.search(r"""dataset\.track\s*=\s*[^'"\s]""", text), f"{path.name}: dataset.track from a variable"
    assert {"sessions.new", "plan.save", "groups.shuffle", "results.export-pdf", "live.next", "live.prev"} <= seen
