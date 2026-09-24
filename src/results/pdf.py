"""The session PDF (epic §14): slides and live results in the order they happened.

Every page of the ``timeline`` (``collect.py``) is one widescreen page — the
slide PNG, or the capture's frozen PNG exactly as the stage showed it — then
an appendix with every counted answer, activity by activity.

The document is one HTML file printed by headless Chromium, so the images go
in untouched and the appendix flows over as many pages as it needs. The
printing runs as its own process (``python -m src.results.pdf <html> <out>``)
so the server never waits on a browser in its own thread.
"""

from __future__ import annotations

import logging
import re
import sys
from html import escape
from pathlib import Path
from typing import Any

from src.config import data_dir
from src.logger import configure_logging

logger = logging.getLogger("session_pdf")

PAGE_RE = re.compile(rb"/Type\s*/Page(?![s\w])")

CSS = """
@page { size: 338.667mm 190.5mm; margin: 0; }
@page appendix { size: 338.667mm 190.5mm; margin: 16mm 20mm; }
html, body { margin: 0; padding: 0; }
body { font-family: "Segoe UI", Arial, sans-serif; color: #1f1f1f; }
.page { width: 338.667mm; height: 190.5mm; overflow: hidden; break-after: page; background: #fff; }
.page img { width: 100%; height: 100%; object-fit: contain; display: block; }
.missing { display: flex; flex-direction: column; justify-content: center; padding: 0 30mm; box-sizing: border-box; background: #f2f2f2; }
.missing h1 { font-size: 30pt; margin: 0 0 6mm; }
.missing p { font-size: 15pt; color: #5e5e5e; margin: 0; }
.appendix { page: appendix; font-size: 10.5pt; }
.appendix > h1 { font-size: 22pt; margin: 0 0 2mm; }
.appendix > .sub { color: #5e5e5e; margin: 0 0 8mm; }
.act { break-inside: auto; margin: 0 0 9mm; }
.act h2 { font-size: 15pt; margin: 0 0 1mm; break-after: avoid; }
.act .meta { color: #5e5e5e; margin: 0 0 3mm; break-after: avoid; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-weight: 600; border-bottom: 1.5px solid #1f1f1f; padding: 1.5mm 2mm; }
td { border-bottom: 0.5px solid #d6d6d6; padding: 1.3mm 2mm; vertical-align: top; }
tr { break-inside: avoid; }
td.name { width: 28%; font-weight: 600; } td.time { width: 8%; color: #5e5e5e; }
"""


def session_html(folder: Path, results: dict[str, Any]) -> str:
    acts = {a["id"]: a for a in results["activities"]}
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(results['session']['title'])}</title>"
             f"<style>{CSS}</style></head><body>"]
    for p in results["pages"]:
        if p["kind"] == "slide":
            src = (folder / "slides" / p["file"]).as_uri()
            parts.append(f"<div class='page'><img src='{src}' alt='{escape(p['title'])}'></div>")
            continue
        a = acts[p["item_id"]]
        png = folder / "live" / "captures" / f"{a['id']}.png"
        if a["has_png"]:
            parts.append(f"<div class='page'><img src='{png.as_uri()}' alt='{escape(a['title'])}'></div>")
        else:
            parts.append(f"<div class='page missing'><h1>{escape(a['title'])}</h1>"
                         f"<p>{escape(a['summary'])} · the stage image of this capture was not saved — every answer is in the appendix.</p></div>")
    date = (results["session"].get("date") or "")[:10]
    parts.append("<section class='appendix'>")
    parts.append(f"<h1>Answers</h1><p class='sub'>{escape(results['session']['title'])}{' · ' + escape(date) if date else ''}</p>")
    for a in results["activities"]:
        rows = [r for r in a["answer_rows"] if not r["hidden"]]
        meta = f"{a['answers']} answers from {a['people']} people · captured {a['captured']}"
        if a["hidden"]:
            meta += f" · {a['hidden']} hidden answer{'s' if a['hidden'] != 1 else ''} left out"
        parts.append(f"<div class='act'><h2>{a['number']} · {escape(a['title'])}</h2><p class='meta'>{escape(meta)}</p>")
        if rows:
            parts.append("<table><thead><tr><th>Name</th><th>Time</th><th>Answer</th></tr></thead><tbody>")
            parts += [f"<tr><td class='name'>{escape(r['sender'])}</td><td class='time'>{escape(r['time'])}</td>"
                      f"<td>{escape(r['text'])}</td></tr>" for r in rows]
            parts.append("</tbody></table>")
        parts.append("</div>")
    parts.append("</section></body></html>")
    return "".join(parts)


def count_pages(pdf: Path) -> int:
    try:
        return len(PAGE_RE.findall(pdf.read_bytes()))
    except OSError:
        return 0


def print_pdf(html: Path, out: Path, timeout_ms: int = 120000) -> int:
    """Print ``html`` to ``out``; returns the page count (0: it could not be counted)."""
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".pdf.tmp")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html.as_uri(), wait_until="load", timeout=timeout_ms)
            broken = page.evaluate("[...document.images].filter(i => !i.complete || !i.naturalWidth).map(i => i.alt)")
            if broken:
                logger.warning("⚠️ session PDF: %d image(s) did not load: %s", len(broken), ", ".join(broken))
            page.pdf(path=str(tmp), prefer_css_page_size=True, print_background=True)
        finally:
            browser.close()
    tmp.replace(out)
    pages = count_pages(out)
    logger.info("✅ session PDF written: %s (%d pages)", out.name, pages)
    return pages


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m src.results.pdf <session.html> <out.pdf>", file=sys.stderr)
        return 1
    configure_logging(log_file=data_dir() / "logs" / "facilitation-suite.log")
    try:
        pages = print_pdf(Path(argv[1]), Path(argv[2]))
    except Exception as exc:  # noqa: BLE001 — reported to the server through the exit code
        logger.error("❌ session PDF failed: %s", exc)
        return 2
    print(pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
