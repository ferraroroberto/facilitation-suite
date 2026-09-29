"""Settings → Stage defaults and the stage library (#110): what new sessions start from,
existing sessions keep their own look, "Reset to default", and the library's own folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src import defaults
from src.config import PROJECT_ROOT, load_config
from src.sessions.store import SESSION_FILE
from tests.fixtures.demo import FONT, build_demo_session

LETTERING = {"family": "Georgia", "weight": 700, "caps": False, "roles": {"answers": {"caps": True}}}


def _yaml(folder: Path) -> dict:
    return yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))


def _new(client: TestClient, title: str) -> tuple[str, Path]:
    r = client.post("/api/sessions", json={"title": title, "workshop": "demo"})
    assert r.status_code == 201, r.text
    return r.json()["id"], Path(r.json()["path"])


def _synthetic_font(tmp: Path, name: str = "Synthetic-Hand.ttf") -> Path:
    """A synthetic font file: a renamed copy of the vendored test font, never a user's font."""
    return Path(shutil.copy(FONT, tmp / name))


def test_new_sessions_copy_the_defaults_and_existing_ones_keep_theirs(client: TestClient, isolated_env: Path) -> None:
    before_sid, before = _new(client, "Before")
    assert "font" not in _yaml(before) and _yaml(before)["theme"] == "default"
    assert client.get(f"/api/sessions/{before_sid}").json()["look"]["uses_defaults"] is True

    r = client.put("/api/settings/defaults", json={"stage": {"font": LETTERING}, "music": {"fade_in_s": 4, "fade_out_s": 6.5}})
    assert r.status_code == 200, r.text
    assert r.json()["stage"]["font"]["family"] == "Georgia" and r.json()["music"] == {"fade_in_s": 4.0, "fade_out_s": 6.5}
    saved = json.loads((isolated_env / "config.json").read_text(encoding="utf-8"))
    assert saved["defaults"]["stage"]["font"]["weight"] == 700 and saved["obs"]["enabled"] is False  # the rest is kept

    # an existing session keeps its own look, and now says it differs from the defaults
    assert "font" not in _yaml(before)
    assert client.get(f"/api/sessions/{before_sid}").json()["look"]["uses_defaults"] is False

    after_sid, after = _new(client, "After")
    font = _yaml(after)["font"]
    assert font["family"] == "Georgia" and font["weight"] == 700 and font["caps"] is False
    assert font["roles"] == {"answers": {"caps": True}}
    assert client.get(f"/api/sessions/{after_sid}").json()["look"]["uses_defaults"] is True

    # the new session owns its copy: a later default leaves it alone
    client.put("/api/settings/defaults", json={"stage": {"font": None}})
    assert _yaml(after)["font"]["family"] == "Georgia"
    assert client.get(f"/api/sessions/{after_sid}").json()["look"]["uses_defaults"] is False


def test_reset_to_default_applies_the_current_defaults(client: TestClient, isolated_env: Path) -> None:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "reset", isolated_env / "sessions.local.yaml")
    raw = _yaml(folder)
    raw["font"] = {"family": "Segoe Print", "stroke_px": 2}
    item = raw["sections"][0]["items"][0]
    item["font"] = {"family": "Georgia", "size_px": 90}  # an item's own exception
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    client.put("/api/settings/defaults", json={"stage": {"font": LETTERING}})
    assert client.get(f"/api/sessions/{sid}").json()["look"]["uses_defaults"] is False

    r = client.post(f"/api/sessions/{sid}/look/reset")
    assert r.status_code == 200 and r.json()["look"]["uses_defaults"] is True
    now = _yaml(folder)
    assert now["font"]["family"] == "Georgia" and now["font"]["weight"] == 700
    assert now["font"].get("stroke_px", 0) == 0  # the session's own thickness is gone
    assert now["sections"][0]["items"][0]["font"]["size_px"] == 90  # items keep their own

    # back to the theme's lettering: the session's block goes too
    client.put("/api/settings/defaults", json={"stage": {"font": None}})
    client.post(f"/api/sessions/{sid}/look/reset")
    assert "font" not in _yaml(folder)


