"""Import a PowerPoint into a session folder, and fold it into the plan (epic §6).

``ImportJob`` runs the COM exporter (``src.importer.pptx_com``) as a child
process with a timeout, reports progress, then:

1. moves the PNGs into ``slides/`` and writes ``slides/slides.json`` — per
   slide: SlideID, index, title, notes, fingerprints and the detected OBS
   profile; a slide with plain text also keeps its text-free picture
   (``slide-<id>-bg.png``) and its text boxes in stage pixels, so the stage
   can draw that text in the session's font;
2. updates ``session.yaml``:
   - **first import** (empty plan): builds sections — PowerPoint sections
     when the deck has them, else a new section at every title-only divider
     slide — and turns *placeholder slides* into plan items: a slide titled
     ``activity – <question>`` (or the old ``streamalive N – <question>``)
     becomes an activity, ``breakout – <title>`` a breakout card, ``mentimeter …``
     a skipped slide;
   - **re-import**: nothing changes yet — the export is staged in
     ``slides/incoming/`` and the review screen (``review.py``) says what
     changed; each change is accepted one by one before it applies.
     Slide indexes limit the export to those slides. They are spliced into
     the current deck, and the review offers each of them.
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

from src.errors import DomainError
from src.importer.analyze import detect_profile, dhash, text_hash
from src.no_window import NO_WINDOW
from src.sessions.model import Item, Section, Session, Source, ensure_ids

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EXPORT_TIMEOUT_S = 300
INCOMING = "incoming"  # slides/incoming/: a re-import waiting for review

_DASH = r"\s*[\-–—:]\s*"
ACTIVITY_RE = re.compile(rf"^\s*(?:streamalive|activity|actividad)\s*\d*{_DASH}(.+)$", re.I | re.S)
BREAKOUT_RE = re.compile(rf"^\s*breakout\s*\d*{_DASH}(.+)$", re.I | re.S)
SKIP_RE = re.compile(r"^\s*mentimeter\b", re.I)
_MAP_WORDS = re.compile(r"\b(map|mapa|where|dónde|donde)\b", re.I)
_SCALE_WORDS = re.compile(r"\b(weather|tiempo|clima|scale|escala|1\s*[-–a]\s*5)\b", re.I)


class DeckImportError(DomainError):
    """A failed import with a user-facing message (distinct per condition); a second import while one runs is a 409."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(409 if code == "busy" else 422, code, message)


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
        return {"kind": "breakout", "title": " ".join(m.group(1).split())}
    if SKIP_RE.match(title or ""):
        return {"kind": "skip"}
    return None


def _is_divider(s: dict[str, Any]) -> bool:
    """A title-only slide (no picture, one short text) opens a new section."""
    return bool(s.get("title")) and s.get("pictures", 0) == 0 and len(s.get("texts") or []) == 1 \
        and len(s["title"]) <= 60 and classify_placeholder(s["title"]) is None


def run_exporter(deck: Path, out_dir: Path, progress: Callable[[int, int], None],
                 only: Optional[list[int]] = None) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "src.importer.pptx_com", str(deck), str(out_dir)]
    if only:
        cmd.append(",".join(str(i) for i in only))
    logger.info("ℹ️ import: exporting %s%s", deck.name, f" slides {only}" if only else "")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except OSError as exc:
        raise DeckImportError("spawn_failed", "The import process could not start") from exc
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
                parts = line.split()
                progress(int(parts[1]), int(parts[2]))
            elif line.startswith("ERROR "):
                last_error = line[6:]
                logger.warning("⚠️ import: %s", last_error)
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise DeckImportError("timeout", "PowerPoint did not finish within 5 minutes — is a dialog open in PowerPoint?")
    if code == 2:
        raise DeckImportError("powerpoint_unavailable", "PowerPoint is not available on this PC")
    if code == 3:
        raise DeckImportError("deck_unreadable", "PowerPoint could not open the deck")
    if code == 5:
        raise DeckImportError("slide_not_found", last_error or "This deck has no such slide")
    if code != 0:
        raise DeckImportError("export_failed", last_error or f"The export failed (exit {code})")
    return json.loads((out_dir / "export.json").read_text(encoding="utf-8"))


