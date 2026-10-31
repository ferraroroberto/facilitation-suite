"""One person under several chat names.

Zoom's chat shows only a display name, so a rename, a short name and an
"iPhone de ..." device all look like different people. ``aliases.yaml`` in the
session folder says which names are one person (``{canonical: [alias, ...]}``);
results read it and count the canonical name (``Aliases.canonical`` is the one
place a name becomes a person). ``chat.jsonl`` and the live session keep the raw
names, and without the file results read exactly as before.

Nothing is merged on its own: ``suggest`` lists likely groups for the owner to
confirm, and only ``merge`` (the owner's click) writes the file.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

import yaml

from src.errors import DomainError
from src.sessions.store import atomic_write_text
from src.text import fold

logger = logging.getLogger(__name__)

ALIASES_FILE = "aliases.yaml"

DEVICES = r"(?:iphone|ipad|ipod|galaxy|android|pixel|samsung)"
DEVICE_PREFIX = re.compile(rf"^{DEVICES}\s+(?:de|di|of|von)\s+")  # "iPhone de Ana"
DEVICE_SUFFIX = re.compile(rf"^(.+?)'s\s+{DEVICES}$")  # "Ana's iPhone"


class AliasError(DomainError):
    """The aliases file, or a merge that cannot be made."""


def ident(name: str) -> str:
    """The lookup key of a raw name: spacing and case do not make a different person."""
    return " ".join(name.split()).casefold()


def _strip_device(name: str) -> tuple[str, bool]:
    folded = " ".join(fold(name).replace("’", "'").split())
    m = DEVICE_SUFFIX.match(folded)
    if m:
        return m.group(1), True
    stripped = DEVICE_PREFIX.sub("", folded)
    return stripped, stripped != folded


def tokens(name: str) -> list[str]:
    """A name's words, folded, with a device prefix or suffix dropped ("iPhone de Ana" → ["ana"])."""
    return re.findall(r"\w+", _strip_device(name)[0])


def is_device_name(name: str) -> bool:
    return _strip_device(name)[1]


def _spelling(name: str) -> tuple[int, bool, bool]:
    """Sorts the neater spelling of one name first: no stray punctuation, capitalised, accents kept."""
    return sum(not (c.isalnum() or c.isspace()) for c in name), name == name.lower(), name.isascii()


def _extends(short: list[str], long: list[str]) -> bool:
    """``short`` is a shorter form of ``long``: a leading part of it, or its words with the last one an initial."""
    n = len(short)
    if not 0 < n <= len(long) or short[:-1] != long[: n - 1]:
        return False
    if short[-1] == long[n - 1]:
        return n < len(long)
    return n >= 2 and len(short[-1]) == 1 and long[n - 1].startswith(short[-1])


class Aliases:
    """The names of a session mapped to their person."""

    def __init__(self, mapping: Optional[dict[str, list[str]]] = None) -> None:
        self.groups: dict[str, list[str]] = {}
        for canonical, names in (mapping or {}).items():
            self.groups[canonical] = list(names)
        self._index()

    def _index(self) -> None:
        self._canon: dict[str, str] = {}
        for canonical, names in self.groups.items():
            for name in (canonical, *names):
                self._canon.setdefault(ident(name), canonical)

    def __bool__(self) -> bool:
        return bool(self.groups)

    def canonical(self, name: str) -> str:
        """The person a raw name belongs to; a name nobody merged is its own person, returned untouched."""
        return self._canon.get(ident(name), name)

    def merge(self, canonical: str, names: Iterable[str]) -> None:
        """Make ``names`` (and whoever they already stand for) the same person as ``canonical``."""
        target = self.canonical(canonical.strip())
        members = self.groups.setdefault(target, [])
        for raw in names:
            name = raw.strip()
            if not name or ident(self.canonical(name)) == ident(target):
                continue
            moved = self.canonical(name)
            members.extend(n for n in (moved, *self.groups.pop(moved, [])) if ident(n) != ident(target))
        self.groups[target] = list(dict.fromkeys(members))
        if not self.groups[target]:
            del self.groups[target]
        self._index()

    def unmerge(self, name: str) -> None:
        """Drop one alias, or the whole group when ``name`` is its canonical name."""
        key = ident(name)
        for canonical in list(self.groups):
            if ident(canonical) == key:
                del self.groups[canonical]
            else:
                self.groups[canonical] = [n for n in self.groups[canonical] if ident(n) != key]
                if not self.groups[canonical]:
                    del self.groups[canonical]
        self._index()


