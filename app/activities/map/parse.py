"""Map: each answer → a place (offline GeoNames), aggregated into pins.

- ``parse`` geocodes one message (``src/geo/gazetteer.py``). A place fixed by
  hand on the presenter arrives as ``message["place_id"]`` and wins.
- ``aggregate`` keeps each person's latest placed answer, and re-decides
  ambiguous names with the room as context: "Valencia" goes to Spain when
  the others are in Spain, not to the larger Valencia in Venezuela.
- Answers that resolve to nothing are listed as ``unplaced`` for the
  presenter's one-click fix.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from src.geo.gazetteer import gazetteer

TOP_CITIES = 4


def parse(message: dict[str, Any], options: dict[str, Any]) -> Optional[dict[str, Any]]:
    text = (message.get("text") or "").strip()
    if not text:
        return None
    g = gazetteer()
    manual = bool(message.get("place_id"))
    if manual:
        place, candidates = g.by_geonameid(str(message["place_id"])), []
    else:
        place, candidates = g.resolve(text)
    return {
        "id": message.get("id"), "sender": message.get("sender", ""), "text": text, "manual": manual,
        "place": place.as_dict() if place else None,
        "candidates": [c.as_dict() for c in candidates],
    }


def _choose(c: dict[str, Any], share: dict[str, float]) -> Optional[dict[str, Any]]:
    """The place for one contribution, preferring countries the room is in."""
    if c["manual"] or not c["candidates"] or not c["place"]:
        return c["place"]
    options = [c["place"], *c["candidates"]]
    return max(options, key=lambda p: (p.get("population") or 1) * (1 + 50 * share.get(p["country"], 0.0)))


def aggregate(contributions: list[dict[str, Any]], options: dict[str, Any]) -> dict[str, Any]:
    firm = Counter(c["place"]["country"] for c in contributions if c["place"] and not c["candidates"])
    total = sum(firm.values()) or 1
    share = {cc: n / total for cc, n in firm.items()}
    latest: dict[str, dict[str, Any]] = {}
    unplaced: dict[str, dict[str, Any]] = {}
    for c in contributions:
        place = _choose(c, share)
        who = c["sender"] or f"#{c['id']}"
        if place:
            latest[who] = {"id": c["id"], "sender": c["sender"], "text": c["text"], **{k: place[k] for k in
                           ("geonameid", "name", "country", "country_name", "lat", "lon", "precision")}}
            unplaced.pop(who, None)
        elif who not in latest:
            unplaced[who] = {"id": c["id"], "sender": c["sender"], "text": c["text"]}
    pins = list(latest.values())
    cities: dict[str, dict[str, Any]] = {}
    for p in pins:
        city = cities.setdefault(p["geonameid"], {"name": p["name"], "country_name": p["country_name"], "names": []})
        city["names"].append(p["sender"])
    top = sorted(cities.values(), key=lambda x: -len(x["names"]))
    senders = {c["sender"] for c in contributions if c["sender"]}
    return {
        "pins": pins,
        "unplaced": list(unplaced.values()),
        "cities": [dict(c, count=len(c["names"])) for c in top[:TOP_CITIES] if len(c["names"]) > 1],
        "placed": len(pins),
        "countries": len({p["country"] for p in pins}),
        "answers": len(contributions),
        "people": len(senders),
    }


def report(result: dict[str, Any]) -> dict[str, Any]:
    """The Results tab's summary line, its top list and the PDF strip's tile label."""
    by_country = Counter(p.get("country_name") or p.get("country") for p in result.get("pins") or [])
    people, countries = result.get("placed", 0), result.get("countries", 0)
    unplaced = len(result.get("unplaced") or [])
    return {
        "summary": f"{people} people · {countries} countr{'y' if countries == 1 else 'ies'}"
                   + (f" · {unplaced} unplaced" if unplaced else ""),
        "top_label": "Countries",
        "top": [{"label": c, "count": n} for c, n in by_country.most_common(6)],
        "tile": str(people),
    }


def value(parsed: dict[str, Any]) -> str:
    """One answer's parsed value for the Excel report: the place it resolved to."""
    place = parsed.get("place")
    if not place:
        return "unplaced"
    return place["name"] if place.get("precision") == "country" else f"{place['name']}, {place['country_name']}"