def test_the_defaults_are_checked_and_changed_only_on_this_pc(client: TestClient, isolated_env: Path) -> None:
    assert client.put("/api/settings/defaults", json={"stage": {"theme": "nope"}}).status_code == 422
    assert client.put("/api/settings/defaults", json={"stage": {"font": {"weight": 500}}}).status_code == 422
    assert client.put("/api/settings/defaults", json={"music": {"fade_in_s": 61}}).status_code == 422
    # an all-default lettering block is the theme's own: stored as none
    assert client.put("/api/settings/defaults", json={"stage": {"font": {"caps": True, "roles": {}}}}).json()["stage"]["font"] is None
    from app.webapp.server import create_app

    with TestClient(create_app(), client=("100.64.0.9", 5000)) as phone:
        assert phone.put("/api/settings/defaults", json={"music": {"fade_in_s": 1}}).status_code in (401, 403)


def test_a_bad_default_in_the_file_falls_back(isolated_env: Path) -> None:
    cfg_path = isolated_env / "config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["defaults"] = {"stage": {"theme": 3, "font": {"weight": 12}}, "music": {"fade_in_s": "slow", "fade_out_s": 3}}
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")
    d = defaults.load(load_config())
    assert d == defaults.Defaults(theme="default", font=None, fade_in_s=2.0, fade_out_s=3.0)


def test_the_library_lives_next_to_the_sessions_never_in_the_repo(client: TestClient, isolated_env: Path, tmp_path: Path) -> None:
    font = _synthetic_font(tmp_path)
    r = client.post("/api/settings/library/font", json={"path": str(font)})
    assert r.status_code == 200, r.text
    lib = Path(r.json()["library"]["path"])
    assert lib == isolated_env / "sessions" / "_library"
    assert PROJECT_ROOT not in lib.parents
    added = Path(r.json()["added"]["path"])
    assert added == lib / "fonts" / "Synthetic-Hand.ttf" and added.read_bytes() == font.read_bytes()
    # the same file again is reused; another with the same name becomes -2
    assert client.post("/api/settings/library/font", json={"path": str(font)}).json()["added"]["path"] == str(added)
    other = tmp_path / "other"
    other.mkdir()
    twin = other / "Synthetic-Hand.ttf"
    twin.write_bytes(font.read_bytes() + b"\0")
    assert client.post("/api/settings/library/font", json={"path": str(twin)}).json()["added"]["name"] == "Synthetic-Hand-2.ttf"
    assert [f["name"] for f in client.get("/api/settings/defaults").json()["library"]["fonts"]] == ["Synthetic-Hand-2.ttf", "Synthetic-Hand.ttf"]
    # not a font, or not there
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert client.post("/api/settings/library/font", json={"path": str(tmp_path / "notes.txt")}).status_code == 422
    assert client.post("/api/settings/library/font", json={"path": str(tmp_path / "gone.ttf")}).status_code == 404

    # a library font as the default title font: new sessions name the library's copy
    client.put("/api/settings/defaults", json={"stage": {"font": {"file": str(added)}}})
    css = client.get("/api/settings/defaults/font.css").text
    assert '"Default Font"' in css and ".stage-canvas.defaults-sample {" in css
    assert "--st-sub-case: none;" in css  # every variable: a session's lettering never shows through
    assert client.get("/api/settings/defaults/font").content == font.read_bytes()
    _, folder = _new(client, "With the library font")
    assert _yaml(folder)["font"]["file"] == str(added)


def test_a_library_theme_is_the_default_and_reaches_the_stage_css(client: TestClient, isolated_env: Path, tmp_path: Path) -> None:
    css_file = tmp_path / "Ocean.css"
    css_file.write_text(".stage-canvas { --st-bg: #dfefff; }\n", encoding="utf-8")
    r = client.post("/api/settings/library/theme", json={"path": str(css_file)})
    assert r.status_code == 200
    themes = {t["value"]: t["source"] for t in r.json()["library"]["themes"]}
    assert themes == {"default": "repo", "library/Ocean": "library"}
    assert client.put("/api/settings/defaults", json={"stage": {"theme": "library/Ocean"}}).status_code == 200
    sid, folder = _new(client, "Ocean session")
    assert _yaml(folder)["theme"] == "library/Ocean"
    assert "--st-bg: #dfefff" in client.get(f"/api/sessions/{sid}/theme.css").text
    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    assert "--st-bg: #dfefff" in client.get("/api/live/theme.css").text
    # a session whose theme the default no longer names keeps it
    client.put("/api/settings/defaults", json={"stage": {"theme": "default"}})
    assert _yaml(folder)["theme"] == "library/Ocean"
