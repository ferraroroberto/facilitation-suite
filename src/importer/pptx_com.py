"""PowerPoint COM export — run as its own process so a stuck PowerPoint can be killed.

    python -m src.importer.pptx_com <deck.pptx> <out_dir>

Opens a *copy* of the deck read-only and without a window (the original may
be open for editing, and a read-only open of a locked file can raise a modal
prompt that would hang the automation), then for every slide writes
``slide-<SlideID>.png`` at 1920×1080 and one ``export.json`` with the slide's
SlideID, index, title, notes, texts, picture count and background colour.

A slide with plain text shapes also gets ``slide-<SlideID>-bg.png`` — the
same slide with those shapes hidden — and their ``boxes`` (position and size
in points, text, size, colour, alignment), so the stage can draw that text
itself in the session's font. Text inside groups, tables or rotated shapes
stays in the picture.
Progress lines ``PROGRESS <i> <n>`` go to stdout. PowerPoint is quit only when
this process started it (no other presentation open).

Exit codes: 0 ok · 2 PowerPoint unavailable · 3 the deck could not be opened ·
4 export failed.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


def _text(shape: Any) -> str:
    try:
        if shape.HasTextFrame and shape.TextFrame.HasText:
            return str(shape.TextFrame.TextRange.Text).replace("\r", "\n").replace("\x0b", "\n").strip()
    except Exception:  # noqa: BLE001 — COM raises on shapes without the property
        pass
    return ""


TEXT_SHAPES = (1, 14, 17)  # msoAutoShape, msoPlaceholder, msoTextBox
_ALIGN = {1: "left", 2: "center", 3: "right", 4: "justify", 5: "justify"}
_ANCHOR = {1: "top", 2: "top", 3: "middle", 4: "bottom", 5: "bottom"}


def _rgb(bgr: int) -> str:
    return f"#{bgr & 0xFF:02x}{(bgr >> 8) & 0xFF:02x}{(bgr >> 16) & 0xFF:02x}"


def _is_title(shape: Any) -> bool:
    """The slide's title placeholder (ppPlaceholderTitle 1, ppPlaceholderCenterTitle 3)."""
    try:
        return int(shape.PlaceholderFormat.Type) in (1, 3)
    except Exception:  # noqa: BLE001 — not a placeholder
        return False


def _box(shape: Any) -> dict[str, Any] | None:
    """A plain, unrotated text shape as a box the stage can redraw; else None."""
    try:
        if int(shape.Type) not in TEXT_SHAPES or float(shape.Rotation) != 0 or not _text(shape):
            return None
        tf = shape.TextFrame
        tr = tf.TextRange
        lines = []
        for i in range(1, int(tr.Paragraphs().Count) + 1):
            p = tr.Paragraphs(i)
            text = str(p.Text).replace("\r", "").replace("\x0b", "\n")
            bullet = text.strip() and int(p.ParagraphFormat.Bullet.Visible) == -1
            lines.append(("• " if bullet else "") + text)
        run = tr.Runs(1).Font
        return {
            "x": float(shape.Left), "y": float(shape.Top), "w": float(shape.Width), "h": float(shape.Height),
            "text": "\n".join(lines).strip("\n"), "size": float(run.Size), "bold": int(run.Bold) == -1,
            "italic": int(run.Italic) == -1, "color": _rgb(int(run.Color.RGB)),
            "align": _ALIGN.get(int(tr.Paragraphs(1).ParagraphFormat.Alignment), "left"),
            "anchor": _ANCHOR.get(int(tf.VerticalAnchor), "top"),
            "pad": [float(tf.MarginTop), float(tf.MarginRight), float(tf.MarginBottom), float(tf.MarginLeft)],
            "title": _is_title(shape),
        }
    except Exception:  # noqa: BLE001 — COM raises on shapes without these properties: leave it in the picture
        return None


def _notes(slide: Any) -> str:
    try:
        for sh in slide.NotesPage.Shapes:
            try:
                if sh.PlaceholderFormat.Type == 2:  # ppPlaceholderBody
                    return _text(sh)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return ""


