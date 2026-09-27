"""Re-import review (epic §6): what changed in the deck, applied change by change.

A re-import no longer touches the session: the export is **staged** in
``slides/incoming/`` (PNGs + ``slides.json``), and ``diff`` compares it with
the current ``slides/slides.json`` and the plan:

- slides are matched by PowerPoint's own SlideID first, then — for a slide
  that was re-created and got a new id — by fingerprints (image dHash close
  and the same title or notes);
- **identical**: same image, title and notes; **modified**: says which of the
  three changed; **new**; **removed**; **moved**: a matched slide whose place
  in the deck order changed (the smallest set of moves that explains the new
  order, so one moved slide doesn't flag all the others).

Each change can be accepted or not. ``apply`` then writes the final
``slides.json`` and PNGs and updates the plan:

- a slide's plan **block** is the slide item plus the items after it up to the
  next slide (the activities anchored to it);
- removed (accepted): the slide item leaves the plan, its activities stay
  where they are — so they now follow the previous surviving slide; not
  accepted: the old slide and its image are kept;
- moved (accepted): the block moves to follow its new deck predecessor;
- new (accepted): a slide item (or the activity/break of a placeholder slide)
  is inserted after its deck predecessor's block; not accepted: left out, so
  the next re-import offers it again;
- modified (not accepted): the old image, title and notes are kept.

A slide matched by fingerprint has its plan items re-pointed at the new id —
bookkeeping, not a change to review.
"""

from __future__ import annotations

import bisect
import difflib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

from src.importer.analyze import hamming
from src.importer.service import INCOMING, placeholder_item, slide_item
from src.sessions.model import Item, Section, Session

logger = logging.getLogger(__name__)

IMAGE_SAME = 3  # dHash bits: re-export noise stays under this
IMAGE_SIMILAR = 10  # a re-created slide with the same drawing
PROFILE_LABEL = {"camera_strip": "Camera strip", "camera_pip": "Camera PiP", "screen_only": "Screen only"}


class ReviewError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


# ---- staging -----------------------------------------------------------------

def incoming_dir(folder: Path) -> Path:
    return folder / "slides" / INCOMING


