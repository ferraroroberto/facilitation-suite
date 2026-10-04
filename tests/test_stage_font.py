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


def test_installed_family_weight_and_capitals() -> None:
    css = font_css(parse_session({"title": "x", "font": {"family": "Georgia", "weight": 700, "caps": False}}), "/f")
    assert '--st-font: "Georgia", "Patrick Hand"' in css and "--st-font-weight: 700;" in css
    assert "--st-question-transform: none;" in css and "@font-face" not in css
    # the defaults write nothing; a hostile family name cannot break out of the rule
    assert font_css(parse_session({"title": "x", "font": {}}), "/f") == ""
    evil = font_css(parse_session({"title": "x", "font": {"family": 'x"; } body { color: red'}}), "/f")
    assert evil.count("{") == 1 and '"x  body  color: red"' in evil
    with pytest.raises(ValidationError):
        parse_session({"title": "x", "font": {"weight": 500}})


def test_a_font_file_wins_over_the_family(font: Path) -> None:
    css = font_css(parse_session({"title": "x", "font": {"file": str(font), "family": "Georgia"}}), "/f")
    assert f'--st-font: "{FAMILY}"' in css and "Georgia" not in css


def test_item_capitals_follow_the_session_by_default() -> None:
    assert Font().caps is None and Font(caps=False).caps is False


def test_text_font_and_each_kind_of_text() -> None:
    css = font_css(parse_session({"title": "x", "font": {
        "text_family": "Georgia", "text_weight": 700,
        "roles": {"answers": {"caps": True}, "hint": {"font": "title", "caps": True},
                  "slide_text": {"font": "Segoe Print"}, "title": {"font": "text", "caps": False}, "typo": {"caps": True}},
    }}), "/f")
    assert '--st-text-font: "Georgia", var(--st-ui-font);' in css and "--st-text-weight: 700;" in css
    # the word cloud in capitals, still in the text font
    assert "--st-answers-case: uppercase;" in css and "--st-answers-font" not in css
    # the chat hint in the title font, in capitals
    assert "--st-hint-font: var(--st-font);" in css and "--st-hint-stroke: var(--st-font-stroke);" in css
    assert "--st-hint-case: uppercase;" in css
    # a family of its own for the slide text, on top of the text font
    assert '--st-slide-font: "Segoe Print", var(--st-text-font);' in css and "--st-slide-stroke: 0px;" in css
    # titles in the text font; their capitals stay font.caps (true here), never the role's
    assert "--st-title-font: var(--st-text-font);" in css and "--st-title-case" not in css
    assert "typo" not in css


def test_item_lettering_keeps_only_known_kinds_and_saves_quietly() -> None:
    from src.sessions.model import dump_session

    s = parse_session({"title": "x", "sections": [{"items": [
        {"kind": "activity", "type": "word_cloud", "font": {"roles": {"answers": {"caps": True}, "nope": {"caps": True}}}},
        {"kind": "slide", "font": {"family": "Georgia"}},
    ]}]})
    first, second = s.sections[0].items
    assert list(first.font.roles) == ["answers"] and first.font.roles["answers"].caps is True
    fonts = [it.get("font") for it in dump_session(s)["sections"][0]["items"]]
    assert fonts[0]["roles"] == {"answers": {"caps": True}}
    assert "roles" not in fonts[1]  # no exceptions: nothing extra in session.yaml


def test_title_colour_and_size_are_checked_and_become_theme_variables() -> None:
    """#191: font.title_color / title_size — a #rrggbb colour and a size in stage px."""
    from src.defaults import normal_font
    from src.sessions.theme import lettering_css

    s = parse_session({"title": "x", "font": {"title_color": " #C62828 ", "title_size": 96}})
    assert s.font is not None and s.font.title_color == "#c62828" and s.font.title_size == 96
    css = font_css(s, "/f")
    assert "--st-title-color: #c62828;" in css and "--st-title-size: 96px;" in css
    assert "--st-title-color" not in font_css(parse_session({"title": "x", "font": {"stroke_px": 1}}), "/f")  # says nothing: the theme's
    for bad in ("red", "#fff", "#12345g", "rgb(1,2,3)"):
        with pytest.raises(ValidationError):
            parse_session({"title": "x", "font": {"title_color": bad}})
    with pytest.raises(ValidationError):
        parse_session({"title": "x", "font": {"title_size": 241}})
    # the Settings sample states every variable, so a session's colour never shows through it
    sample = lettering_css(None, "/f", scope=".font-sample", complete=True)
    assert "--st-title-color: var(--st-ink);" in sample and "--st-title-size: 72px;" in sample
    # a colour or size is a real difference from the theme (a default block saves as nothing)
    assert normal_font(s.font) is not None and normal_font(parse_session({"title": "x", "font": {"title_size": 0}}).font) is None


