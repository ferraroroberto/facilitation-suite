"""Offline geocoding for the magic map (epic §10) — no network, ever.

Data (built by ``scripts/build_geo.py``, committed under
``app/activities/map/``): GeoNames ``cities15000`` (CC BY 4.0), country names
in English and Spanish, and ``aliases.yaml`` (``CDMX``, ``NYC``, ``EEUU``…).

``resolve(text)`` understands what people type in a chat:
``Milan, italy`` · ``Sevilla, España`` · ``CDMX`` · ``desde Bogotá`` ·
``Lisboa - Portugal`` · ``Milan Italy`` · ``Spain`` (→ the capital, marked
``precision="country"``). A city named in several countries resolves to the
most populous one unless the country is given; ``candidates`` keeps the
others so the map can prefer the countries the rest of the room is in.
Anything else is ``None`` — the presenter lists it as unplaced.
"""

from __future__ import annotations

import difflib
import gzip
import json
import logging
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent.parent.parent / "app" / "activities" / "map"
PREFIX = re.compile(r"^(?:(?:hola|hi|hello)[\s,!.]+)?(?:(?:yo\s+)?(?:estoy|vivo|soy|desde|de|en|conect\w*|from|in|i'?m|i am|im|live in|here in|aqu[ií])\s+)+", re.I)
SPLIT = re.compile(r"\s*(?:,|/|\s-\s|\(|\)|;)\s*")


def fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    out = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", out)).strip()