class Excluded:
    """The senders a session does not count (a co-host or producer account), as people:
    excluding a name excludes every name merged with it, and a merge made later extends it."""

    def __init__(self, names: Iterable[str] = (), aliases: Optional[Aliases] = None) -> None:
        self._aliases = aliases or Aliases()
        self._people = frozenset(ident(self._aliases.canonical(n)) for n in names if n.strip())

    def __bool__(self) -> bool:
        return bool(self._people)

    def __contains__(self, sender: object) -> bool:
        return isinstance(sender, str) and ident(self._aliases.canonical(sender)) in self._people


def read_aliases(folder: Path, *, strict: bool = False) -> Aliases:
    """The session's ``aliases.yaml``. A missing file is no merges; a damaged one reads as none and is logged
    (``strict``: raises instead, so a write never replaces a file the owner edited by hand)."""
    path = folder / ALIASES_FILE
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Aliases()
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return _damaged(f"{ALIASES_FILE} unreadable ({exc})", strict)
    if raw is None:
        return Aliases()
    if not isinstance(raw, dict):
        return _damaged(f"{ALIASES_FILE} is not a mapping of name to aliases", strict)
    mapping: dict[str, list[str]] = {}
    for canonical, names in raw.items():
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            return _damaged(f"{ALIASES_FILE}: the aliases of {canonical!r} are not a list of names", strict)
        mapping[str(canonical)] = names
    return Aliases(mapping)


def _damaged(message: str, strict: bool) -> Aliases:
    if strict:
        raise AliasError(422, "aliases_unreadable", f"{message} — fix or delete the file first")
    logger.warning("⚠️ results: %s — read as no merges", message)
    return Aliases()


def write_aliases(folder: Path, aliases: Aliases) -> None:
    """Save the merges; no merges left removes the file, which restores the raw view."""
    path = folder / ALIASES_FILE
    if not aliases:
        path.unlink(missing_ok=True)
        return
    atomic_write_text(path, yaml.safe_dump(aliases.groups, allow_unicode=True, sort_keys=False))


def suggest(names: Iterable[str], aliases: Aliases, roster: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Likely groups among the names seen, as ``{"canonical", "aliases"}``, for the owner to confirm.

    Names are compared by their folded words with a device prefix or suffix
    dropped: equal words are one person, and a name that is a leading part of
    another or its first-plus-initial ("Ana P" for "Ana Perez") joins it, unless
    it fits two different people ("Ana" with an "Ana Perez" and an "Ana Lopez").
    A roster name is the preferred canonical name of its group.
    """
    nodes: dict[str, dict[str, Any]] = {}
    for raw, is_roster in [*((n, False) for n in names), *((n, True) for n in roster)]:
        label = " ".join(raw.split())
        if not label:
            continue
        label = aliases.canonical(label) if not is_roster else label
        node = nodes.setdefault(ident(label), {"label": label, "tokens": tokens(label), "roster": False})
        node["roster"] = node["roster"] or is_roster
    nodes = {k: v for k, v in nodes.items() if v["tokens"]}
    parent = {k: k for k in nodes}

    def find(k: str) -> str:
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    by_words: dict[tuple[str, ...], str] = {}
    for k, v in nodes.items():
        parent[find(k)] = find(by_words.setdefault(tuple(v["tokens"]), k))
    changed = True
    while changed:
        changed = False
        for k, v in nodes.items():
            targets = {find(o) for o, w in nodes.items() if find(o) != find(k) and _extends(v["tokens"], w["tokens"])}
            if len(targets) == 1:
                parent[find(k)] = targets.pop()
                changed = True

    groups: dict[str, list[str]] = {}
    for k in nodes:
        groups.setdefault(find(k), []).append(k)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rostered = [m for m in members if nodes[m]["roster"]]
        if len(rostered) > 1:
            continue  # two roster entries are two people: whatever links them is a coincidence
        best = rostered[0] if rostered else min(
            members, key=lambda m: (is_device_name(nodes[m]["label"]), -len(nodes[m]["tokens"]), _spelling(nodes[m]["label"]), -len(m), m))
        out.append({"canonical": nodes[best]["label"],
                    "aliases": sorted((nodes[m]["label"] for m in members if m != best), key=str.casefold)})
    return sorted(out, key=lambda g: g["canonical"].casefold())


def seen_names(activities: list[dict[str, Any]], chat: list[dict[str, Any]], excluded: Optional[Excluded] = None) -> list[str]:
    """Every raw name that answered or wrote (the facilitator's own messages and the excluded senders left out), each once."""
    seen: dict[str, str] = {}
    excluded = excluded or Excluded()
    for a in activities:
        for r in a["answer_rows"]:
            name = r.get("raw") or r["sender"]
            if name and not r["hidden"]:
                seen.setdefault(ident(name), name)
    for m in chat:
        if not m.get("own") and m.get("sender") and m["sender"] not in excluded:
            seen.setdefault(ident(m["sender"]), m["sender"])
    return sorted(seen.values(), key=str.casefold)