def test_timer_colours_are_checked_and_become_theme_variables() -> None:
    """#211: font.timer_idle / _running / _paused / _done — a #rrggbb colour each, with an ink that reads on it."""
    from src.defaults import normal_font
    from src.sessions.theme import TIMER_DEFAULTS, lettering_css, timer_ink

    s = parse_session({"title": "x", "font": {"timer_running": " #1565C0 ", "timer_paused": "#fafafa"}})
    assert s.font is not None and s.font.timer_running == "#1565c0" and s.font.timer_idle == ""
    css = font_css(s, "/f")
    # a pale fill gets dark ink, a saturated one white
    assert "--st-timer-running: #1565c0; --st-timer-running-ink: #ffffff;" in css
    assert "--st-timer-paused: #fafafa; --st-timer-paused-ink: #1f1f1f;" in css
    assert "--st-timer-idle" not in css and "--st-timer-done" not in css  # says nothing: the theme's
    for bad in ("red", "#fff", "#12345g", "rgb(1,2,3)"):
        with pytest.raises(ValidationError):
            parse_session({"title": "x", "font": {"timer_done": bad}})
    # the Settings sample states all four, so a session's colour never shows through it
    sample = lettering_css(None, "/f", scope=".font-sample", complete=True)
    for state, colour in TIMER_DEFAULTS.items():
        assert f"--st-timer-{state}: {colour};" in sample and f"--st-timer-{state}-ink: {timer_ink(colour)};" in sample
    # a colour is a real difference from the theme (a default block saves as nothing)
    assert normal_font(s.font) is not None and normal_font(parse_session({"title": "x", "font": {"timer_idle": ""}}).font) is None


def test_timer_defaults_match_the_theme_and_their_inks_hold_contrast() -> None:
    """#211: the defaults are Roberto's four colours, the theme (themes/default.css) says the same, and the
    pill's ink is white where white holds 3:1 (large text) on the fill, else the dark ink."""
    from src.sessions.theme import TIMER_DEFAULTS, contrast, timer_ink

    assert TIMER_DEFAULTS == {"idle": "#1f1f1f", "running": "#00a44e", "paused": "#f2b705", "done": "#c40c0c"}
    css = (Path(__file__).resolve().parent.parent / "themes" / "default.css").read_text(encoding="utf-8")
    for state, colour in TIMER_DEFAULTS.items():
        assert f"--st-timer-{state}: {colour};" in css.lower(), state
        assert f"--st-timer-{state}-ink: {timer_ink(colour)};" in css.lower(), state
        assert contrast(timer_ink(colour), colour) >= 3, state
    assert [timer_ink(c) for c in TIMER_DEFAULTS.values()] == ["#ffffff", "#ffffff", "#1f1f1f", "#ffffff"]


def test_a_run_item_has_a_size_only_when_it_sets_one_or_is_not_an_activity() -> None:
    """#191: an activity's title size is the session's (font.title_size) unless the item sets its own;
    the other kinds keep their 72 px."""
    from src.live.plan import _item_font

    session = parse_session({"title": "x", "sections": [{"id": "s", "items": [
        {"kind": "activity", "id": "a", "type": "word_cloud"},
        {"kind": "activity", "id": "b", "type": "word_cloud", "font": {"size_px": 90}},
        {"kind": "break", "id": "c", "font": {"family": "theme"}}, {"kind": "break", "id": "d"}]}]})
    a, b, c, d = (it for sec in session.sections for it in sec.items)
    assert _item_font(a).get("size_px") is None and _item_font(b)["size_px"] == 90
    assert _item_font(c)["size_px"] == 72 and _item_font(d)["size_px"] == 72
