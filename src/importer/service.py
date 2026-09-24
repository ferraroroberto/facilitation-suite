"""Import a PowerPoint into a session folder, and fold it into the plan (epic §6).

``ImportJob`` runs the COM exporter (``src.importer.pptx_com``) as a child
process with a timeout, reports progress, then:

1. moves the PNGs into ``slides/`` and writes ``slides/slides.json`` — per
   slide: SlideID, index, title, notes, fingerprints and the detected OBS
   profile;
2. updates ``session.yaml``:
   - **first import** (empty plan): builds sections — PowerPoint sections
     when the deck has them, else a new section at every title-only divider
     slide — and turns *placeholder slides* into plan items: a slide titled
     ``activity – <question>`` (or the old ``streamalive N – <question>``)
     becomes an activity, ``breakout – <title>`` a break, ``mentimeter …`` a
     skipped slide;
   - **re-import** (simple, step 3): slides are matched by SlideID; surviving
     slide items stay where they are (the plan order is the user's), removed
     slides drop out (an activity after them now follows the previous
     surviving slide), new slides are inserted after the slide that precedes
     them in the deck. The full review screen is step 13.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.importer.analyze import detect_profile, dhash, text_hash
from src.no_window import NO_WINDOW
from src.sessions.model import Item, Section, Session, Source, ensure_ids

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXPORT_TIMEOUT_S = 300

_DASH = r"\s*[\-–—:]\s*"
ACTIVITY_RE = re.compile(rf"^\s*(?:streamalive|activity|actividad)\s*\d*{_DASH}(.+)$", re.I | re.S)
BREAKOUT_RE = re.compile(rf"^\s*breakout\s*\d*{_DASH}(.+)$", re.I | re.S)
SKIP_RE = re.compile(r"^\s*mentimeter\b", re.I)
_MAP_WORDS = re.compile(r"\b(map|mapa|where|dónde|donde)\b", re.I)
_SCALE_WORDS = re.compile(r"\b(weather|tiempo|clima|scale|escala|1\s*[-–a]\s*5)\b", re.I)


class ImportError_(Exception):
    """A failed import with a user-facing message (distinct per condition)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def display_title(s: dict[str, Any]) -> str:
    title = (s.get("title") or "").strip()
    if title:
        return " ".join(title.split())
    for t in s.get("texts") or []:
        t = " ".join(t.split())
        if t:
            return t[:80]
    return f"Slide {s['index']}"


def classify_placeholder(title: str) -> Optional[dict[str, Any]]:
    """A placeholder slide → the item it stands for, else None."""
    m = ACTIVITY_RE.match(title or "")
    if m:
        question = " ".join(m.group(1).split())
        kind = "map" if _MAP_WORDS.search(question) else "scale" if _SCALE_WORDS.search(question) else "word_cloud"
        q = question[0].upper() + question[1:] if question else question
        return {"kind": "activity", "type": kind, "question": q, "title": q}
    m = BREAKOUT_RE.match(title or "")
    if m:
        return {"kind": "break", "title": "Breakout · " + " ".join(m.group(1).split())}
    if SKIP_RE.match(title or ""):
        return {"kind": "skip"}
    return None


def _is_divider(s: dict[str, Any]) -> bool:
    """A title-only slide (no picture, one short text) opens a new section."""
    return bool(s.get("title")) and s.get("pictures", 0) == 0 and len(s.get("texts") or []) == 1 \
        and len(s["title"]) <= 60 and classify_placeholder(s["title"]) is None


def run_exporter(deck: Path, out_dir: Path, progress: Callable[[int, int], None]) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "src.importer.pptx_com", str(deck), str(out_dir)]
    logger.info("ℹ️ import: exporting %s", deck.name)
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except OSError as exc:
        raise ImportError_("spawn_failed", "The import process could not start") from exc
    last_error = ""
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(EXPORT_TIMEOUT_S, _kill)
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS "):
                _, i, n = line.split()
                progress(int(i), int(n))
            elif line.startswith("ERROR "):
                last_error = line[6:]
                logger.warning("⚠️ import: %s", last_error)
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise ImportError_("timeout", "PowerPoint did not finish within 5 minutes — is a dialog open in PowerPoint?")
    if code == 2:
        raise ImportError_("powerpoint_unavailable", "PowerPoint is not available on this PC")
    if code == 3:
        raise ImportError_("deck_unreadable", "PowerPoint could not open the deck")
    if code != 0:
        raise ImportError_("export_failed", last_error or f"The export failed (exit {code})")
    return json.loads((out_dir / "export.json").read_text(encoding="utf-8"))


