"""The Excel report (epic §14): a summary, one sheet per activity, participation.

- **Summary** — the session, then one row per activity: answers, people,
  hidden, when it was captured.
- **One sheet per activity** — every answer with the person's name, the chat
  time, the text, its parsed value, and whether it was hidden.
- **Two sheets per quiz game** (``src/quiz/results.py``), only when the
  session played one: ``Quiz – <title>`` (the final leaderboard, then each
  question's distribution) and ``Quiz – <title> answers`` (player × question:
  the choice, correct or not, its points, the response time). A replay of the
  same quiz adds `` run <n>`` to both names.
- **Participation** — per person: answers counted in each activity, the
  total, and chat messages sent (the StreamAlive "fans" idea), most active
  first.

Sheet names are at most 31 characters, free of the characters Excel refuses, and unique.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
BOLD = Font(bold=True)
MAX_SHEET = 31  # Excel's limit on a sheet name
QUIZ_PREFIX = "Quiz – "


def _unique(base: str, taken: set[str]) -> str:
    name, k = base, 2
    while name.casefold() in taken or name.casefold() in ("summary", "participation"):
        suffix = f" ({k})"
        name, k = base[: MAX_SHEET - len(suffix)] + suffix, k + 1
    taken.add(name.casefold())
    return name


def sheet_title(n: int, title: str, taken: set[str]) -> str:
    base = BAD_SHEET_CHARS.sub(" ", f"{n} {title}").strip()[:MAX_SHEET].rstrip() or str(n)
    return _unique(base, taken)


def quiz_sheet_title(title: str, run: int, suffix: str, taken: set[str]) -> str:
    """``Quiz – <title>[ run <n>]<suffix>``: the title is shortened so the run and the suffix always fit."""
    tag = f" run {run}" if run > 1 else ""
    room = MAX_SHEET - len(QUIZ_PREFIX) - len(tag) - len(suffix)
    core = " ".join(BAD_SHEET_CHARS.sub(" ", title).split())[:room].rstrip() or "Quiz"
    return _unique(f"{QUIZ_PREFIX}{core}{tag}{suffix}", taken)


def _table(ws, header: list[str], rows: list[list[Any]], widths: list[int]) -> None:  # noqa: ANN001 — openpyxl sheet
    ws.append(header)
    for c in ws[ws.max_row]:
        c.font = BOLD
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def build_report(results: dict[str, Any], people: list[dict[str, Any]], out: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    sess = results["session"]
    ws.append([sess["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([(sess.get("date") or "")[:10]])
    ws.append([])
    _table(ws, ["#", "Activity", "Type", "Answers", "People", "Hidden", "Captured", "Result"],
           [[a["number"], a["title"], a["type_label"], a["answers"], a["people"], a["hidden"], a["captured"], a["summary"]]
            for a in results["activities"]],
           [5, 48, 14, 10, 10, 10, 14, 36])
    ws.freeze_panes = "A5"

    taken: set[str] = set()
    for a in results["activities"]:
        sh = wb.create_sheet(sheet_title(a["number"], a["title"], taken))
        sh.append([a["title"]])
        sh["A1"].font = Font(bold=True, size=13)
        if a["question"] and a["question"] != a["title"]:
            sh.append([a["question"]])
        sh.append([f"{a['answers']} answers from {a['people']} people · captured {a['captured']}"
                   + (f" · {a['hidden']} hidden" if a["hidden"] else "")])
        sh.append([])
        first = sh.max_row + 1
        _table(sh, ["Name", "Time", "Answer", "Parsed", "Hidden"],
               [[r["sender"], r["time"], r["text"], r["value"], "yes" if r["hidden"] else ""] for r in a["answer_rows"]],
               [26, 8, 60, 30, 8])
        sh.freeze_panes = f"A{first + 1}"

    for q in results.get("quizzes") or []:
        _quiz_sheets(wb, q, taken)

    ps = wb.create_sheet("Participation")
    acts = results["activities"]
    _table(ps, ["Name", "Answers", *[f"{a['number']} {a['title']}"[:40] for a in acts], "Chat messages"],
           [[p["name"], p["total"], *[p["by_activity"].get(a["id"], 0) for a in acts], p["messages"]] for p in people],
           [26, 10, *[14 for _ in acts], 14])
    ps.freeze_panes = "B2"

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    tmp.replace(out)
    return out


def _seconds(ms: Any) -> Any:
    return round(ms / 1000, 2) if isinstance(ms, int) else ""


def _quiz_sheets(wb: Workbook, q: dict[str, Any], taken: set[str]) -> None:
    """One game's leaderboard sheet and its answers sheet (every number straight from the engine)."""
    sh = wb.create_sheet(quiz_sheet_title(q["title"], q["run"], "", taken))
    sh.append([q["label"]])
    sh["A1"].font = Font(bold=True, size=13)
    sh.append([q["summary"]])
    sh.append([])
    first = sh.max_row + 1
    _table(sh, ["Rank", "Nickname", "Score", "Correct", "Answered", "Avg response (s)", "Source"],
           [[r["rank"], r["name"], r["score"], r["correct"], r["answered"], _seconds(r["avg_ms"]), r["source"]]
            for r in q["leaderboard"]],
           [8, 26, 10, 10, 10, 16, 10])
    sh.freeze_panes = f"A{first + 1}"
    sh.append([])
    sh.append(["Questions"])
    sh.cell(sh.max_row, 1).font = BOLD
    _table(sh, ["#", "Question", "Answer", "Picked", "Correct"],
           [[x["number"], x["question"], f"{a['n']} · {a['text']}", a["count"], "yes" if a["correct"] else ""]
            for x in q["questions"] for a in x["answers"]],
           [8, 48, 36, 10, 16])

    sa = wb.create_sheet(quiz_sheet_title(q["title"], q["run"], " answers", taken))
    sa.append([f"{q['label']} · answers"])
    sa["A1"].font = Font(bold=True, size=13)
    sa.append(["Every player × every question asked; no answer scores 0."])
    sa.append([])
    first = sa.max_row + 1
    _table(sa, ["Nickname", "Source", "#", "Question", "Answer", "Correct", "Points", "Response (ms)"],
           [[r["name"], r["source"], r["number"], r["question"],
             f"{r['choice']} · {r['choice_text']}" if r["choice"] else "",
             "" if r["correct"] is None else ("yes" if r["correct"] else "no"), r["points"],
             r["elapsed_ms"] if r["elapsed_ms"] is not None else ""]
            for r in q["answer_rows"]],
           [26, 10, 6, 48, 30, 10, 10, 14])
    sa.freeze_panes = f"A{first + 1}"