@dataclass(frozen=True)
class Place:
    geonameid: str
    name: str
    country: str
    country_name: str
    lat: float
    lon: float
    population: int
    precision: str = "city"  # city | country

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Gazetteer:
    def __init__(self, data_dir: Path = DATA) -> None:
        self.data_dir = data_dir
        self.cities: list[tuple[str, str, str, float, float, int]] = []  # id, name, cc, lat, lon, pop
        self.by_id: dict[str, int] = {}
        self.index: dict[str, list[int]] = {}
        self.countries: dict[str, dict[str, Any]] = {}  # iso → info
        self.country_index: dict[str, str] = {}  # folded name → iso
        self.aliases: dict[str, str] = {}
        self._load()

    # ---------------------------------------------------------------- load

    def _load(self) -> None:
        raw = gzip.decompress((self.data_dir / "cities.tsv.gz").read_bytes()).decode("utf-8")
        for line in raw.splitlines():
            gid, name, ascii_name, cc, lat, lon, pop, alts = line.split("\t")
            i = len(self.cities)
            self.cities.append((gid, name, cc, float(lat), float(lon), int(pop)))
            self.by_id[gid] = i
            for n in {name, ascii_name, *(a for a in alts.split("|") if a)}:
                self.index.setdefault(fold(n), []).append(i)
        for idx in self.index.values():
            idx.sort(key=lambda i: -self.cities[i][5])
        for c in json.loads((self.data_dir / "countries.json").read_text(encoding="utf-8")):
            self.countries[c["iso"]] = c
            for n in c["names"] + [c["iso3"]]:
                self.country_index.setdefault(fold(n), c["iso"])
        aliases = yaml.safe_load((self.data_dir / "aliases.yaml").read_text(encoding="utf-8")) or {}
        for key, target in (aliases.get("countries") or {}).items():
            self.country_index[fold(str(key))] = str(target)
        for key, target in (aliases.get("cities") or {}).items():
            self.aliases[fold(str(key))] = str(target)
        logger.info("ℹ️ gazetteer: %d cities, %d names, %d countries", len(self.cities), len(self.index), len(self.countries))

    # ------------------------------------------------------------- helpers

    def _place(self, i: int, precision: str = "city") -> Place:
        gid, name, cc, lat, lon, pop = self.cities[i]
        return Place(gid, name, cc, self.country_label(cc), lat, lon, pop, precision)

    def country_label(self, iso: str) -> str:
        c = self.countries.get(iso) or {}
        return c.get("name_es") or c.get("name") or iso

    def _country(self, part: str, *, allow_iso2: bool) -> Optional[str]:
        key = fold(part)
        if not key:
            return None
        if key in self.country_index:
            return self.country_index[key]
        if allow_iso2 and len(key) == 2 and key.upper() in self.countries:
            return key.upper()
        return None

    def _country_place(self, iso: str) -> Optional[Place]:
        c = self.countries.get(iso)
        if not c or c.get("lat") is None:
            return None
        return Place(f"country:{iso}", c.get("capital") or c["name"], iso, self.country_label(iso), c["lat"], c["lon"], 0, "country")

    def _cities(self, part: str, iso: Optional[str] = None) -> list[int]:
        hits = self.index.get(fold(part), [])
        return [i for i in hits if iso is None or self.cities[i][2] == iso]

    def by_geonameid(self, gid: str) -> Optional[Place]:
        if gid.startswith("country:"):
            return self._country_place(gid.split(":", 1)[1])
        i = self.by_id.get(gid)
        return self._place(i) if i is not None else None

    # ------------------------------------------------------------- resolve

    def resolve(self, text: str) -> tuple[Optional[Place], list[Place]]:
        """(best place, other candidates) for what someone typed."""
        t = PREFIX.sub("", (text or "").strip()).strip(" .!?¡¿")
        if not t:
            return None, []
        key = fold(t)
        if key in self.aliases:
            return self._alias(self.aliases[key])
        parts = [p for p in SPLIT.split(t) if p.strip()]
        if len(parts) >= 2:
            iso = self._country(parts[-1], allow_iso2=True)
            city_part = parts[0]
            if iso:
                hits = self._cities(city_part, iso)
                if hits:
                    return self._place(hits[0]), [self._place(i) for i in hits[1:4]]
                if fold(city_part) in self.aliases:
                    return self._alias(self.aliases[fold(city_part)])
                return self._country_place(iso), []
            # "Madrid, Madrid" or "Lisboa, Lisbon district": the first part is the city
            hits = self._cities(city_part)
            if hits:
                return self._place(hits[0]), [self._place(i) for i in hits[1:4]]
        hits = self._cities(t)
        if hits:
            return self._place(hits[0]), [self._place(i) for i in hits[1:4]]
        iso = self._country(t, allow_iso2=False)
        if iso:
            return self._country_place(iso), []
        # "Milan Italy" (no comma): the last one or two words may be the country
        words = t.split()
        for n in (2, 1):
            if len(words) > n:
                iso = self._country(" ".join(words[-n:]), allow_iso2=False)
                if iso:
                    hits = self._cities(" ".join(words[:-n]), iso)
                    return (self._place(hits[0]), []) if hits else (self._country_place(iso), [])
        return None, []

    def _alias(self, target: str) -> tuple[Optional[Place], list[Place]]:
        name, _, cc = target.partition(",")
        hits = self._cities(name.strip(), cc.strip().upper() or None)
        return (self._place(hits[0]), []) if hits else (None, [])

    # ------------------------------------------------------------- suggest

    def suggest(self, text: str, n: int = 6) -> list[Place]:
        """Close spellings for the presenter's one-click fix of an unplaced answer."""
        best, others = self.resolve(text)
        out = ([best] if best else []) + others
        key = fold(PREFIX.sub("", text or ""))
        parts = [w for w in SPLIT.split(key) if w] or [key]
        # each part, then each longer word in it ("sevila de los mares" → "sevila")
        words = parts + [w for part in parts for w in part.split() if len(w) >= 4 and w != part]
        for w in words:
            if len(out) >= n or not w:
                break
            pool = [k for k in self.index if k[:1] == w[:1]]
            for match in difflib.get_close_matches(w, pool, n=n, cutoff=0.72):
                for i in self.index[match][:2]:
                    p = self._place(i)
                    if all(p.geonameid != q.geonameid for q in out):
                        out.append(p)
        return out[:n]


_lock = threading.Lock()
_instance: Optional[Gazetteer] = None


def gazetteer() -> Gazetteer:
    """The one loaded gazetteer (loaded on first use, ~1 s)."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = Gazetteer()
        return _instance
