"""A synthetic demo session: drawn slides, slides.json and a full plan.

The one dataset allowed on screen in tests, screenshots and demos — no real
deck, roster or chat. Slides are drawn with Pillow in the house style (light
grey canvas, a grey camera box on the right, hand-lettered titles in the
vendored Patrick Hand font).

    python -m tests.fixtures.demo --root <folder> --ledger <sessions.yaml>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from src.importer.service import build_slides_meta
from src.sessions.model import parse_session
from src.sessions.store import SESSION_FILE, LedgerEntry, session_id

REPO = Path(__file__).resolve().parents[2]
FONT = REPO / "app" / "webapp" / "static" / "fonts" / "PatrickHand-Regular.ttf"
BG, BOX, INK = (242, 242, 242), (217, 217, 217), (31, 31, 31)
PALETTE = [(229, 57, 53), (67, 160, 71), (30, 136, 229), (251, 192, 45), (142, 36, 170)]

SLIDES: list[dict[str, Any]] = [
    {"id": 101, "title": "Welcome to the workshop", "shape": "sun", "divider": True},
    {"id": 102, "title": "Today's menu", "shape": "pizza"},
    {"id": 103, "title": "What we want from today", "shape": "puzzle"},
    {"id": 104, "title": "Getting to know each other", "shape": "none", "divider": True},
    {"id": 105, "title": "My personal readme", "shape": "shirt"},
    {"id": 106, "title": "Work hard, work smart", "shape": "stairs", "wide": True},
    {"id": 107, "title": "Working agreement", "shape": "none", "divider": True},
    {"id": 108, "title": "The common enemy", "shape": "shield"},
    {"id": 109, "title": "What do we need to win?", "shape": "badges"},
    {"id": 110, "title": "Thank you!", "shape": "heart"},
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def draw_slide(spec: dict[str, Any], path: Path) -> None:
    im = Image.new("RGB", (1920, 1080), BG)
    d = ImageDraw.Draw(im)
    wide = spec.get("wide", False)
    if not wide:
        d.rectangle((1120, 248, 1888, 832), fill=BOX)
    if spec.get("divider"):
        d.text((110, 470), spec["title"], font=_font(96), fill=(94, 94, 94))
    else:
        d.text((110, 80), spec["title"].upper(), font=_font(64), fill=INK)
    cx, cy = (960, 600) if wide else (520, 620)
    if spec.get("divider"):
        cx, cy = 300, 250  # above the divider's title, not over it
    shape = spec["shape"]
    if shape == "pizza":
        d.ellipse((cx - 300, cy - 300, cx + 300, cy + 300), fill=(210, 60, 40), outline=(90, 30, 20), width=12)
        for i, col in enumerate(PALETTE):
            d.ellipse((cx - 200 + i * 80, cy - 120 + (i % 2) * 150, cx - 130 + i * 80, cy - 50 + (i % 2) * 150), fill=col)
    elif shape == "puzzle":
        for i, col in enumerate(PALETTE[:4]):
            x, y = cx - 280 + (i % 2) * 280, cy - 260 + (i // 2) * 260
            d.rounded_rectangle((x, y, x + 260, y + 240), 30, fill=col)
    elif shape == "sun":
        d.ellipse((cx - 160, cy - 160, cx + 160, cy + 160), fill=PALETTE[3])
    elif shape == "shirt":
        d.polygon([(cx - 250, cy - 200), (cx + 250, cy - 200), (cx + 180, cy + 280), (cx - 180, cy + 280)], fill=(255, 167, 0))
    elif shape == "stairs":
        for i in range(6):
            d.rectangle((cx - 600 + i * 200, cy + 200 - i * 70, cx - 420 + i * 200, cy + 260), fill=PALETTE[1])
    elif shape == "shield":
        d.polygon([(cx, cy - 300), (cx + 260, cy - 180), (cx + 200, cy + 200), (cx, cy + 320), (cx - 200, cy + 200), (cx - 260, cy - 180)], fill=(200, 150, 30))
    elif shape == "badges":
        for i in range(9):
            x, y = cx - 300 + (i % 3) * 220, cy - 250 + (i // 3) * 200
            d.ellipse((x, y, x + 160, y + 160), fill=PALETTE[i % 5], outline=INK, width=8)
    elif shape == "heart":
        d.ellipse((cx - 220, cy - 200, cx + 10, cy + 30), fill=PALETTE[0])
        d.ellipse((cx - 10, cy - 200, cx + 220, cy + 30), fill=PALETTE[0])
        d.polygon([(cx - 215, cy - 60), (cx + 215, cy - 60), (cx, cy + 260)], fill=PALETTE[0])
    im.save(path)


PLAN: dict[str, Any] = {
    "schema": 1,
    "title": "Demo workshop · cohort A",
    "date": "2026-10-27T18:00:00+01:00",
    "duration_minutes": 120,
    "sections": [
        {"id": "sec-welcome", "name": "Welcome", "minutes": 20, "items": [
            {"kind": "slide", "slide_id": 101, "profile": "camera_strip"},
            {"kind": "activity", "id": "act-map", "type": "map", "question": "Where are you joining from?",
             "chat_prompt": "Your city and country", "profile": "camera_pip",
             "timer": {"enabled": True, "seconds": 120, "start": "with_capture", "show_on": "stage", "end": "keep"}},
            {"kind": "slide", "slide_id": 102, "profile": "camera_strip"},
            {"kind": "activity", "id": "act-weather", "type": "scale", "question": "How is your inner weather today?",
             "chat_prompt": "A number from 1 (storm) to 5 (sun)", "profile": "camera_pip",
             "options": {"min": 1, "max": 5, "keywords": "storm, rain, cloudy, sunny spells, sun"}},
            {"kind": "slide", "slide_id": 103, "profile": "camera_strip"},
        ]},
        {"id": "sec-readme", "name": "Personal readme", "minutes": 40, "items": [
            {"kind": "slide", "slide_id": 104, "profile": "camera_strip"},
            {"kind": "slide", "slide_id": 105, "profile": "camera_strip", "title": "Personal readme instructions"},
            {"kind": "activity", "id": "act-pairs", "type": "groups_reveal", "title": "Who are you with?", "profile": "screen_only",
             "include": False, "options": {"round": "pairs"}},
            {"kind": "activity", "id": "act-kryptonite", "type": "word_cloud",
             "question": "What did you learn about this group's kryptonite?",
             "chat_prompt": "One or two words: what switches this group off?", "profile": "camera_pip",
             "font": {"family": "Patrick Hand", "size_px": 72},
             "timer": {"enabled": True, "seconds": 180, "start": "with_capture", "show_on": "stage", "end": "stop_capture"},
             "options": {"merge_variants": True, "stopwords": "en", "show_names": False}},
            {"kind": "slide", "slide_id": 106, "profile": "screen_only"},
        ]},
        {"id": "sec-break", "name": "Break", "minutes": 10, "items": [
            {"kind": "break", "id": "brk-coffee", "title": "Coffee break", "profile": "camera_strip",
             "timer": {"enabled": True, "seconds": 600, "start": "on_enter", "show_on": "both", "end": "chime"}},
        ]},
        {"id": "sec-agreement", "name": "Working agreement", "minutes": 40, "items": [
            {"kind": "slide", "slide_id": 107, "profile": "camera_strip"},
            {"kind": "slide", "slide_id": 108, "profile": "camera_strip"},
            {"kind": "activity", "id": "act-enemy", "type": "cards", "question": "What is our common enemy?",
             "chat_prompt": "One sentence", "profile": "camera_pip"},
            {"kind": "slide", "slide_id": 109, "profile": "camera_strip"},
            {"kind": "activity", "id": "act-ideas", "type": "feed", "question": "What do we need to win?",
             "chat_prompt": "Your idea in the chat", "profile": "camera_pip"},
        ]},
        {"id": "sec-close", "name": "Closing", "minutes": 10, "items": [
            {"kind": "activity", "id": "act-takeaway", "type": "word_cloud", "question": "What do you take away today?",
             "chat_prompt": "One word", "profile": "camera_pip"},
            {"kind": "slide", "slide_id": 110, "profile": "camera_strip"},
        ]},
    ],
}


FIRST = ["Alex", "Sam", "Jordan", "Casey", "Robin", "Jamie", "Taylor", "Morgan", "Avery", "Riley", "Quinn", "Drew",
         "Noa", "Ian", "Eli", "Mia", "Leo", "Ada", "Max", "Zoe"]
LAST = "RSTPLMGBDC"
ROLES = ["Designer", "Engineer", "Product owner", "Analyst", "Marketing lead", "Consultant", "Founder", "Operations"]
PLACES = ["Madrid, ES", "Barcelona, ES", "Sevilla, ES", "Lisboa, PT", "Ciudad de México, MX", "Bogotá, CO", "Milan, IT"]


def write_demo_roster(path: Path, n: int = 40) -> list[str]:
    """A synthetic roster (made-up names, example.com emails): two absent, two without an email."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Participants"
    ws.append(["name", "role", "company", "country", "present", "email"])
    names = []
    for i in range(n):
        name = demo_person(i)
        names.append(name)
        email = "" if i in (5, 17) else f"{name.split()[0].lower()}.{i}@example.com"
        ws.append([name, ROLES[i % len(ROLES)], "Demo Co", PLACES[i % len(PLACES)], 0 if i in (8, 23) else 1, email])
    wb.save(path)
    return names