def build_slides_meta(export: dict[str, Any], slides_dir: Path, deck: Path) -> dict[str, Any]:
    slides = []
    for s in export["slides"]:
        png = slides_dir / s["file"]
        guess = detect_profile(png, s.get("notes", ""))
        slides.append({
            "slide_id": s["slide_id"], "index": s["index"], "title": display_title(s),
            "raw_title": s.get("title", ""), "notes": s.get("notes", ""), "file": s["file"],
            "hidden": s.get("hidden", False), "pictures": s.get("pictures", 0), "texts": s.get("texts", []),
            "background": s.get("background", ""),
            "fp": {"image": dhash(png), "title": text_hash(s.get("title", "")), "notes": text_hash(s.get("notes", ""))},
            "profile": guess.profile, "profile_source": guess.source, "zone": guess.zone,
            "placeholder": classify_placeholder(s.get("title", "")),
        })
    return {
        "source": str(deck), "imported_at": datetime.now().replace(microsecond=0).isoformat(),
        "sections": export.get("sections", []), "slides": slides,
    }


def _slide_item(s: dict[str, Any]) -> Item:
    return Item(kind="slide", slide_id=s["slide_id"], profile=s["profile"], include=not s["hidden"])


def _placeholder_item(s: dict[str, Any], ph: dict[str, Any]) -> Optional[Item]:
    if ph["kind"] == "skip":
        return Item(kind="slide", slide_id=s["slide_id"], include=False, profile=s["profile"])
    if ph["kind"] == "activity":
        return Item(kind="activity", type=ph["type"], question=ph["question"], title=ph["title"],
                    chat_prompt="", profile="camera_pip")
    return Item(kind="break", title=ph["title"])


def first_plan(meta: dict[str, Any], duration_minutes: int) -> list[Section]:
    slides = meta["slides"]
    starts: dict[int, str] = {}
    if meta.get("sections"):
        for sec in meta["sections"]:
            starts[sec["first"]] = sec["name"]
    else:
        for s in slides:
            if _is_divider(s):
                starts[s["index"]] = s["title"]
    sections: list[Section] = []
    for s in slides:
        if s["index"] in starts or not sections:
            sections.append(Section(name=starts.get(s["index"], "Opening"), items=[]))
        ph = s.get("placeholder")
        item = _placeholder_item(s, ph) if ph else _slide_item(s)
        if item is not None:
            sections[-1].items.append(item)
    # Planned minutes: the session's duration split by item count, rounded to 5.
    total = sum(len(sec.items) for sec in sections) or 1
    for sec in sections:
        sec.minutes = max(5, int(round(duration_minutes * len(sec.items) / total / 5.0)) * 5)
    return sections


def merge_reimport(session: Session, meta: dict[str, Any]) -> dict[str, int]:
    """Simple re-import: keep plan order, drop removed slides, insert new ones."""
    by_id = {s["slide_id"]: s for s in meta["slides"]}
    deck_order = [s["slide_id"] for s in meta["slides"]]
    present = {it.slide_id for it in session.all_items() if it.kind == "slide"}
    removed = 0
    for sec in session.sections:
        before = len(sec.items)
        sec.items = [it for it in sec.items if not (it.kind == "slide" and it.slide_id not in by_id)]
        removed += before - len(sec.items)
    added = 0
    for pos, sid in enumerate(deck_order):
        if sid in present:
            continue
        s = by_id[sid]
        if s.get("placeholder"):
            continue
        new_item = _slide_item(s)
        # after the nearest preceding deck slide that is in the plan
        anchor = next((deck_order[j] for j in range(pos - 1, -1, -1) if deck_order[j] in present), None)
        placed = False
        if anchor is not None:
            for sec in session.sections:
                for k, it in enumerate(sec.items):
                    if it.kind == "slide" and it.slide_id == anchor:
                        sec.items.insert(k + 1, new_item)
                        placed = True
                        break
                if placed:
                    break
        if not placed:
            if not session.sections:
                session.sections.append(Section(name="Slides", items=[]))
            session.sections[0].items.insert(0, new_item)
        present.add(sid)
        added += 1
    return {"added": added, "removed": removed, "kept": len(present) - added}


