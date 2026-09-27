"""PowerPoint import: image analysis, placeholder slides, plan building, the job API.

The real COM exporter runs only under ``FS_TEST_POWERPOINT=1`` (it drives the
PowerPoint installed on this PC, on a synthetic deck it builds itself); every
other test uses a fake exporter that writes synthetic PNGs.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from src.importer.analyze import detect_profile, dhash, hamming
from src.importer.service import (
    build_slides_meta,
    classify_placeholder,
    first_plan,
)

BG = (242, 242, 242)
BOX = (217, 217, 217)


def _slide(path: Path, *, box: tuple[int, int, int, int] | None = None, art: str = "left") -> Path:
    im = Image.new("RGB", (1920, 1080), BG)
    d = ImageDraw.Draw(im)
    if art == "left":
        d.ellipse((150, 200, 900, 900), fill=(200, 60, 40))
        d.rectangle((300, 400, 700, 600), fill=(40, 120, 200))
    elif art == "wide":
        d.rectangle((100, 150, 1820, 950), fill=(40, 150, 90))
        d.ellipse((600, 300, 1300, 800), fill=(200, 60, 40))
    if box:
        d.rectangle(box, fill=BOX)
    im.save(path)
    return path


def test_profile_detection(tmp_path: Path) -> None:
    strip = _slide(tmp_path / "strip.png", box=(1120, 250, 1890, 830))
    pip = _slide(tmp_path / "pip.png", box=(1500, 40, 1880, 300))
    empty_right = _slide(tmp_path / "empty.png")
    wide = _slide(tmp_path / "wide.png", art="wide")
    g = detect_profile(strip)
    assert (g.profile, g.source) == ("camera_strip", "detected")
    assert g.zone is not None and g.zone[0] == pytest.approx(0.583, abs=0.02)
    assert detect_profile(pip).profile == "camera_pip"
    assert detect_profile(empty_right).profile == "camera_strip"
    assert detect_profile(wide).profile == "screen_only"


def test_notes_override_detection(tmp_path: Path) -> None:
    wide = _slide(tmp_path / "wide.png", art="wide")
    g = detect_profile(wide, "Remember the story.\n[obs: pip]")
    assert (g.profile, g.source) == ("camera_pip", "notes")


def test_dhash_is_stable_and_discriminates(tmp_path: Path) -> None:
    a = _slide(tmp_path / "a.png", box=(1120, 250, 1890, 830))
    b = _slide(tmp_path / "b.png", box=(1120, 250, 1890, 830))
    c = _slide(tmp_path / "c.png", art="wide")
    assert dhash(a) == dhash(b)
    assert hamming(dhash(a), dhash(c)) > 8


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("streamalive 1 - map", {"kind": "activity", "type": "map"}),
        ("streamalive 2 – weather report", {"kind": "activity", "type": "scale"}),
        ("activity – what did you learn about the group", {"kind": "activity", "type": "word_cloud"}),
        ("breakout 1 – 1:1 instructions", {"kind": "break"}),
        ("Mentimeter slide", {"kind": "skip"}),
        ("Working agreement", None),
    ],
)
def test_placeholder_slides(title: str, expected: dict[str, str] | None) -> None:
    got = classify_placeholder(title)
    if expected is None:
        assert got is None
    else:
        assert got is not None and {k: got[k] for k in expected} == expected


def _export(slides: list[dict[str, Any]], sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = []
    for i, s in enumerate(slides, start=1):
        out.append({"slide_id": s["id"], "index": i, "title": s.get("title", ""), "notes": s.get("notes", ""),
                    "texts": [s["title"]] if s.get("title") else [], "pictures": s.get("pictures", 1),
                    "background": "#f2f2f2", "hidden": False, "file": f"slide-{s['id']}.png"})
    return {"slide_width": 960, "slide_height": 540, "sections": sections or [], "slides": out}


DECK = [
    {"id": 10, "title": "Opening", "pictures": 0},
    {"id": 11},
    {"id": 12, "title": "streamalive 1 - where are you joining from"},
    {"id": 13},
    {"id": 14, "title": "Working agreement", "pictures": 0},
    {"id": 15},
    {"id": 16, "title": "breakout 1 – pairs"},
]


def _meta(tmp: Path, deck: list[dict[str, Any]], sections: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    export = _export(deck, sections)
    for s in export["slides"]:
        _slide(tmp / s["file"], box=(1120, 250, 1890, 830))
    return build_slides_meta(export, tmp, Path("deck.pptx"))


def test_first_plan_uses_dividers_and_placeholders(tmp_path: Path) -> None:
    meta = _meta(tmp_path, DECK)
    sections = first_plan(meta, 120)
    assert [s.name for s in sections] == ["Opening", "Working agreement"]
    kinds = [(it.kind, it.slide_id or it.type) for it in sections[0].items]
    assert kinds == [("slide", 10), ("slide", 11), ("activity", "map"), ("slide", 13)]
    assert sections[1].items[-1].kind == "break"
    assert sum(s.minutes for s in sections) == pytest.approx(120, abs=10)
    assert all(it.timer is None for s in sections for it in s.items)


def test_first_plan_prefers_powerpoint_sections(tmp_path: Path) -> None:
    meta = _meta(tmp_path, DECK, sections=[{"name": "Part A", "first": 1, "count": 3}, {"name": "Part B", "first": 4, "count": 4}])
    assert [s.name for s in first_plan(meta, 60)] == ["Part A", "Part B"]


class FakeExporter:
    def __init__(self, deck: list[dict[str, Any]]) -> None:
        self.deck = deck

    def __call__(self, deck: Path, out: Path, progress) -> dict[str, Any]:
        export = _export(self.deck)
        for i, s in enumerate(export["slides"], start=1):
            _slide(out / s["file"], box=(1120, 250, 1890, 830))
            progress(i, len(export["slides"]))
        return export


def _wait(client, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        job = client.get(f"/api/imports/{job_id}").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("import did not finish")


def test_import_api_end_to_end(client, tmp_path: Path) -> None:
    sid = client.post("/api/sessions", json={"title": "Demo", "workshop": "w", "folder": "demo"}).json()["id"]
    client.app.state.importer._exporter = FakeExporter(DECK)
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a deck")
    job = client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()
    job = _wait(client, job["id"])
    assert job["state"] == "done", job
    assert job["result"]["slides"] == 7 and job["result"]["first_import"] is True

    slides = client.get(f"/api/sessions/{sid}/slides").json()
    assert [s["slide_id"] for s in slides["slides"]] == [d["id"] for d in DECK]
    assert slides["slides"][1]["profile"] == "camera_strip"
    png = client.get(f"/api/sessions/{sid}/slides/slide-11.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert client.get(f"/api/sessions/{sid}/slides/..%2Fsession.yaml").status_code == 404

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["readiness"][0]["state"] == "ok"
    assert detail["session"]["source"]["pptx"] == str(deck)

    # a re-import waits for the review (tests/test_reimport.py): nothing changes yet
    client.app.state.importer._exporter = FakeExporter([d for d in DECK if d["id"] != 13])
    job = _wait(client, client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"])
    assert job["result"]["review"] is True and job["result"]["first_import"] is False
    folder = Path(detail["folder"]["path"])
    assert (folder / "slides" / "slide-13.png").exists()
    assert (folder / "slides" / "incoming" / "slides.json").is_file()


def test_import_rejects_non_decks(client, tmp_path: Path) -> None:
    sid = client.post("/api/sessions", json={"title": "Demo"}).json()["id"]
    res = client.post(f"/api/sessions/{sid}/import", json={"pptx": str(tmp_path / "missing.pptx")})
    assert res.status_code == 422 and res.json()["error"]["code"] == "deck_not_found"
    res = client.post(f"/api/sessions/{sid}/import", json={"pptx": str(tmp_path / "notes.txt")})
    assert res.json()["error"]["code"] == "not_a_deck"


def test_exporter_failure_is_reported(client, tmp_path: Path) -> None:
    from src.importer.service import ImportError_

    def broken(deck, out, progress):
        raise ImportError_("powerpoint_unavailable", "PowerPoint is not available on this PC")

    sid = client.post("/api/sessions", json={"title": "Demo"}).json()["id"]
    client.app.state.importer._exporter = broken
    deck = tmp_path / "d.pptx"
    deck.write_bytes(b"x")
    job = _wait(client, client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"])
    assert job["state"] == "error" and job["code"] == "powerpoint_unavailable"


def test_pick_is_local_only(isolated_env) -> None:
    from fastapi.testclient import TestClient

    from app.webapp.server import create_app

    with TestClient(create_app(), client=("100.64.0.9", 5000)) as remote:
        assert remote.post("/api/pick", json={"kind": "pptx"}).status_code == 403


@pytest.mark.skipif(os.environ.get("FS_TEST_POWERPOINT") != "1", reason="drives the real PowerPoint; set FS_TEST_POWERPOINT=1")
def test_real_powerpoint_export(tmp_path: Path) -> None:
    import pythoncom
    import win32com.client

    from src.importer.service import run_exporter

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    pres = app.Presentations.Add(False)
    for i, title in enumerate(["Opening", "activity – what brings you here", "Closing"], start=1):
        slide = pres.Slides.Add(i, 1)  # ppLayoutTitle
        slide.Shapes.Title.TextFrame.TextRange.Text = title
    deck = tmp_path / "synthetic.pptx"
    pres.SaveAs(str(deck))
    pres.Close()

    seen: list[tuple[int, int]] = []
    export = run_exporter(deck, tmp_path / "out", lambda i, n: seen.append((i, n)))
    assert [s["title"] for s in export["slides"]] == ["Opening", "activity – what brings you here", "Closing"]
    assert seen[-1] == (3, 3)
    assert all((tmp_path / "out" / s["file"]).is_file() for s in export["slides"])
    with Image.open(tmp_path / "out" / export["slides"][0]["file"]) as im:
        assert im.size == (1920, 1080)
    assert json.loads((tmp_path / "out" / "export.json").read_text(encoding="utf-8"))["slides"]