# What the room "answered" in the demo run (synthetic), per activity in chat order.
RUN_ANSWERS: dict[str, list[str]] = {
    "act-map": ["Madrid, Spain", "Sevilla, España", "Lisboa", "Milan, italy", "CDMX", "desde Bogotá", "Barcelona", "Grnada"],
    "act-weather": ["4", "3", "sunny spells", "5", "2", "4", "3 cloudy", "4"],
    "act-kryptonite": ["meetings", "perfectionism", "meetings without agenda", "procrastination", "perfectionism",
                       "notifications", "hello", "tiredness", "meetings"],
    "act-enemy": ["Endless meetings", "Not saying no", "Context switching", "Unclear goals"],
    "act-ideas": ["Focus blocks every morning", "A shared agenda", "Say no kindly", "Fewer tools"],
    "act-takeaway": ["trust", "focus", "trust", "energy", "clarity", "focus", "trust"],
}
RUN_HIDDEN = {("act-kryptonite", "hello")}
RUN_START_MS = 1_793_120_400_000  # 2026-10-27 17:00 UTC (18:00 in Madrid)


def demo_person(i: int) -> str:
    return f"{FIRST[i % len(FIRST)]} {LAST[(i // len(FIRST) + i) % len(LAST)]}."


def write_demo_run(folder: Path, *, pngs: bool = True) -> dict[str, list[dict[str, Any]]]:
    """A finished live run of the demo session, written the way the live services write it.

    ``live/chat.jsonl`` (source "zoom"), ``live/events.jsonl`` (every item
    shown, each capture's start and stop), ``live/captures/<item>.json`` frozen
    through the real plug-in parsers, and (``pngs``) a stand-in PNG per capture.
    Returns the chat messages and events written.
    """
    from datetime import UTC, datetime

    from src.activities.registry import options_with_defaults, parser, result_for
    from src.live.plan import build_run
    from src.sessions.readiness import slides_meta

    session = parse_session(PLAN)
    run = build_run(session, slides_meta(folder))
    live = folder / "live"
    (live / "captures").mkdir(parents=True, exist_ok=True)
    t = RUN_START_MS
    chat: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def at(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds")

    def clock(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")

    events.append({"at": at(t), "event": "session_live", "items": len(run["items"]), "index": 0,
                   "item_id": run["items"][0]["id"], "kind": run["items"][0]["kind"]})
    person = 0
    for item in run["items"]:
        if item["index"]:
            t += 60_000
            events.append({"at": at(t), "event": "item", "item_id": item["id"], "index": item["index"], "kind": item["kind"]})
        answers = RUN_ANSWERS.get(item["id"])
        if not answers:
            continue
        start = t + 5_000
        events.append({"at": at(start), "event": "capture_start", "item_id": item["id"]})
        mine = []
        for k, text in enumerate(answers):
            ms = start + 4_000 + k * 7_000
            msg = {"id": len(chat) + 1, "sender": demo_person(person % 14), "text": text, "time": clock(ms),
                   "received_at": ms, "at": at(ms), "source": "zoom", "own": False}
            person += 1
            chat.append(msg)
            mine.append(msg)
        stop = start + 4_000 + len(answers) * 7_000
        t = stop
        hidden = {m["id"] for m in mine if (item["id"], m["text"]) in RUN_HIDDEN}
        events.append({"at": at(stop), "event": "capture_stop", "item_id": item["id"], "answers": len(mine) - len(hidden)})
        mod = parser(item["type"])
        opts = options_with_defaults(item["type"], item["options"] or {})
        frozen = {
            "item": item, "frozen_at": at(stop), "windows": [[start, stop]], "names": False,
            "answers": [{"id": m["id"], "sender": m["sender"], "text": m["text"], "time": m["time"], "received_at": m["received_at"],
                         "hidden": m["id"] in hidden, "parsed": None if m["id"] in hidden else mod.parse(m, opts)} for m in mine],
            "result": result_for(item["type"], item["options"] or {}, [m for m in mine if m["id"] not in hidden]),
        }
        (live / "captures" / f"{item['id']}.json").write_text(json.dumps(frozen, ensure_ascii=False, indent=1), encoding="utf-8")
        if pngs:
            im = Image.new("RGB", (1920, 1080), (250, 250, 250))
            ImageDraw.Draw(im).text((110, 470), f"Live result · {item['title']}", font=_font(72), fill=INK)
            im.save(live / "captures" / f"{item['id']}.png")
    # the facilitator's own line ("You") is chat, never an answer
    chat.append({"id": len(chat) + 1, "sender": "You", "text": "Thanks everyone!", "time": clock(t + 30_000),
                 "received_at": t + 30_000, "at": at(t + 30_000), "source": "zoom", "own": True})
    events.append({"at": at(t + 60_000), "event": "session_closed"})
    (live / "chat.jsonl").write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in chat), encoding="utf-8")
    (live / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return {"chat": chat, "events": events}


def zoom_saved_chat(chat: list[dict[str, Any]], *, host: str = "Demo Host", layout: str = "to") -> str:
    """The chat as Zoom would save it (synthetic): ``to`` (text on its own line) or ``old`` (same line)."""
    from datetime import datetime

    lines = []
    for m in chat:
        hms = datetime.fromtimestamp(m["received_at"] / 1000).strftime("%H:%M:%S")
        who = host if m.get("own") else m["sender"]
        if layout == "to":
            lines += [f"{hms} From {who} to Everyone:", f"\t{m['text']}"]
        else:
            lines.append(f"{hms}\t From  {who} : {m['text']}")
    return "\n".join(lines) + "\n"


def build_demo_session(folder: Path, ledger: Path | None = None) -> tuple[str, Path]:
    """Write the demo session into ``folder`` (and the ledger, when given)."""
    slides_dir = folder / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("live", "exports"):
        (folder / sub).mkdir(exist_ok=True)
    export_slides = []
    for i, spec in enumerate(SLIDES, start=1):
        file = f"slide-{spec['id']}.png"
        draw_slide(spec, slides_dir / file)
        export_slides.append({"slide_id": spec["id"], "index": i, "title": spec["title"], "notes": "",
                              "texts": [spec["title"]], "pictures": 0 if spec.get("divider") else 1,
                              "background": "#f2f2f2", "hidden": False, "file": file})
    meta = build_slides_meta({"sections": [], "slides": export_slides}, slides_dir, Path("demo.pptx"))
    (slides_dir / "slides.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    write_demo_roster(folder / "roster.xlsx")
    session = parse_session(PLAN)
    from src.sessions.model import dump_session

    (folder / SESSION_FILE).write_text(yaml.safe_dump(dump_session(session), sort_keys=False, allow_unicode=True), encoding="utf-8")
    if ledger is not None:
        entries = []
        if ledger.is_file():
            entries = (yaml.safe_load(ledger.read_text(encoding="utf-8")) or {}).get("sessions") or []
        entries.append({"name": PLAN["title"], "path": str(folder)})
        ledger.write_text(yaml.safe_dump({"sessions": entries}, allow_unicode=True), encoding="utf-8")
    return session_id(folder), folder


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--ledger", type=Path)
    args = ap.parse_args()
    sid, folder = build_demo_session(args.root, args.ledger)
    print(f"demo session {sid} at {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_demo_session", "LedgerEntry"]