@dataclass
class ImportJob:
    id: str
    session_id: str
    deck: str
    state: str = "queued"  # queued | running | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    code: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "session_id": self.session_id, "state": self.state, "done": self.done,
                "total": self.total, "message": self.message, "code": self.code, "result": self.result}


class Importer:
    """One import at a time per app; jobs are polled by the UI."""

    def __init__(self, load: Callable[[str], Session], save: Callable[[str, Session], Any],
                 folder: Callable[[str], Path], exporter: Callable[..., dict[str, Any]] = run_exporter) -> None:
        self._load, self._save, self._folder, self._exporter = load, save, folder, exporter
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.Lock()

    def start(self, session_id: str, deck: str) -> ImportJob:
        path = Path(deck.strip().strip('"'))
        if path.suffix.lower() not in (".pptx", ".ppt", ".pptm"):
            raise ImportError_("not_a_deck", "Choose a PowerPoint file (.pptx)")
        if not path.is_file():
            raise ImportError_("deck_not_found", "That PowerPoint file does not exist")
        with self._lock:
            if any(j.state in ("queued", "running") for j in self._jobs.values()):
                raise ImportError_("busy", "An import is already running")
            job = ImportJob(id=uuid.uuid4().hex[:10], session_id=session_id, deck=str(path))
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"import-{job.id}").start()
        return job

    def get(self, job_id: str) -> Optional[ImportJob]:
        return self._jobs.get(job_id)

    def _run(self, job: ImportJob) -> None:
        job.state = "running"
        job.message = "Opening the deck in PowerPoint…"
        try:
            folder = self._folder(job.session_id)
            session = self._load(job.session_id)
            slides_dir = folder / "slides"
            with tempfile.TemporaryDirectory(prefix="fs-export-") as tmp:
                out = Path(tmp)

                def progress(i: int, n: int) -> None:
                    job.done, job.total = i, n
                    job.message = f"Exporting slide {i} of {n}"

                export = self._exporter(Path(job.deck), out, progress)
                job.message = "Analysing slides…"
                slides_dir.mkdir(parents=True, exist_ok=True)
                keep = {s["file"] for s in export["slides"]}
                for old in slides_dir.glob("slide-*.png"):
                    if old.name not in keep:
                        old.unlink()
                for s in export["slides"]:
                    shutil.copy2(out / s["file"], slides_dir / s["file"])
            meta = build_slides_meta(export, slides_dir, Path(job.deck))
            (slides_dir / "slides.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

            first = not session.all_items()
            if first:
                session.sections = first_plan(meta, session.duration_minutes)
                summary = {"first_import": True, "sections": len(session.sections),
                           "items": len(session.all_items())}
            else:
                summary = {"first_import": False, **merge_reimport(session, meta)}
            session.source = Source(pptx=job.deck, imported_at=datetime.now().replace(microsecond=0))
            self._save(job.session_id, ensure_ids(session))
            unsure = sum(1 for s in meta["slides"] if s["profile"] is None and not s.get("placeholder"))
            job.result = {"slides": len(meta["slides"]), "unsure_profiles": unsure, **summary}
            job.state = "done"
            job.message = f"Imported {len(meta['slides'])} slides"
            logger.info("✅ import: %s slides into session %s (%s)", len(meta["slides"]), job.session_id, summary)
        except ImportError_ as exc:
            job.state, job.code, job.message = "error", exc.code, str(exc)
            logger.error("❌ import failed (%s): %s", exc.code, exc)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI, detail in the log
            job.state, job.code, job.message = "error", "import_failed", "The import failed — see the log"
            logger.exception("❌ import crashed: %s", exc)
