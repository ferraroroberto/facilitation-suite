"""Build the map's offline data into ``app/activities/map/`` (committed output).

    .venv/Scripts/python.exe scripts/build_geo.py

- ``cities.tsv.gz`` — GeoNames ``cities15000`` (every city over 15 000
  people; CC BY 4.0, https://www.geonames.org — attribution in the README
  and the app's Settings → Credits): id, name, ASCII name, country, lat,
  lon, population, and for cities over 100 000 people their Latin-script
  alternate names (``Milán``, ``Nueva York``…).
- ``countries.json`` — every country with its ISO code, its capital's
  coordinates (GeoNames ``countryInfo``) and its names in English and
  Spanish (Unicode CLDR via ``babel``, build-time only).
- ``world.svg`` — country outlines from world-atlas ``countries-50m``
  (Natural Earth, public domain), projected equirectangular with a 35°
  standard parallel (x = lon·cos 35°, y = −lat), rounded and thinned.

Network is needed only here; the app itself never downloads anything.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

from babel import Locale

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "activities" / "map"
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
COUNTRIES_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
WORLD_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json"
LATIN = re.compile(r"^[A-Za-zÀ-ɏ' .()-]{2,40}$")
ALT_MIN_POPULATION = 100_000
KX = math.cos(math.radians(35))  # x scale of the projection
SVG_SCALE = 10  # SVG units per degree


def fetch(url: str) -> bytes:
    print(f"… {url}")
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 — fixed public sources
        return r.read()


def fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def build_cities() -> dict[tuple[str, str], tuple[float, float, int]]:
    raw = zipfile.ZipFile(io.BytesIO(fetch(CITIES_URL))).read("cities15000.txt").decode("utf-8")
    lines = []
    # (country, folded name) → the most populous city of that name: finds capitals by name.
    coords: dict[tuple[str, str], tuple[float, float, int]] = {}
    for row in raw.splitlines():
        f = row.split("\t")
        gid, name, ascii_name, alts, lat, lon, cc, pop = f[0], f[1], f[2], f[3], f[4], f[5], f[8], int(f[14] or 0)
        for n in (name, ascii_name):
            k = (cc, fold(n))
            if k not in coords or coords[k][2] < pop:
                coords[k] = (float(lat), float(lon), pop)
        keep = []
        if pop >= ALT_MIN_POPULATION and alts:
            seen = {fold(name), fold(ascii_name)}
            for a in alts.split(","):
                a = a.strip()
                if LATIN.match(a) and fold(a) not in seen:
                    seen.add(fold(a))
                    keep.append(a)
        lines.append("\t".join([gid, name, ascii_name, cc, f"{float(lat):.4f}", f"{float(lon):.4f}", str(pop), "|".join(keep)]))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    (OUT / "cities.tsv.gz").write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    print(f"✅ {len(lines)} cities → cities.tsv.gz ({(OUT / 'cities.tsv.gz').stat().st_size // 1024} KB)")
    return coords


def build_countries(coords: dict[tuple[str, str], tuple[float, float, int]]) -> None:
    en, es = Locale("en"), Locale("es")
    out = []
    for row in fetch(COUNTRIES_URL).decode("utf-8").splitlines():
        if not row or row.startswith("#"):
            continue
        f = row.split("\t")
        iso, iso3, name, capital = f[0], f[1], f[4], f[5]
        names = {name, en.territories.get(iso, name), es.territories.get(iso, name)}
        lat, lon, _ = coords.get((iso, fold(capital)), (None, None, 0))
        if lat is None:  # the capital is spelled differently in the city list: its largest city
            best = max((v for k, v in coords.items() if k[0] == iso), key=lambda v: v[2], default=None)
            lat, lon = (best[0], best[1]) if best else (None, None)
        out.append({"iso": iso, "iso3": iso3, "name": en.territories.get(iso, name), "name_es": es.territories.get(iso, name),
                    "names": sorted(n for n in names if n), "capital": capital, "lat": lat, "lon": lon})
    (OUT / "countries.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"✅ {len(out)} countries → countries.json")


def _arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def _ring(indexes: list[int], arcs: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in indexes:
        seg = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        pts.extend(seg if not pts else seg[1:])
    return pts


def _path(ring: list[tuple[float, float]]) -> str:
    out, last = [], None
    for lon, lat in ring:
        p = (round(lon * KX * SVG_SCALE), round(-lat * SVG_SCALE))
        if p != last:
            out.append(p)
            last = p
    if len(out) < 3:
        return ""
    return "M" + "L".join(f"{x} {y}" for x, y in out) + "Z"


def build_world() -> None:
    topo = json.loads(fetch(WORLD_URL))
    arcs = _arcs(topo)
    paths = []
    for geo in topo["objects"]["countries"]["geometries"]:
        polys = geo.get("arcs") or []
        if geo.get("type") == "Polygon":
            polys = [polys]
        d = "".join(_path(_ring(ring, arcs)) for poly in polys for ring in poly)
        if d:
            paths.append(f'<path d="{d}"/>')
    w, h = round(180 * KX * SVG_SCALE), 90 * SVG_SCALE
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-w} {-h} {2 * w} {2 * h}" '
           f'data-kx="{KX:.6f}" data-scale="{SVG_SCALE}">\n'
           "<!-- Country outlines: world-atlas countries-50m (Natural Earth, public domain). -->\n"
           f'<g class="land">{"".join(paths)}</g></svg>\n')
    (OUT / "world.svg").write_text(svg, encoding="utf-8")
    print(f"✅ {len(paths)} countries → world.svg ({(OUT / 'world.svg').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    build_countries(build_cities())
    build_world()
