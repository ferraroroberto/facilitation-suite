"""A session's playbook: ``playbook.md`` in the session folder, shown rendered in the app.

The playbook holds the instructions that belong to one session (what to analyse afterwards, in which
shape, under which rules). It is a plain markdown file the facilitator edits in their own editor; the app
only reads it, and creates it from a generic template. A real playbook lives in its session folder and
never in the repo.

The markdown is rendered here, so the page only inserts trusted HTML: every character of the file is
escaped first, then a small set of constructs is turned back into tags. Raw HTML in the file therefore
shows as text, and a link only keeps an http(s), mailto or relative target.
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any

from src.errors import DomainError

logger = logging.getLogger(__name__)

PLAYBOOK_FILE = "playbook.md"

# The headings the analysis skill reads; the body of each is a prompt for the facilitator to fill in.
TEMPLATE = """\
# Playbook

## Purpose

What this analysis is for and who reads the result.

## What to analyse

- Participation per activity
- The answers themselves

## Output format

- An overall assessment
- Notes per participant

## Rules

- Do not invent anything the results do not show.
- Write *not enough observation* where there is no evidence.
- Merge one person's several display names.

## People to leave out

- Facilitators and hosts
"""


class PlaybookError(DomainError):
    """The session's playbook file."""


def read_playbook(folder: Path) -> dict[str, Any]:
    """``{exists, html, modified_ms}`` for the folder's playbook (``html`` empty when there is none)."""
    path = folder / PLAYBOOK_FILE
    try:
        stat = path.stat()
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return {"exists": False, "html": "", "modified_ms": 0}
    except OSError as exc:
        logger.error("❌ cannot read %s: %s", path, exc)
        raise PlaybookError(422, "playbook_unreadable", "playbook.md could not be read") from exc
    return {"exists": True, "html": render_markdown(text), "modified_ms": int(stat.st_mtime * 1000)}


def create_playbook(folder: Path) -> dict[str, Any]:
    """Write the template to ``playbook.md``; an existing playbook is never touched (409)."""
    path = folder / PLAYBOOK_FILE
    try:
        # "x" fails when the file exists, so a playbook written meanwhile (OneDrive, the editor) is never overwritten.
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(TEMPLATE)
    except FileExistsError as exc:
        raise PlaybookError(409, "playbook_exists", "This session already has a playbook") from exc
    except OSError as exc:
        logger.error("❌ cannot create %s: %s", path, exc)
        raise PlaybookError(422, "playbook_not_created", "playbook.md could not be created") from exc
    logger.info("✅ playbook created in %s", folder.name)
    return read_playbook(folder)


# -- markdown → html ------------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_NUMBER = re.compile(r"^(\s*)(\d{1,9})[.)]\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
# The card's own title is an h3, so the file's # becomes an h4 and nothing nests above the card title.
_HEADING_SHIFT = 3


def render_markdown(text: str) -> str:
    """Headings, paragraphs, lists, tables, quotes, code, rules and inline emphasis, links and code."""
    return _blocks(text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4).split("\n"))