def stage_boxes(boxes: list[dict[str, Any]], width_pt: float, height_pt: float) -> list[dict[str, Any]]:
    """PowerPoint text boxes (points) → the stage's 1920×1080 pixels."""
    kx, ky = 1920 / (width_pt or 960), 1080 / (height_pt or 540)
    out = []
    for b in boxes:
        top, right, bottom, left = (b.get("pad") or [3.6, 7.2, 3.6, 7.2])[:4]
        out.append({
            "x": round(b["x"] * kx, 1), "y": round(b["y"] * ky, 1), "w": round(b["w"] * kx, 1), "h": round(b["h"] * ky, 1),
            "text": b["text"], "size": round(b["size"] * kx, 1), "color": b.get("color") or "#1f1f1f",
            "align": b.get("align") or "left", "anchor": b.get("anchor") or "top",
            "pad": [round(top * ky, 1), round(right * kx, 1), round(bottom * ky, 1), round(left * kx, 1)],
            "title": bool(b.get("title")),  # the stage letters it as a title, the rest as slide text
        })
    return out


def files_of(s: dict[str, Any]) -> list[str]:
    """A slide's picture files: the slide, and its text-free picture when it has one."""
    return [s["file"]] + ([s["bg_file"]] if s.get("bg_file") else [])


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
            **({"bg_file": s["bg_file"], "boxes": stage_boxes(s["boxes"], export.get("slide_width", 960), export.get("slide_height", 540))}
               if s.get("bg_file") and s.get("boxes") and (slides_dir / s["bg_file"]).is_file() else {}),
        })
    return {
        "source": str(deck), "imported_at": datetime.now().replace(microsecond=0).isoformat(),
        "sections": export.get("sections", []), "slides": slides,
    }


def merge_partial(old_meta: dict[str, Any], partial: dict[str, Any]) -> dict[str, Any]:
    """Splices a partial export into the current deck, by SlideID and then by index.

    Slides that were not exported stay as they are. ``refresh_ids`` names the
    exported slides so the review offers each one even when its fingerprint matches.
    """
    old = list(old_meta.get("slides") or [])
    new = list(partial.get("slides") or [])
    by_id = {s["slide_id"]: i for i, s in enumerate(old)}
    by_index: dict[int, int] = {}
    for i, s in enumerate(old):
        by_index.setdefault(int(s["index"]), i)
    replacement: dict[int, dict[str, Any]] = {}
    used: set[int] = set()

    def take(slot: int, slide: dict[str, Any]) -> None:
        if slot in replacement or slide["slide_id"] in used:
            return
        replacement[slot] = slide
        used.add(slide["slide_id"])

    for slide in new:
        slot = by_id.get(slide["slide_id"])
        if slot is not None:
            take(slot, slide)
    for slide in new:
        if slide["slide_id"] in used:
            continue
        slot = by_index.get(int(slide["index"]))
        if slot is not None:
            take(slot, slide)
    merged = [replacement.get(i, slide) for i, slide in enumerate(old)]
    merged.extend(slide for slide in new if slide["slide_id"] not in used)
    merged.sort(key=lambda slide: (int(slide.get("index") or 0), int(slide["slide_id"])))
    return {
        **old_meta,
        "source": partial.get("source") or old_meta.get("source", ""),
        "imported_at": partial.get("imported_at"),
        "slides": merged,
        "refresh_ids": [slide["slide_id"] for slide in new],
    }


def deck_status(source: str, imported_at: Optional[str]) -> str:
    """``newer``, ``missing``, ``current`` or ``unknown`` for a deck path.

    A relative path stays unknown: a fixture names its deck that way, and only
    a real path on this PC can be missing or newer than the import.
    """
    if not source:
        return "unknown"
    path = Path(source)
    if not path.is_absolute():
        return "unknown"
    if not path.is_file():
        return "missing"
    if not imported_at:
        return "newer"
    try:
        imported = datetime.fromisoformat(imported_at)
    except (TypeError, ValueError):
        return "unknown"
    modified = datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0)
    return "newer" if modified > imported else "current"


def _normalize_slides(only: Optional[list[int]]) -> Optional[list[int]]:
    if only is None:
        return None
    if not only:
        raise DeckImportError("no_slides", "Pick at least one slide")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in only):
        raise DeckImportError("bad_slide", "Slide numbers start at 1")
    return list(dict.fromkeys(only))


def slide_item(s: dict[str, Any]) -> Item:
    return Item(kind="slide", slide_id=s["slide_id"], profile=s["profile"], include=not s["hidden"])


