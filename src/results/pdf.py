"""The session PDF (epic §14): slides and live results in the order they happened.

Every page of the ``timeline`` (``collect.py``) is one widescreen page — the
slide PNG, or the capture's frozen PNG exactly as the stage showed it — then
an appendix with every counted answer, activity by activity.

A quiz (``src/quiz/results.py``) adds a page per question asked — its frozen
PNG when one exists, else a rendered summary of its distribution — and a
podium page per game, where they happened; the appendix then lists each
game's final leaderboard. A session without a quiz prints exactly as before.

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
from typing import Any, Optional

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

QUIZ_CSS = """
.quiz { display: flex; flex-direction: column; justify-content: center; padding: 0 30mm; box-sizing: border-box; background: #f2f2f2; }
.quiz .kicker { font-size: 13pt; color: #5e5e5e; margin: 0 0 3mm; }
.quiz h1 { font-size: 28pt; margin: 0 0 9mm; }
.quiz .bar-row { display: grid; grid-template-columns: 34% 1fr 16mm; align-items: center; gap: 5mm; margin: 0 0 4mm; font-size: 15pt; }
.quiz .bar { height: 9mm; background: #d6d6d6; border-radius: 2mm; overflow: hidden; }
.quiz .bar span { display: block; height: 100%; background: #9e9e9e; }
.quiz .right .bar span { background: #1f1f1f; }
.quiz .right .label { font-weight: 700; }
.quiz .count { text-align: right; font-weight: 700; }
.quiz .foot { font-size: 13pt; color: #5e5e5e; margin: 5mm 0 0; }
.podium { display: flex; align-items: flex-end; justify-content: center; gap: 8mm; margin: 0 0 8mm; }
.podium .place { width: 70mm; text-align: center; }
.podium .name { font-size: 18pt; font-weight: 700; margin: 0 0 2mm; overflow-wrap: anywhere; }
.podium .score { font-size: 13pt; color: #5e5e5e; margin: 0 0 3mm; }
.podium .block { background: #1f1f1f; color: #fff; font-size: 30pt; font-weight: 700; display: flex; align-items: center;
  justify-content: center; border-radius: 3mm 3mm 0 0; }
.podium .p1 .block { height: 52mm; }
.podium .p2 .block { height: 38mm; background: #5e5e5e; }
.podium .p3 .block { height: 28mm; background: #9e9e9e; }
.quiz .rest { font-size: 12pt; color: #5e5e5e; text-align: center; margin: 0; }
td.num { width: 8%; }
"""


def _seconds(ms: Optional[int]) -> str:
    return f"{ms / 1000:.1f} s" if ms is not None else "–"


def _quiz_question(folder: Path, q: dict[str, Any], x: dict[str, Any]) -> str:
    """A question's page: its frozen PNG, else its distribution with the correct answer(s) marked."""
    if x["has_png"]:
        png = folder / "live" / "captures" / f"{x['item_id']}.png"
        return f"<div class='page'><img src='{png.as_uri()}' alt='{escape(x['question'])}'></div>"
    top = max([a["count"] for a in x["answers"]] + [1])
    rows = "".join(
        f"<div class='bar-row{' right' if a['correct'] else ''}'>"
        f"<span class='label'>{a['n']} · {escape(a['text'])}{' ✓' if a['correct'] else ''}</span>"
        f"<span class='bar'><span style='width:{round(100 * a['count'] / top)}%'></span></span>"
        f"<span class='count'>{a['count']}</span></div>" for a in x["answers"])
    return (f"<div class='page quiz'><p class='kicker'>{escape(q['label'])} · question {x['number']} of {q['question_count']}</p>"
            f"<h1>{escape(x['question'])}</h1>{rows}"
            f"<p class='foot'>{x['answered']} of {x['players']} players answered · ✓ correct</p></div>")


def _quiz_podium(q: dict[str, Any]) -> str:
    """A game's podium page: the top three (second, first, third), then how many more played."""
    places = {r["rank"]: r for r in q["podium"]}
    cols = "".join(f"<div class='place p{n}'><p class='name'>{escape(places[n]['name'])}</p>"
                   f"<p class='score'>{places[n]['score']} points</p><div class='block'>{n}</div></div>"
                   for n in (2, 1, 3) if n in places) or "<p class='rest'>Nobody played.</p>"
    more = q["player_count"] - len(places)
    rest = (f"<p class='rest'>and {more} more player{'s' if more != 1 else ''} · the full leaderboard is in the appendix</p>"
            if more > 0 else "")
    return (f"<div class='page quiz'><p class='kicker'>{escape(q['summary'])}</p><h1>{escape(q['label'])} · podium</h1>"
            f"<div class='podium'>{cols}</div>{rest}</div>")


def _quiz_appendix(q: dict[str, Any]) -> str:
    """A game's final leaderboard for the appendix."""
    out = f"<div class='act'><h2>Quiz · {escape(q['label'])}</h2><p class='meta'>{escape(q['summary'])}</p>"
    if q["leaderboard"]:
        out += ("<table><thead><tr><th>Rank</th><th>Nickname</th><th>Score</th><th>Correct</th>"
                "<th>Avg response</th><th>Source</th></tr></thead><tbody>")
        out += "".join(f"<tr><td class='num'>{r['rank']}</td><td class='name'>{escape(r['name'])}</td><td>{r['score']}</td>"
                       f"<td>{r['correct']} of {q['question_count']}</td><td>{_seconds(r['avg_ms'])}</td>"
                       f"<td>{escape(r['source'])}</td></tr>" for r in q["leaderboard"])
        out += "</tbody></table>"
    return out + "</div>"


def session_html(folder: Path, results: dict[str, Any]) -> str:
    acts = {a["id"]: a for a in results["activities"]}
    quizzes = {q["id"]: q for q in results.get("quizzes") or []}
    parts = [f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(results['session']['title'])}</title>"
             f"<style>{CSS}{QUIZ_CSS if quizzes else ''}</style></head><body>"]
    for p in results["pages"]:
        if p["kind"] == "slide":
            src = (folder / "slides" / p["file"]).as_uri()
            parts.append(f"<div class='page'><img src='{src}' alt='{escape(p['title'])}'></div>")
            continue
        if p["kind"] == "quiz_question":
            q = quizzes[p["game"]]
            parts.append(_quiz_question(folder, q, next(x for x in q["questions"] if x["item_id"] == p["item_id"])))
            continue
        if p["kind"] == "quiz_podium":
            parts.append(_quiz_podium(quizzes[p["game"]]))
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
    parts += [_quiz_appendix(q) for q in quizzes.values()]
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
    configure_logging(to_file=False)  # the server relays our stderr into its log (#37)
    try:
        pages = print_pdf(Path(argv[1]), Path(argv[2]))
    except Exception as exc:  # noqa: BLE001 — reported to the server through the exit code
        logger.error("❌ session PDF failed: %s", exc)
        return 2
    print(pages)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
