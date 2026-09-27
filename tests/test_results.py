"""Results (step 12): the PDF order, the Excel report, the Zoom-chat check."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from src.results.collect import load_results, participation, timeline
from src.results.excel import sheet_title
from src.results.reconcile import minutes_of, norm_text, parse_saved_chat, reconcile
from src.sessions.model import parse_session
from tests.fixtures.demo import PLAN, build_demo_session, write_demo_run, zoom_saved_chat


def _run(isolated_env: Path) -> tuple[str, Path, dict]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "results", isolated_env / "sessions.local.yaml")
    return sid, folder, write_demo_run(folder)


# ---- the order of the session PDF ---------------------------------------------

ITEMS = [
    {"id": "slide-1", "kind": "slide", "title": "One", "slide_file": "slide-1.png"},
    {"id": "act-a", "kind": "activity", "title": "A"},
    {"id": "slide-2", "kind": "slide", "title": "Two", "slide_file": "slide-2.png"},
    {"id": "brk", "kind": "break", "title": "Break"},
]


def ev(name: str, item: str | None = None, **kw) -> dict:
    return {"event": name, "at": "t", **({"item_id": item} if item else {}), **kw}


def test_slides_at_first_showing_and_captures_at_their_last_stop() -> None:
    events = [ev("session_live", "slide-1"), ev("item", "act-a"), ev("capture_start", "act-a"), ev("capture_stop", "act-a"),
              ev("item", "slide-2"), ev("item", "act-a"), ev("capture_reopen", "act-a"), ev("capture_stop", "act-a"),
              ev("item", "slide-1"), ev("item", "brk"), ev("item", "slide-gone")]
    pages = timeline(events, ITEMS, {"act-a"})
    assert [(p["kind"], p["item_id"]) for p in pages] == [("slide", "slide-1"), ("slide", "slide-2"), ("capture", "act-a")]


def test_an_old_log_without_the_starting_item_starts_on_item_one() -> None:
    pages = timeline([ev("session_live"), ev("item", "slide-2"), ev("session_live")], ITEMS, set())
    assert [p["item_id"] for p in pages] == ["slide-1", "slide-2"]


def test_a_capture_without_its_frozen_file_is_not_a_page() -> None:
    assert timeline([ev("capture_stop", "act-a")], ITEMS, set()) == []


# ---- the results of a finished run ---------------------------------------------

def test_results_read_back_in_the_order_they_happened(isolated_env: Path) -> None:
    _, folder, run = _run(isolated_env)
    data = load_results(folder, parse_session(PLAN))
    assert [a["id"] for a in data["activities"]] == ["act-map", "act-weather", "act-kryptonite", "act-enemy", "act-ideas", "act-takeaway"]
    assert (data["slides"], data["captures"]) == (10, 6)
    assert data["pages"][0] == {**data["pages"][0], "kind": "slide", "item_id": "slide-101"}
    assert data["pages"][1]["kind"] == "capture" and data["pages"][1]["item_id"] == "act-map"

    kr = next(a for a in data["activities"] if a["id"] == "act-kryptonite")
    assert (kr["answers"], kr["hidden"]) == (8, 1)
    assert kr["summary"] == "8 answers · word cloud" and kr["top_label"] == "Top words"
    assert kr["top"][0] == {"label": "meetings", "count": 2}
    assert next(r for r in kr["answer_rows"] if r["text"] == "hello")["hidden"] is True
    weather = next(a for a in data["activities"] if a["id"] == "act-weather")
    assert weather["summary"].startswith("8 answers · average 3.") and weather["tile"].startswith("3.")
    geo = next(a for a in data["activities"] if a["id"] == "act-map")
    assert "1 unplaced" in geo["summary"] and geo["top_label"] == "Countries"
    assert next(r for r in geo["answer_rows"] if r["text"] == "Grnada")["value"] == "unplaced"

    people = participation(data["activities"], run["chat"])
    assert sum(p["total"] for p in people) == 39  # 40 answers, one hidden
    assert "You" not in {p["name"] for p in people}
    assert people[0]["total"] >= people[-1]["total"]


def test_a_session_that_never_went_live_has_no_results(isolated_env: Path) -> None:
    _, folder = build_demo_session(isolated_env / "sessions" / "demo" / "fresh")
    data = load_results(folder, parse_session(PLAN))
    assert data == {**data, "activities": [], "pages": [], "went_live": False}


def test_a_damaged_capture_is_left_out_not_fatal(isolated_env: Path) -> None:
    _, folder, _ = _run(isolated_env)
    (folder / "live" / "captures" / "act-enemy.json").write_text("{not json", encoding="utf-8")
    with open(folder / "live" / "events.jsonl", "a", encoding="utf-8") as fh:
        fh.write("garbage\n")
    data = load_results(folder, parse_session(PLAN))
    assert "act-enemy" not in [a["id"] for a in data["activities"]] and len(data["activities"]) == 5


# ---- API: results, frozen image, Excel -------------------------------------------

def test_results_api_and_the_frozen_image(client, isolated_env: Path) -> None:
    sid, _, _ = _run(isolated_env)
    data = client.get(f"/api/sessions/{sid}/results").json()
    assert data["went_live"] and len(data["activities"]) == 6
    assert data["reconciliation"] is None and data["exports"] == {"pdf": None, "xlsx": None}
    r = client.get(f"/api/sessions/{sid}/results/captures/act-kryptonite.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get(f"/api/sessions/{sid}/results/captures/nope.png").status_code == 404
    assert client.get(f"/api/sessions/{sid}/results/captures/..%2Fx.png").status_code == 404


def test_excel_report_has_a_sheet_per_activity_and_participation(client, isolated_env: Path) -> None:
    sid, folder, _ = _run(isolated_env)
    r = client.get(f"/api/sessions/{sid}/exports/report.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    wb = load_workbook(folder / "exports" / "report.xlsx")
    assert wb.sheetnames[0] == "Summary" and wb.sheetnames[-1] == "Participation"
    assert len(wb.sheetnames) == 8 and all(len(n) <= 31 for n in wb.sheetnames)
    kr = wb[next(n for n in wb.sheetnames if n.startswith("3 "))]
    rows = [[c.value for c in row] for row in kr.iter_rows()]
    header = rows.index(["Name", "Time", "Answer", "Parsed", "Hidden"])
    assert ["yes"] == [r[4] for r in rows[header + 1:] if r[2] == "hello"]
    part = [[c.value for c in row] for row in wb["Participation"].iter_rows()]
    assert part[0][0] == "Name" and part[0][-1] == "Chat messages" and len(part[0]) == 9
    assert client.get(f"/api/sessions/{sid}/exports/session.pdf").status_code == 404


def test_excel_needs_a_capture(client, isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "empty", isolated_env / "sessions.local.yaml")
    assert client.get(f"/api/sessions/{sid}/exports/report.xlsx").json()["error"]["code"] == "nothing_to_export"
    assert client.post(f"/api/sessions/{sid}/exports/pdf").json()["error"]["code"] == "nothing_to_export"


def test_sheet_titles_are_valid_and_unique() -> None:
    taken: set[str] = set()
    a = sheet_title(1, "What is our [common] enemy? / really: *", taken)
    b = sheet_title(1, "What is our [common] enemy? / really: *", taken)
    assert len(a) <= 31 and len(b) <= 31 and a != b and not set("[]:*?/\\") & set(a + b)


# ---- the session PDF (real headless Chromium) ------------------------------------

def test_session_pdf_prints_every_page_then_the_appendix(client, isolated_env: Path) -> None:
    sid, folder, _ = _run(isolated_env)
    (folder / "live" / "captures" / "act-ideas.png").unlink()  # a PNG that never rendered still gets a page
    r = client.post(f"/api/sessions/{sid}/exports/pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["slides"], body["captures"]) == (10, 6)
    assert body["missing_images"] == ["What do we need to win?"]
    assert body["pages"] is not None and body["pages"] >= 17  # 16 pages + at least one appendix page
    pdf = client.get(f"/api/sessions/{sid}/exports/session.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert client.get(f"/api/sessions/{sid}/results").json()["exports"]["pdf"]["file"] == "session.pdf"


# ---- the check against Zoom's saved chat -----------------------------------------

def test_both_saved_chat_layouts_parse() -> None:
    new = "18:40:12 From Sofía L. to Everyone:\n\tSevilla, España\n\tsecond line\n18:41:00 From Carlos P. to Everyone:\n\tok\n"
    old = "18:40:12\t From  Sofía L. : Sevilla, España\n18:41:00\t From  Carlos P. : ok\n"
    a, b = parse_saved_chat(new), parse_saved_chat(old)
    assert [(m.sender, m.text) for m in a] == [("Sofía L.", "Sevilla, España\nsecond line"), ("Carlos P.", "ok")]
    assert a[0].to == "Everyone" and a[0].time == "18:40:12"
    assert [(m.sender, m.text) for m in b] == [("Sofía L.", "Sevilla, España"), ("Carlos P.", "ok")]


def test_replies_and_reactions() -> None:
    text = ('18:40:12 From Ana R. to Everyone:\n\tperfectionism\n'
            '18:40:40 From Ben S. to Everyone:\n\tReplying to "perfectionism"\n\tsame here\n'
            '18:40:50 From Ana R. to Everyone:\n\tReacted to "same here" with 👍\n')
    saved = parse_saved_chat(text)
    assert saved[1].text == "same here"
    chat = [{"sender": "Ana R.", "text": "perfectionism", "time": "18:40"}, {"sender": "Ben S.", "text": "same here", "time": "18:40"}]
    rep = reconcile(saved, chat)
    assert (rep["zoom_messages"], rep["matched"], rep["reactions"], rep["missing_count"]) == (2, 2, 1, 0)


def test_emoji_own_messages_simulated_and_time_tolerance() -> None:
    saved = parse_saved_chat("18:40:59 From Ana R. to Everyone:\n\tgreat 🎉 session\n"
                             "18:41:10 From Demo Host to Everyone:\n\tThanks!\n"
                             "18:42:00 From Ben S. to Everyone:\n\t🎉\n")
    chat = [
        {"sender": "Ana R.", "text": "great session", "time": "6:41 PM", "source": "zoom"},
        {"sender": "You", "text": "Thanks!", "time": "18:41", "own": True, "source": "zoom"},
        {"sender": "Ben S.", "text": "", "time": "18:42", "source": "zoom"},
        {"sender": "Robin T.", "text": "7", "time": "18:43", "source": "simulator"},
    ]
    rep = reconcile(saved, chat)
    assert (rep["matched"], rep["missing_count"], rep["extra_count"], rep["simulated"]) == (3, 0, 0, 1)


def test_missing_and_extra_are_listed() -> None:
    saved = parse_saved_chat("18:00:05 From Ana R. to Everyone:\n\tHi all\n18:40:00 From Ben S. to Everyone:\n\tmeetings\n")
    chat = [{"sender": "Ben S.", "text": "meetings", "time": "18:40"}, {"sender": "Cy D.", "text": "late one", "time": "18:59"}]
    rep = reconcile(saved, chat)
    assert rep["matched"] == 1 and rep["missing"] == [{"time": "18:00:05", "sender": "Ana R.", "to": "Everyone", "text": "Hi all"}]
    assert rep["extra"] == [{"time": "18:59", "sender": "Cy D.", "text": "late one"}]


def test_the_same_answer_twice_matches_twice_not_once() -> None:
    saved = parse_saved_chat("18:40:01 From Ana R. to Everyone:\n\tyes\n18:40:30 From Ana R. to Everyone:\n\tyes\n")
    rep = reconcile(saved, [{"sender": "Ana R.", "text": "yes", "time": "18:40"}])
    assert (rep["matched"], rep["missing_count"]) == (1, 1)


def test_clock_and_text_normalisation() -> None:
    assert minutes_of("18:40:12") == minutes_of("6:40 PM") == 18 * 60 + 40
    assert minutes_of("12:05 AM") == 5 and minutes_of("nope") is None
    assert norm_text("  Great 🎉   Session ") == "great session"


def test_reconcile_endpoint_keeps_the_report(client, isolated_env: Path) -> None:
    sid, folder, run = _run(isolated_env)
    saved = isolated_env / "meeting_saved_chat.txt"
    saved.write_text("18:00:01 From Early Bird to Everyone:\n\tHello!\n" + zoom_saved_chat(run["chat"]), encoding="utf-8")
    rep = client.post(f"/api/sessions/{sid}/reconcile", json={"path": str(saved)}).json()
    assert (rep["zoom_messages"], rep["matched"], rep["missing_count"]) == (42, 41, 1)
    assert rep["file"] == "meeting_saved_chat.txt"
    assert client.get(f"/api/sessions/{sid}/results").json()["reconciliation"]["matched"] == 41
    assert (folder / "exports" / "zoom-reconciliation.json").is_file()
    junk = isolated_env / "notes.txt"
    junk.write_text("just some notes\n", encoding="utf-8")
    assert client.post(f"/api/sessions/{sid}/reconcile", json={"path": str(junk)}).json()["error"]["code"] == "not_a_zoom_chat"
    assert client.post(f"/api/sessions/{sid}/reconcile", json={"path": str(isolated_env / "none.txt")}).status_code == 404
