"""The session's stage font: session.yaml → font, its CSS, the file route, readiness.

The font is a copy of the vendored Patrick Hand under another name — no real
font file from this PC is ever used.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.sessions.model import THEME_FONT, Font, parse_session
from src.sessions.theme import FAMILY, font_css

VENDORED = Path(__file__).resolve().parent.parent / "app" / "webapp" / "static" / "fonts" / "PatrickHand-Regular.ttf"


@pytest.fixture
def font(tmp_path: Path) -> Path:
    return Path(shutil.copy(VENDORED, tmp_path / "Synthetic-Regular.ttf"))


def _session_with(client, font_block: dict | None, folder: str = "font") -> tuple[str, dict]:
    sid = client.post("/api/sessions", json={"title": "Font", "folder": folder}).json()["id"]
    session = client.get(f"/api/sessions/{sid}").json()["session"]
    if font_block is not None:
        session["font"] = font_block
    assert client.put(f"/api/sessions/{sid}", json={"session": session}).status_code == 200
    return sid, session


def test_font_block_parses_and_bounds_the_thickness(font: Path) -> None:
    s = parse_session({"title": "x", "font": {"file": str(font), "stroke_px": 1.5}})
    assert s.font is not None and s.font.file == str(font) and s.font.stroke_px == 1.5
    with pytest.raises(ValidationError):
        parse_session({"title": "x", "font": {"stroke_px": 9}})
    assert parse_session({"title": "x"}).font is None


def test_question_font_patrick_hand_means_the_theme_font() -> None:
    # The editor offered "Patrick Hand" as "the session theme": it follows the session font now.
    assert Font(family="Patrick Hand").family == THEME_FONT
    assert Font().family == THEME_FONT
    assert Font(family="Georgia").family == "Georgia"


def test_font_css(font: Path) -> None:
    css = font_css(parse_session({"title": "x", "font": {"file": str(font), "stroke_px": 1.25}}), "/f")
    assert f'font-family: "{FAMILY}"' in css and 'format("truetype")' in css and "/f?v=" in css
    assert f'--st-font: "{FAMILY}", "Patrick Hand"' in css and "--st-font-stroke: 1.25px;" in css
    # thickness alone keeps the theme's font
    only = font_css(parse_session({"title": "x", "font": {"stroke_px": 2}}), "/f")
    assert "@font-face" not in only and "--st-font:" not in only and "--st-font-stroke: 2px;" in only
    # a missing or non-font file is ignored (the stage falls back), never served
    assert font_css(parse_session({"title": "x", "font": {"file": str(font.with_name("gone.otf"))}}), "/f") == ""
    txt = font.with_name("notes.txt")
    txt.write_text("x", encoding="utf-8")
    assert font_css(parse_session({"title": "x", "font": {"file": str(txt)}}), "/f") == ""


def test_theme_and_font_routes(client, font: Path) -> None:
    sid, _ = _session_with(client, {"file": str(font), "stroke_px": 1.5})
    css = client.get(f"/api/sessions/{sid}/theme.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
    assert f"/api/sessions/{sid}/font?v=" in css.text and "--st-font-stroke: 1.5px;" in css.text
    got = client.get(f"/api/sessions/{sid}/font")
    assert got.status_code == 200 and got.headers["content-type"] == "font/ttf" and got.content == font.read_bytes()

    # the session's own theme.css comes after the font, so it can still override it
    folder = Path(client.get(f"/api/sessions/{sid}").json()["folder"]["path"])
    (folder / "theme.css").write_text(".stage-canvas { --st-c1: #123456; }", encoding="utf-8")
    text = client.get(f"/api/sessions/{sid}/theme.css").text
    assert text.index("--st-font-stroke") < text.index("--st-c1")

    # the live stage gets the same CSS once the session is live
    assert client.get("/api/live/theme.css").text == ""
    assert client.post("/api/live/activate", json={"session": sid}).status_code == 200
    assert client.get("/api/live/theme.css").text == text


def test_font_route_refuses_without_a_font(client, font: Path) -> None:
    sid, session = _session_with(client, None)
    assert client.get(f"/api/sessions/{sid}/font").status_code == 404
    session["font"] = {"file": str(font.with_name("missing.otf"))}
    client.put(f"/api/sessions/{sid}", json={"session": session})
    assert client.get(f"/api/sessions/{sid}/font").json()["error"]["code"] == "font_not_found"


def test_readiness_names_the_font(client, font: Path) -> None:
    sid, session = _session_with(client, {"file": str(font)})
    check = next(c for c in client.get(f"/api/sessions/{sid}").json()["readiness"] if c["key"] == "font")
    assert (check["state"], check["detail"]) == ("ok", "Synthetic-Regular.ttf")
    font.unlink()
    check = next(c for c in client.get(f"/api/sessions/{sid}").json()["readiness"] if c["key"] == "font")
    assert check["state"] == "warn" and "not on this PC" in check["detail"]
    # no font set → no check at all
    sid2, _ = _session_with(client, None, folder="plain")
    assert all(c["key"] != "font" for c in client.get(f"/api/sessions/{sid2}").json()["readiness"])


def test_picker_accepts_fonts(client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.webapp.routers import slides

    monkeypatch.setattr(slides, "native_pick", lambda kind: f"C:/picked.{kind}")
    assert client.post("/api/pick", json={"kind": "font"}).json() == {"path": "C:/picked.font"}
