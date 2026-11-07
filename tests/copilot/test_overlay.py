"""The overlay: what it draws, where it sits, and when it shows what (no window needed)."""

from __future__ import annotations

from app.tray.overlay.__main__ import socket_url
from app.tray.overlay.behaviour import FADE_IN_S, FADE_OUT_S, Behaviour
from app.tray.overlay.render import Style, frame, premultiplied_bgra, render_cue, render_hints, sample_hints
from app.tray.overlay.screens import Screen, pick, place

HINTS = [{"type": "next_q", "text": "Ask which idea they would test first with a real customer"},
         {"type": "drift", "text": "Four minutes behind"}]
STYLE = {"enabled": True, "mode": "full", "shape": "rounded", "width": 700, "font": "Segoe UI", "font_size": 30,
         "text_color": "#ffffff", "background": "#111418", "opacity": 80, "show_seconds": 10, "screen": "", "position": "top-center"}


def test_card_wraps_to_its_width_and_keeps_its_corners_clear() -> None:
    img = render_hints(HINTS, Style.from_settings(STYLE))
    assert img.size[0] == 700 and img.size[1] > 3 * 30
    assert img.getpixel((0, 0))[3] == 0  # a rounded corner is transparent
    assert img.getpixel((350, 5))[3] == round(80 * 2.55)  # the background at its opacity
    taller = render_hints(HINTS, Style.from_settings({**STYLE, "width": 300}))
    assert taller.size[1] > img.size[1]  # narrower wraps onto more lines
    square = render_hints(HINTS, Style.from_settings({**STYLE, "shape": "square"}))
    assert square.getpixel((0, 0))[3] > 0


def test_style_falls_back_and_scales_with_the_monitor() -> None:
    style = Style.from_settings({"width": 99999, "font": "Comic Sans", "text_color": "red", "shape": "blob"}, scale=1.5)
    assert style.width == 3600 and style.font == "Segoe UI" and style.text == (255, 255, 255) and style.shape == "rounded"
    assert Style.from_settings({"font_size": 30}, scale=2).font_size == 60


def test_cue_mode_draws_only_the_badge() -> None:
    cue = frame(HINTS, "cue", STYLE)
    assert cue is not None and cue.size == (66, 66)  # 2.2 × the text size: an icon, not the words
    assert render_cue(Style.from_settings(STYLE)).getpixel((0, 0))[3] == 0  # round
    assert frame([], "full", STYLE) is None
    assert len(premultiplied_bgra(cue)) == 66 * 66 * 4


def test_place_on_a_monitor_work_area() -> None:
    screens = [Screen(r"\\.\DISPLAY1", True, 0, 0, 1920, 1040, 1.0), Screen(r"\\.\DISPLAY3", False, 1920, -200, 2560, 1400, 1.5)]
    assert pick(screens, r"\\.\DISPLAY3").x == 1920 and pick(screens, "gone").primary
    second = pick(screens, r"\\.\DISPLAY3")
    assert place(second, "top-center", 24, 700, 200) == (1920 + (2560 - 700) // 2, -200 + 36)  # margin scaled 1.5
    assert place(second, "bottom-right", 0, 700, 200) == (1920 + 2560 - 700, -200 + 1400 - 200)
    assert place(screens[0], "center", 24, 100, 100) == (910, 470)
    assert place(screens[0], "nowhere", 0, 100, 100) == (910, 0)
    assert pick([], "").width == 1920


def state(on: bool = True, hints: list | None = None, at: int | None = 1, **extra: object) -> dict:
    return {"on": on, "hints": hints if hints is not None else HINTS, "hints_at": at, "overlay": dict(STYLE), **extra}


def test_pops_up_holds_and_fades_to_nothing() -> None:
    b = Behaviour()
    b.on_state(state(hints=[]), 0.0, 0, sample_hints())
    assert b.alpha(0.0) == 0 and not b.animating()
    b.on_state(state(), 1.0, 0, sample_hints())
    assert 0 < b.alpha(1.0 + FADE_IN_S / 2) < 255 and b.alpha(1.0 + FADE_IN_S) == 255
    assert b.alpha(1.0 + FADE_IN_S + 9.9) == 255  # held for show_seconds
    assert 0 < b.alpha(1.0 + FADE_IN_S + 10 + FADE_OUT_S / 2) < 255
    assert b.alpha(1.0 + FADE_IN_S + 10 + FADE_OUT_S) == 0 and b.popup is None
    b.on_state(state(), 20.0, 0, sample_hints())  # the same hints again: not shown twice
    assert b.popup is None


def test_cleared_hints_or_copilot_off_fade_out_at_once() -> None:
    b = Behaviour()
    b.on_state(state(), 0.0, 0, sample_hints())
    b.on_state(state(hints=[], at=None), 2.0, 0, sample_hints())  # the item changed: the server cleared them
    assert b.alpha(2.0 + FADE_OUT_S) == 0
    b.on_state(state(at=5), 3.0, 0, sample_hints())
    b.on_state(state(on=False), 4.0, 0, sample_hints())
    assert b.alpha(4.0 + FADE_OUT_S) == 0


def test_cue_pulses_and_a_sample_shows_with_copilot_off() -> None:
    b = Behaviour()
    b.on_state(state(on=False, hints=[], overlay={**STYLE, "mode": "cue"}, sample_at=10_000), 0.0, 12_000, sample_hints())
    assert b.popup is not None and b.popup.sample and b.popup.mode == "cue"
    levels = {b.alpha(FADE_IN_S + t / 10) for t in range(15)}
    assert min(levels) < 160 < max(levels)  # a gentle pulse, never gone
    b.on_state(state(on=False, hints=[], overlay={**STYLE, "mode": "cue"}, sample_at=10_000), 1.0, 13_000, sample_hints())
    assert b.popup is not None and b.popup.fade_from is None  # a sample is not faded by a snapshot with Copilot off
    late = Behaviour()
    late.on_state(state(on=False, hints=[], sample_at=10_000), 0.0, 40_000, sample_hints())
    assert late.popup is None  # asked for long ago (an overlay that started late)


def test_disabled_overlay_never_shows_and_style_changes_redraw() -> None:
    b = Behaviour()
    b.on_state(state(overlay={**STYLE, "enabled": False}), 0.0, 0, sample_hints())
    assert b.popup is None
    rev = b.style_rev
    b.on_state(state(at=2, overlay={**STYLE, "width": 900}), 1.0, 0, sample_hints())
    assert b.style_rev == rev + 1 and b.popup is not None


def test_socket_url_follows_the_scheme() -> None:
    assert socket_url("https://127.0.0.1:8449/") == "wss://127.0.0.1:8449/ws?role=overlay"
    assert socket_url("http://127.0.0.1:8456") == "ws://127.0.0.1:8456/ws?role=overlay"


def test_a_test_hint_says_so_on_the_card() -> None:
    plain = render_hints([{"type": "next_q", "text": "Ask which idea first"}], Style.from_settings(STYLE))
    labelled = render_hints([{"type": "next_q", "text": "Ask which idea first", "test": True}], Style.from_settings(STYLE))
    assert plain.size == labelled.size and plain.tobytes() != labelled.tobytes()  # the label row reads "TEST · ASK"