def placeholder_item(s: dict[str, Any], ph: dict[str, Any]) -> Optional[Item]:
    if ph["kind"] == "skip":
        return Item(kind="slide", slide_id=s["slide_id"], include=False, profile=s["profile"])
    if ph["kind"] == "activity":
        return Item(kind="activity", type=ph["type"], question=ph["question"], title=ph["title"],
                    chat_prompt="", profile="camera_pip")
    return Item(kind=ph["kind"], title=ph["title"])  # a breakout (or an older export's break)


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
        item = placeholder_item(s, ph) if ph else slide_item(s)
        if item is not None:
            sections[-1].items.append(item)
    # Planned minutes: the session's duration split by item count, rounded to 5.
    total = sum(len(sec.items) for sec in sections) or 1
    for sec in sections:
        sec.minutes = max(5, int(round(duration_minutes * len(sec.items) / total / 5.0)) * 5)
    return sections


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
    only: Optional[list[int]] = None
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

    def start(self, session_id: str, deck: str, only: Optional[list[int]] = None) -> ImportJob:
        path = Path(deck.strip().strip('"'))
        only = _normalize_slides(only)
        if path.suffix.lower() not in (".pptx", ".ppt", ".pptm"):
            raise DeckImportError("not_a_deck", "Choose a PowerPoint file (.pptx)")
        if not path.is_file():
            raise DeckImportError("deck_not_found", "That PowerPoint file does not exist")
        with self._lock:
            if any(j.state in ("queued", "running") for j in self._jobs.values()):
                raise DeckImportError("busy", "An import is already running")
            job = ImportJob(id=uuid.uuid4().hex[:10], session_id=session_id, deck=str(path), only=only)
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
            first = not session.all_items()
            if job.only and first:
                raise DeckImportError("selection_needs_import",
                                   "Import the whole PowerPoint first, then re-import the slides you pick")
            slides_dir = folder / "slides"
            staged = slides_dir / INCOMING
            shutil.rmtree(staged, ignore_errors=True)  # a newer export replaces one waiting for review
            target = slides_dir if first else staged
            with tempfile.TemporaryDirectory(prefix="fs-export-") as tmp:
                out = Path(tmp)

                def progress(i: int, n: int) -> None:
                    job.done, job.total = i, n
                    job.message = f"Exporting slide {i} of {n}"

                export = self._exporter(Path(job.deck), out, progress, job.only)
                job.message = "Analysing slides…"
                target.mkdir(parents=True, exist_ok=True)
                if first:
                    keep = {f for s in export["slides"] for f in files_of(s)}
                    for old in slides_dir.glob("slide-*.png"):
                        if old.name not in keep:
                            old.unlink()
                for s in export["slides"]:
                    for name in files_of(s):
                        shutil.copy2(out / name, target / name)
            meta = build_slides_meta(export, target, Path(job.deck))
            if job.only:
                stored = slides_dir / "slides.json"
                if not stored.is_file():
                    raise DeckImportError("slides_missing", "Import the PowerPoint before re-importing selected slides")
                meta = merge_partial(json.loads(stored.read_text(encoding="utf-8")), meta)
            (target / "slides.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            exported = len(export["slides"])
            unsure = sum(1 for s in meta["slides"] if s["profile"] is None and not s.get("placeholder"))
            if first:
                session.sections = first_plan(meta, session.duration_minutes)
                session.source = Source(pptx=job.deck, imported_at=datetime.now().replace(microsecond=0))
                self._save(job.session_id, ensure_ids(session))
                summary = {"first_import": True, "review": False, "sections": len(session.sections),
                           "items": len(session.all_items())}
                job.message = f"Imported {exported} slides"
            else:
                summary = {"first_import": False, "review": True}
                job.message = f"Exported {exported} slides — review the changes"
            job.result = {"slides": exported, "unsure_profiles": unsure, **summary}
            job.state = "done"
            logger.info("✅ import: %s slides for session %s (%s)", exported, job.session_id, summary)
        except DeckImportError as exc:
            job.state, job.code, job.message = "error", exc.code, str(exc)
            logger.error("❌ import failed (%s): %s", exc.code, exc)
        except Exception as exc:  # noqa: BLE001 — surfaced to the UI, detail in the log
            job.state, job.code, job.message = "error", "import_failed", "The import failed — see the log"
            logger.exception("❌ import crashed: %s", exc)