def _sections(pres: Any) -> list[dict[str, Any]]:
    out = []
    try:
        sp = pres.SectionProperties
        for i in range(1, sp.Count + 1):
            out.append({"name": str(sp.Name(i)), "first": int(sp.FirstSlide(i)), "count": int(sp.SlidesCount(i))})
    except Exception:  # noqa: BLE001
        return []
    return out


def export(deck: Path, out_dir: Path) -> int:
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        print("ERROR pywin32 is not installed", flush=True)
        return 2
    pythoncom.CoInitialize()
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR PowerPoint is not available: {exc}", flush=True)
        return 2
    started_empty = False
    try:
        started_empty = int(app.Presentations.Count) == 0
    except Exception:  # noqa: BLE001
        pass

    tmp_dir = Path(tempfile.mkdtemp(prefix="fs-import-"))
    copy = tmp_dir / "deck.pptx"
    shutil.copy2(deck, copy)
    pres = None
    try:
        try:
            # ReadOnly=True, Untitled=False, WithWindow=False
            pres = app.Presentations.Open(str(copy), True, False, False)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR the deck could not be opened: {exc}", flush=True)
            return 3
        out_dir.mkdir(parents=True, exist_ok=True)
        n = int(pres.Slides.Count)
        slides = []
        for slide in pres.Slides:
            idx = int(slide.SlideIndex)
            sid = int(slide.SlideID)
            title = ""
            try:
                if slide.Shapes.HasTitle:
                    title = _text(slide.Shapes.Title)
            except Exception:  # noqa: BLE001
                pass
            texts, pictures, boxes, boxed = [], 0, [], []
            for sh in slide.Shapes:
                t = _text(sh)
                if t:
                    texts.append(t)
                box = _box(sh)
                if box:
                    boxes.append(box)
                    boxed.append(sh)
                try:
                    if int(sh.Type) in (13, 11):  # msoPicture, msoLinkedPicture
                        pictures += 1
                except Exception:  # noqa: BLE001
                    pass
            try:
                bgr = int(slide.Background.Fill.ForeColor.RGB)
                background = f"#{bgr & 0xFF:02x}{(bgr >> 8) & 0xFF:02x}{(bgr >> 16) & 0xFF:02x}"
            except Exception:  # noqa: BLE001
                background = ""
            try:
                hidden = bool(slide.SlideShowTransition.Hidden)
            except Exception:  # noqa: BLE001
                hidden = False
            file = f"slide-{sid}.png"
            try:
                slide.Export(str(out_dir / file), "PNG", 1920, 1080)
            except Exception as exc:  # noqa: BLE001
                print(f"ERROR export of slide {idx} failed: {exc}", flush=True)
                return 4
            bg_file = None
            if boxes:
                # the same slide without its text boxes (the copy is never saved)
                try:
                    for sh in boxed:
                        sh.Visible = 0
                    bg_file = f"slide-{sid}-bg.png"
                    slide.Export(str(out_dir / bg_file), "PNG", 1920, 1080)
                except Exception as exc:  # noqa: BLE001 — the slide keeps its text in the picture
                    print(f"WARN no text-free picture for slide {idx}: {exc}", flush=True)
                    bg_file, boxes = None, []
            slides.append({
                "slide_id": sid, "index": idx, "title": title, "notes": _notes(slide), "texts": texts,
                "pictures": pictures, "background": background, "hidden": hidden, "file": file,
                **({"bg_file": bg_file, "boxes": boxes} if bg_file else {}),
            })
            print(f"PROGRESS {idx} {n}", flush=True)
        meta = {
            "slide_width": float(pres.PageSetup.SlideWidth), "slide_height": float(pres.PageSetup.SlideHeight),
            "sections": _sections(pres), "slides": slides,
        }
        (out_dir / "export.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    finally:
        if pres is not None:
            try:
                pres.Close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if started_empty and int(app.Presentations.Count) == 0:
                app.Quit()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m src.importer.pptx_com <deck.pptx> <out_dir>", file=sys.stderr)
        return 1
    deck, out_dir = Path(argv[1]), Path(argv[2])
    if not deck.is_file():
        print(f"ERROR deck not found: {deck.name}", flush=True)
        return 3
    return export(deck, out_dir)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main(sys.argv))