def read_meta(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("❌ re-import: %s unreadable (%s)", path, exc)
        return None


def pending(folder: Path) -> Optional[dict[str, Any]]:
    return read_meta(incoming_dir(folder) / "slides.json")


def discard(folder: Path) -> None:
    shutil.rmtree(incoming_dir(folder), ignore_errors=True)
    logger.info("ℹ️ re-import: staged export discarded (%s)", folder.name)


# ---- matching ----------------------------------------------------------------

def _img(a: dict[str, Any], b: dict[str, Any]) -> int:
    try:
        return hamming(a["fp"]["image"], b["fp"]["image"])
    except (KeyError, TypeError, ValueError):
        return 64


def match(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[int, int]:
    """new SlideID → old SlideID, by id first, then by fingerprint."""
    old_ids = {s["slide_id"] for s in old}
    pairs = {s["slide_id"]: s["slide_id"] for s in new if s["slide_id"] in old_ids}
    rest_old = [s for s in old if s["slide_id"] not in pairs.values()]
    rest_new = [s for s in new if s["slide_id"] not in pairs]
    candidates = []
    for n in rest_new:
        for o in rest_old:
            d = _img(o, n)
            same_title = bool(o.get("raw_title") or o.get("title")) and o["fp"].get("title") == n["fp"].get("title")
            same_notes = bool(o.get("notes")) and o["fp"].get("notes") == n["fp"].get("notes")
            if d <= IMAGE_SIMILAR and (same_title or same_notes):
                candidates.append((d, -(same_title + same_notes), n["slide_id"], o["slide_id"]))
    used_old: set[int] = set()
    for _, _, nid, oid in sorted(candidates):
        if nid not in pairs and oid not in used_old:
            pairs[nid] = oid
            used_old.add(oid)
    return pairs


def _stable(seq: list[int]) -> set[int]:
    """Positions (in ``seq``) of a longest increasing subsequence: the slides that did not move."""
    if not seq:
        return set()
    tails: list[int] = []
    tails_at: list[int] = []
    prev = [-1] * len(seq)
    for i, v in enumerate(seq):
        k = bisect.bisect_left(tails, v)
        if k == len(tails):
            tails.append(v)
            tails_at.append(i)
        else:
            tails[k] = v
            tails_at[k] = i
        prev[i] = tails_at[k - 1] if k else -1
    out, i = set(), tails_at[-1]
    while i != -1:
        out.add(i)
        i = prev[i]
    return out


def _notes_change(a: str, b: str) -> str:
    al, bl = [x for x in (a or "").splitlines() if x.strip()], [x for x in (b or "").splitlines() if x.strip()]
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=al, b=bl).get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    parts = [f"{added} line{'s' if added != 1 else ''} added" if added else "",
             f"{removed} line{'s' if removed != 1 else ''} removed" if removed else ""]
    return " · ".join(p for p in parts if p) or "edited"


# ---- the plan's blocks ---------------------------------------------------------

def _locate(session: Session, slide_id: int) -> Optional[tuple[Section, int]]:
    for sec in session.sections:
        for k, it in enumerate(sec.items):
            if it.kind == "slide" and it.slide_id == slide_id:
                return sec, k
    return None


def _block_end(sec: Section, k: int) -> int:
    """Index after the block that starts at ``sec.items[k]`` (the slide + its anchored items)."""
    j = k + 1
    while j < len(sec.items) and sec.items[j].kind != "slide":
        j += 1
    return j


def _followers(session: Session, slide_id: int) -> list[Item]:
    loc = _locate(session, slide_id)
    if loc is None:
        return []
    sec, k = loc
    return sec.items[k + 1:_block_end(sec, k)]


def _prev_plan_slide(session: Session, slide_id: int) -> Optional[int]:
    prev = None
    for it in session.all_items():
        if it.kind == "slide" and it.slide_id == slide_id:
            return prev
        if it.kind == "slide":
            prev = it.slide_id
    return None


# ---- the diff ------------------------------------------------------------------

def _brief(s: dict[str, Any]) -> dict[str, Any]:
    return {"slide_id": s["slide_id"], "index": s["index"], "title": s["title"], "file": s["file"]}


def diff(old_meta: Optional[dict[str, Any]], new_meta: dict[str, Any], session: Session) -> dict[str, Any]:
    old = (old_meta or {}).get("slides") or []
    new = new_meta.get("slides") or []
    old_by = {s["slide_id"]: s for s in old}
    pairs = match(old, new)
    in_plan = {it.slide_id for it in session.all_items() if it.kind == "slide"}
    changes: list[dict[str, Any]] = []
    identical: list[dict[str, Any]] = []

    # moved: matched slides, in new order, whose old order is not increasing
    matched_new = [s for s in new if s["slide_id"] in pairs]
    stable = _stable([old_by[pairs[s["slide_id"]]]["index"] for s in matched_new])
    moved_ids = {s["slide_id"] for i, s in enumerate(matched_new) if i not in stable}

    for pos, s in enumerate(new):
        nid = s["slide_id"]
        if nid not in pairs:
            ph = s.get("placeholder")
            prev = next((p for p in reversed(new[:pos]) if p["slide_id"] in pairs), None)
            if ph and ph.get("kind") == "activity":
                detail = f"New activity slide · becomes a {ph['type'].replace('_', ' ')}"
            elif ph and ph.get("kind") == "break":
                detail = "New breakout slide · becomes a break"
            elif ph:
                detail = "New slide · skipped in the plan"
            else:
                prof = PROFILE_LABEL.get(s.get("profile") or "", "")
                detail = f"New slide · {prof.lower()} detected" if prof else "New slide · OBS profile unsure"
            changes.append({"id": f"new-{nid}", "kind": "new", "slide_id": nid, "old": None, "new": _brief(s),
                            "title": s["title"], "position": s["index"], "after": prev["index"] if prev else None,
                            "detail": detail})
            continue
        o = old_by[pairs[nid]]
        what = []
        if _img(o, s) > IMAGE_SAME:
            what.append("Image changed")
        if o["fp"].get("title") != s["fp"].get("title"):
            what.append(f"Title changed from “{o['title']}”")
        if o["fp"].get("notes") != s["fp"].get("notes"):
            what.append(f"Notes changed · {_notes_change(o.get('notes', ''), s.get('notes', ''))}")
        brief = {"old": _brief(o), "new": _brief(s), "title": s["title"], "position": s["index"]}
        if what:
            changes.append({"id": f"mod-{nid}", "kind": "modified", "slide_id": nid, **brief, "detail": " · ".join(what)})
        if nid in moved_ids:
            changes.append({"id": f"mov-{nid}", "kind": "moved", "slide_id": nid, **brief,
                            "detail": f"Was slide {o['index']}, now slide {s['index']}"
                                      + ("" if o["slide_id"] in in_plan else " · not in the plan")})
        if not what and nid not in moved_ids:
            identical.append({"slide_id": nid, "index": s["index"], "title": s["title"],
                              "rematched": pairs[nid] != nid})

    for o in old:
        if o["slide_id"] in pairs.values():
            continue
        if o["slide_id"] not in in_plan:
            detail = "It was not in the plan"
        else:
            follow = _followers(session, o["slide_id"])
            prev = _prev_plan_slide(session, o["slide_id"])
            prev_meta = old_by.get(prev) if prev is not None else None
            where = f"slide {prev_meta['index']}" if prev_meta else "the start of its section"
            if follow:
                n = len(follow)
                detail = f"The {'activity' if n == 1 else f'{n} items'} after it move{'s' if n == 1 else ''} to follow {where}"
            else:
                detail = "It leaves the plan"
        changes.append({"id": f"rem-{o['slide_id']}", "kind": "removed", "slide_id": o["slide_id"], "old": _brief(o),
                        "new": None, "title": o["title"], "position": o["index"], "detail": detail})

    order = {"modified": 0, "new": 1, "removed": 2, "moved": 3}
    changes.sort(key=lambda c: (c["position"], order[c["kind"]]))
    counts = {"identical": len(identical), **{k: sum(1 for c in changes if c["kind"] == k) for k in order}}
    profiles: dict[str, int] = {}
    for s in new:
        if not s.get("placeholder"):
            key = s.get("profile") or "unsure"
            profiles[key] = profiles.get(key, 0) + 1
    return {"source": new_meta.get("source", ""), "imported_at": new_meta.get("imported_at"), "slides": len(new),
            "changes": changes, "identical": identical, "counts": counts, "profiles": profiles}


# ---- apply ---------------------------------------------------------------------

def _new_item(s: dict[str, Any]) -> Item:
    """A new deck slide's plan item: the slide, or what its placeholder title stands for."""
    ph = s.get("placeholder")
    return placeholder_item(s, ph) if ph else slide_item(s)


def _move_block(session: Session, slide_id: int, after: Optional[int]) -> None:
    loc = _locate(session, slide_id)
    if loc is None:
        return
    sec, k = loc
    end = _block_end(sec, k)
    block = sec.items[k:end]
    del sec.items[k:end]
    _insert(session, block, after)


def _insert(session: Session, items: list[Item], after: Optional[int]) -> None:
    """Put ``items`` after the block of slide ``after`` (or at the very start)."""
    loc = _locate(session, after) if after is not None else None
    if loc is None:
        if not session.sections:
            session.sections.append(Section(name="Slides", items=[]))
        session.sections[0].items[0:0] = items
        return
    sec, k = loc
    end = _block_end(sec, k)
    sec.items[end:end] = items


def apply(folder: Path, session: Session, accepted: set[str]) -> dict[str, Any]:
    """Apply the accepted changes: copies the PNGs and updates ``session`` in place.

    Returns a summary with ``meta`` — the new ``slides.json`` — which the
    caller writes together with the session.
    """
    new_meta = pending(folder)
    if new_meta is None:
        raise ReviewError(404, "no_pending_import", "There is no re-import waiting for review")
    slides_dir = folder / "slides"
    inc = incoming_dir(folder)
    old_meta = read_meta(slides_dir / "slides.json") or {"slides": []}
    report = diff(old_meta, new_meta, session)
    known = {c["id"] for c in report["changes"]}
    unknown = accepted - known
    if unknown:
        raise ReviewError(409, "stale_review", "The deck changed since this review was opened — reload it")
    old_by = {s["slide_id"]: s for s in old_meta["slides"]}
    pairs = match(old_meta["slides"], new_meta["slides"])

    def ok(change_id: str) -> bool:
        return change_id in accepted

    # 1. slides.json + PNGs
    final: list[dict[str, Any]] = []
    copy_in: list[str] = []
    for s in new_meta["slides"]:
        nid = s["slide_id"]
        if nid not in pairs:
            if ok(f"new-{nid}"):
                final.append(s)
                copy_in.append(s["file"])
            continue
        o = old_by[pairs[nid]]
        if f"mod-{nid}" in known and not ok(f"mod-{nid}"):
            final.append({**o, "slide_id": nid, "index": s["index"]})  # keep the old look, under the deck's id
        else:
            final.append(s)
            copy_in.append(s["file"])
    for o in old_meta["slides"]:
        if o["slide_id"] not in pairs.values() and not ok(f"rem-{o['slide_id']}"):
            final.append({**o, "in_deck": False})
    for name in copy_in:
        shutil.copy2(inc / name, slides_dir / name)
    keep = {s["file"] for s in final}
    for png in slides_dir.glob("slide-*.png"):
        if png.name not in keep:
            png.unlink()
    meta = {**new_meta, "slides": final}

    # 2. the plan: re-point re-created slides, then remove, move, add
    for nid, oid in pairs.items():
        if nid != oid:
            for it in session.all_items():
                if it.kind == "slide" and it.slide_id == oid:
                    it.slide_id = nid
    removed = 0
    for o in old_meta["slides"]:
        if o["slide_id"] not in pairs.values() and ok(f"rem-{o['slide_id']}"):
            for sec in session.sections:
                before = len(sec.items)
                sec.items = [it for it in sec.items if not (it.kind == "slide" and it.slide_id == o["slide_id"])]
                removed += before - len(sec.items)
    deck = [s["slide_id"] for s in new_meta["slides"]]

    def predecessor(pos: int) -> Optional[int]:
        present = {it.slide_id for it in session.all_items() if it.kind == "slide"}
        return next((deck[j] for j in range(pos - 1, -1, -1) if deck[j] in present), None)

    moved = added = 0
    for pos, nid in enumerate(deck):
        if ok(f"mov-{nid}"):
            _move_block(session, nid, predecessor(pos))
            moved += 1
    for pos, nid in enumerate(deck):
        if ok(f"new-{nid}"):
            # a placeholder slide becomes its activity/break, placed where the slide would be
            _insert(session, [_new_item(new_meta["slides"][pos])], predecessor(pos))
            added += 1
    modified = sum(1 for c in report["changes"] if c["kind"] == "modified" and ok(c["id"]))
    return {"meta": meta, "added": added, "removed": removed, "moved": moved, "modified": modified,
            "declined": len(known) - len(accepted), "slides": len(new_meta["slides"])}
