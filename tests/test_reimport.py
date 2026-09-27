"""Re-import review (step 13): the diff categories, per-change apply, anchors."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from src.importer.analyze import dhash, hamming
from src.importer.review import apply, diff, discard, pending
from src.importer.service import INCOMING, build_slides_meta, first_plan
from src.sessions.model import Item, Session, ensure_ids

ART = {"a": (120, 180), "b": (420, 700), "c": (700, 160), "d": (180, 640), "e": (520, 380),
       "f": (820, 620), "g": (300, 420), "h": (40, 820), "i": (640, 80), "j": (860, 300)}


def _draw(path: Path, art: str) -> None:
    im = Image.new("RGB", (1920, 1080), (242, 242, 242))
    d = ImageDraw.Draw(im)
    x, y = ART[art]
    d.rectangle((x, y, x + 380, y + 260), fill=(30, 30, 30))
    d.ellipse((900 - x // 2, 900 - y // 2, 1100 - x // 3, 1040 - y // 3), fill=(200, 60, 40))
    d.rectangle((1120, 250, 1890, 830), fill=(217, 217, 217))
    im.save(path)


def _meta(folder: Path, deck: list[dict[str, Any]]) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    slides = []
    for i, s in enumerate(deck, start=1):
        _draw(folder / f"slide-{s['id']}.png", s["art"])
        slides.append({"slide_id": s["id"], "index": i, "title": s["title"], "notes": s.get("notes", ""),
                       "texts": [s["title"]], "pictures": 1, "background": "#f2f2f2", "hidden": False,
                       "file": f"slide-{s['id']}.png"})
    return build_slides_meta({"sections": [], "slides": slides}, folder, Path("deck.pptx"))


OLD = [{"id": n, "art": a, "title": f"Slide {n}"} for n, a in zip(range(1, 9), "abcdefgh", strict=True)]
OLD[3]["notes"] = "line one"
NEW = [
    {"id": 1, "art": "a", "title": "Slide 1"},
    {"id": 2, "art": "j", "title": "Slide 2"},  # image changed
    {"id": 3, "art": "c", "title": "Slide 3, renamed"},  # title changed
    {"id": 9, "art": "i", "title": "A new slide"},  # new
    {"id": 4, "art": "d", "title": "Slide 4", "notes": "line one\nline two\nline three"},  # notes changed
    {"id": 70, "art": "g", "title": "Slide 7"},  # slide 7, re-created with a new id
    {"id": 8, "art": "h", "title": "Slide 8"},
    {"id": 6, "art": "f", "title": "Slide 6"},  # moved to the end
    {"id": 10, "art": "e", "title": "activity – what do you take away"},  # a new placeholder
]  # slide 5 removed


def _setup(tmp_path: Path) -> tuple[Path, Session]:
    folder = tmp_path / "session"
    old_meta = _meta(folder / "slides", OLD)
    (folder / "slides" / "slides.json").write_text(json.dumps(old_meta), encoding="utf-8")
    new_meta = _meta(folder / "slides" / INCOMING, NEW)
    (folder / "slides" / INCOMING / "slides.json").write_text(json.dumps(new_meta), encoding="utf-8")
    session = Session(title="x")
    session.sections = first_plan(old_meta, 60)
    items = session.sections[0].items
    items.insert(5, Item(kind="activity", id="act-a", type="word_cloud", question="after five"))
    items.insert(7, Item(kind="activity", id="act-b", type="scale", question="after six"))
    ensure_ids(session)
    return folder, session


def _plan(session: Session) -> list[Any]:
    return [it.slide_id if it.kind == "slide" else (it.id or it.type) for it in session.all_items()]


def test_the_drawings_are_told_apart() -> None:
    assert hamming(_hash("a"), _hash("j")) > 3


def _hash(art: str) -> str:
    import tempfile

    p = Path(tempfile.mkdtemp()) / "x.png"
    _draw(p, art)
    return dhash(p)


def test_diff_names_every_category(tmp_path: Path) -> None:
    folder, session = _setup(tmp_path)
    old = json.loads((folder / "slides" / "slides.json").read_text(encoding="utf-8"))
    d = diff(old, pending(folder), session)
    assert d["counts"] == {"identical": 3, "modified": 3, "new": 2, "removed": 1, "moved": 1}
    by = {c["id"]: c for c in d["changes"]}
    assert by["mod-2"]["detail"] == "Image changed"
    assert by["mod-3"]["detail"] == "Title changed from “Slide 3”"
    assert by["mod-4"]["detail"] == "Notes changed · 2 lines added"
    assert by["new-9"]["after"] == 3 and by["new-9"]["detail"] == "New slide · camera strip detected"
    assert by["new-10"]["detail"] == "New activity slide · becomes a word cloud"
    assert by["rem-5"]["detail"] == "The activity after it moves to follow slide 4"
    assert by["mov-6"]["detail"] == "Was slide 6, now slide 8"
    assert {i["slide_id"]: i["rematched"] for i in d["identical"]} == {1: False, 70: True, 8: False}
    assert d["profiles"] == {"camera_strip": 8}
    assert [c["id"] for c in d["changes"]][:3] == ["mod-2", "mod-3", "new-9"]  # in deck order


def test_apply_everything(tmp_path: Path) -> None:
    folder, session = _setup(tmp_path)
    old = json.loads((folder / "slides" / "slides.json").read_text(encoding="utf-8"))
    ids = {c["id"] for c in diff(old, pending(folder), session)["changes"]}
    res = apply(folder, session, ids)
    assert (res["added"], res["removed"], res["moved"], res["modified"], res["declined"]) == (2, 1, 1, 3, 0)
    plan = _plan(session)
    assert plan[:9] == [1, 2, 3, 9, 4, "act-a", 70, 8, 6]  # act-a now follows slide 4; slide 7 is 70 now
    assert plan[9] == "act-b" and session.all_items()[10].question.lower().startswith("what do you take away")
    assert [s["slide_id"] for s in res["meta"]["slides"]] == [1, 2, 3, 9, 4, 70, 8, 6, 10]
    names = {p.name for p in (folder / "slides").glob("slide-*.png")}
    assert "slide-5.png" not in names and "slide-7.png" not in names and "slide-9.png" in names


def test_apply_only_some(tmp_path: Path) -> None:
    folder, session = _setup(tmp_path)
    before = dhash(folder / "slides" / "slide-2.png")
    res = apply(folder, session, {"mod-3", "mod-4", "new-10"})  # keep 2's old image, keep 5, don't move 6, skip 9
    assert res["declined"] == 4
    plan = _plan(session)
    assert plan[:9] == [1, 2, 3, 4, 5, "act-a", 6, "act-b", plan[8]] and plan[9:] == [70, 8]
    slides = {s["slide_id"]: s for s in res["meta"]["slides"]}
    assert 9 not in slides and slides[5]["in_deck"] is False
    assert slides[2]["fp"]["image"] == before and dhash(folder / "slides" / "slide-2.png") == before
    assert slides[3]["title"] == "Slide 3, renamed"
    assert (folder / "slides" / "slide-5.png").is_file() and not (folder / "slides" / "slide-9.png").exists()


def test_a_stale_review_is_refused(tmp_path: Path) -> None:
    import pytest

    from src.importer.review import ReviewError

    folder, session = _setup(tmp_path)
    with pytest.raises(ReviewError) as err:
        apply(folder, session, {"mod-99"})
    assert err.value.code == "stale_review"
    discard(folder)
    assert pending(folder) is None
    with pytest.raises(ReviewError) as err:
        apply(folder, session, set())
    assert err.value.code == "no_pending_import"


# ---- through the API, with a fake PowerPoint -------------------------------------------

class FakeExporter:
    def __init__(self, deck: list[dict[str, Any]]) -> None:
        self.deck = deck

    def __call__(self, deck: Path, out: Path, progress) -> dict[str, Any]:  # noqa: ANN001
        slides = []
        for i, s in enumerate(self.deck, start=1):
            _draw(out / f"slide-{s['id']}.png", s["art"])
            slides.append({"slide_id": s["id"], "index": i, "title": s["title"], "notes": s.get("notes", ""),
                           "texts": [s["title"]], "pictures": 1, "background": "#f2f2f2", "hidden": False,
                           "file": f"slide-{s['id']}.png"})
            progress(i, len(self.deck))
        return {"sections": [], "slides": slides}


def _wait(client, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/imports/{job_id}").json()
        if job["state"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("import did not finish")


def test_reimport_waits_for_review_then_applies(client, tmp_path: Path) -> None:
    sid = client.post("/api/sessions", json={"title": "Demo", "workshop": "w", "folder": "demo"}).json()["id"]
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"not really a deck")
    client.app.state.importer._exporter = FakeExporter(OLD)
    assert _wait(client, client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"])["result"]["first_import"]
    assert client.get(f"/api/sessions/{sid}/reimport").json() == {"pending": False}
    folder = Path(client.get(f"/api/sessions/{sid}").json()["folder"]["path"])
    before = (folder / "slides" / "slides.json").read_text(encoding="utf-8")

    client.app.state.importer._exporter = FakeExporter(NEW)
    job = _wait(client, client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"])
    assert job["state"] == "done" and job["result"]["review"] is True
    assert (folder / "slides" / "slides.json").read_text(encoding="utf-8") == before  # nothing applied yet
    assert (folder / "slides" / "slide-5.png").is_file()

    review = client.get(f"/api/sessions/{sid}/reimport").json()
    assert review["pending"] and review["counts"]["new"] == 2 and review["counts"]["removed"] == 1
    png = client.get(f"/api/sessions/{sid}/reimport/slides/slide-9.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    assert client.get(f"/api/sessions/{sid}/reimport/slides/..%2Fslides.json").status_code == 404

    res = client.post(f"/api/sessions/{sid}/reimport/apply", json={"accepted": [c["id"] for c in review["changes"]]}).json()
    assert (res["added"], res["removed"], res["moved"]) == (2, 1, 1)
    assert client.get(f"/api/sessions/{sid}/reimport").json() == {"pending": False}
    assert not (folder / "slides" / INCOMING).exists()
    slides = client.get(f"/api/sessions/{sid}/slides").json()["slides"]
    assert [s["slide_id"] for s in slides] == [1, 2, 3, 9, 4, 70, 8, 6, 10]
    detail = client.get(f"/api/sessions/{sid}").json()
    plan = [it.get("slide_id") or it["kind"] for sec in detail["session"]["sections"] for it in sec["items"]]
    assert plan[:4] == [1, 2, 3, 9] and 5 not in plan and 70 in plan

    # a second re-import that is cancelled leaves everything as it was
    client.app.state.importer._exporter = FakeExporter(OLD)
    _wait(client, client.post(f"/api/sessions/{sid}/import", json={"pptx": str(deck)}).json()["id"])
    assert client.get(f"/api/sessions/{sid}/reimport").json()["pending"] is True
    assert client.delete(f"/api/sessions/{sid}/reimport").json() == {"pending": False}
    assert [s["slide_id"] for s in client.get(f"/api/sessions/{sid}/slides").json()["slides"]] == [1, 2, 3, 9, 4, 70, 8, 6, 10]