def _blocks(lines: list[str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif _FENCE.match(line):
            i = _fence(lines, i, out)
        elif (m := _HEADING.match(line)):
            level = min(len(m.group(1)) + _HEADING_SHIFT, 6)
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
        elif _RULE.match(line):
            out.append("<hr>")
            i += 1
        elif _QUOTE.match(line):
            i = _quote(lines, i, out)
        elif _BULLET.match(line) or _NUMBER.match(line):
            i = _list(lines, i, out)
        elif _is_table(lines, i):
            i = _table(lines, i, out)
        else:
            i = _paragraph(lines, i, out)
    return "\n".join(out)


def _fence(lines: list[str], i: int, out: list[str]) -> int:
    marker = lines[i].strip()[:3]
    body: list[str] = []
    i += 1
    while i < len(lines) and not lines[i].strip().startswith(marker):
        body.append(lines[i])
        i += 1
    out.append(f"<pre><code>{html.escape(chr(10).join(body))}</code></pre>")
    return i + 1  # past the closing fence (an unclosed one runs to the end)


def _quote(lines: list[str], i: int, out: list[str]) -> int:
    inner: list[str] = []
    while i < len(lines) and (m := _QUOTE.match(lines[i])):
        inner.append(m.group(1))
        i += 1
    out.append(f"<blockquote>{_blocks(inner)}</blockquote>")
    return i


def _starts_block(lines: list[str], i: int) -> bool:
    line = lines[i]
    return bool(not line.strip() or _FENCE.match(line) or _HEADING.match(line) or _RULE.match(line)
                or _QUOTE.match(line) or _BULLET.match(line) or _NUMBER.match(line))


def _paragraph(lines: list[str], i: int, out: list[str]) -> int:
    para = [lines[i].strip()]
    i += 1
    while i < len(lines) and not _starts_block(lines, i) and not _is_table(lines, i):
        para.append(lines[i].strip())
        i += 1
    out.append(f"<p>{_inline(' '.join(para))}</p>")
    return i


def _list(lines: list[str], i: int, out: list[str]) -> int:
    ordered = bool(_NUMBER.match(lines[i]))
    sibling = _NUMBER if ordered else _BULLET
    base = len(lines[i]) - len(lines[i].lstrip())
    nested = " " * (base + 2)
    items: list[list[str]] = []
    while i < len(lines):
        line = lines[i]
        m = sibling.match(line)
        if m and len(m.group(1)) == base:
            items.append([m.group(3)])
        elif not items:
            break
        elif line.startswith(nested):  # a nested list or a continuation of the item
            items[-1].append(line[base + 2:])
        elif not line.strip() and i + 1 < len(lines) and _continues(lines[i + 1], sibling, base, nested):
            items[-1].append("")  # a blank line inside a loose list
        else:
            break
        i += 1
    tag = "ol" if ordered else "ul"
    out.append(f"<{tag}>" + "".join(f"<li>{_item(body)}</li>" for body in items) + f"</{tag}>")
    return i


def _continues(line: str, sibling: re.Pattern[str], base: int, nested: str) -> bool:
    m = sibling.match(line)
    return line.startswith(nested) or bool(m and len(m.group(1)) == base)


def _item(body: list[str]) -> str:
    """One list item; in a tight item the first paragraph loses its <p>, so the bullet text sits on the bullet."""
    inner = _blocks(body)
    if "" in body or not inner.startswith("<p>"):
        return inner
    end = inner.index("</p>")
    return inner[3:end] + inner[end + 4:].removeprefix("\n")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _is_table(lines: list[str], i: int) -> bool:
    return (i + 1 < len(lines) and "|" in lines[i] and "-" in lines[i + 1] and bool(_TABLE_SEP.match(lines[i + 1]))
            and len(_cells(lines[i])) == len(_cells(lines[i + 1])))


def _table(lines: list[str], i: int, out: list[str]) -> int:
    head = _cells(lines[i])
    aligns = [_align(c) for c in _cells(lines[i + 1])]
    i += 2
    rows: list[list[str]] = []
    while i < len(lines) and lines[i].strip() and "|" in lines[i]:
        rows.append(_cells(lines[i]))
        i += 1

    def cell(tag: str, text: str, col: int) -> str:
        style = f' style="text-align:{aligns[col]}"' if aligns[col] else ""
        return f"<{tag}{style}>{_inline(text)}</{tag}>"

    parts = ["<table><thead><tr>" + "".join(cell("th", c, n) for n, c in enumerate(head)) + "</tr></thead>"]
    body = ["<tr>" + "".join(cell("td", (r + [""] * len(head))[n], n) for n in range(len(head))) + "</tr>" for r in rows]
    parts.append("<tbody>" + "".join(body) + "</tbody></table>")
    out.append("".join(parts))
    return i


def _align(sep: str) -> str:
    sep = sep.strip()
    if sep.startswith(":") and sep.endswith(":"):
        return "center"
    return "right" if sep.endswith(":") else ("left" if sep.startswith(":") else "")


# -- inline ---------------------------------------------------------------------------------------

_STASH_OPEN, _STASH_CLOSE = "", ""  # private-use marks, never in escaped text
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~>])")
_LINK = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+&quot;[^)]*?&quot;)?\s*\)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_STAR = re.compile(r"\*(?=\S)(.+?)(?<=\S)\*")
_ITALIC_UNDER = re.compile(r"(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_SAFE_TARGET = re.compile(r"^(https?://|mailto:|/|#|\./|\.\./|[^:]*$)", re.I)


def _inline(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"{_STASH_OPEN}{len(stash) - 1}{_STASH_CLOSE}"

    text = _CODE_SPAN.sub(lambda m: keep(f"<code>{html.escape(m.group(2).strip())}</code>"), text)
    text = _ESCAPED.sub(lambda m: keep(html.escape(m.group(1))), text)
    text = html.escape(text)
    text = _LINK.sub(lambda m: keep(_link(m.group(1), m.group(2))), text)
    text = _BOLD.sub(r"<strong>\2</strong>", text)
    text = _ITALIC_STAR.sub(r"<em>\1</em>", text)
    text = _ITALIC_UNDER.sub(r"<em>\1</em>", text)
    text = _STRIKE.sub(r"<del>\1</del>", text)
    # A stashed fragment can sit inside another stash's text (a code span in a link label): unwind until none is left.
    pattern = re.compile(f"{_STASH_OPEN}(\\d+){_STASH_CLOSE}")
    while pattern.search(text):
        text = pattern.sub(lambda m: stash[int(m.group(1))], text)
    return text


def _link(label: str, target: str) -> str:
    """``label`` and ``target`` are already escaped; a target that could run script keeps only its label."""
    if not _SAFE_TARGET.match(html.unescape(target)):
        return label
    label = _BOLD.sub(r"<strong>\2</strong>", label)
    label = _ITALIC_STAR.sub(r"<em>\1</em>", label)
    external = html.unescape(target).lower().startswith(("http://", "https://"))
    extra = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return f'<a href="{target}"{extra}>{label}</a>'
