"""The magic map: offline geocoding, room-aware choice, unplaced answers and the manual fix."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.activities.registry import result_for
from src.chat.hub import ChatHub
from src.config import load_config
from src.geo.gazetteer import gazetteer
from src.live.capture import CaptureService
from src.live.hub import LiveHub, now_ms
from src.sessions.store import SessionStore
from tests.fixtures.demo import build_demo_session


@pytest.mark.parametrize(("text", "city", "country"), [
    ("Milan, italy", "Milan", "IT"),        # the step's acceptance examples
    ("Sevilla, España", "Sevilla", "ES"),
    ("CDMX", "Mexico City", "MX"),
    ("desde Bogotá", "Bogotá", "CO"),
    ("hola! estoy en Málaga", "Málaga", "ES"),
    ("Lisboa - Portugal", "Lisbon", "PT"),
    ("Milan Italy", "Milan", "IT"),
    ("Londres", "London", "GB"),
    ("Buenos Aires, Argentina", "Buenos Aires", "AR"),
    ("Montevideo, UY", "Montevideo", "UY"),
    ("NYC", "New York City", "US"),
])
def test_resolves_what_people_type(text: str, city: str, country: str) -> None:
    place, _ = gazetteer().resolve(text)
    assert place is not None and (place.name, place.country, place.precision) == (city, country, "city")


@pytest.mark.parametrize(("text", "city", "country"), [
    ("zaragoza!!", "Zaragoza", "ES"),       # an alternate name of Tijuana
    ("Zaragoza", "Zaragoza", "ES"),
    ("cancun", "Cancún", "MX"),             # an alternate name of Changchun
    ("Cancún", "Cancún", "MX"),
    ("guadalupe", "Guadalupe", "MX"),
    ("pekin", "Beijing", "CN"),             # a much larger city's alternate still wins
    ("colonia", "Köln", "DE"),
])
def test_a_citys_own_name_outranks_an_alternate_name(text: str, city: str, country: str) -> None:
    place, others = gazetteer().resolve(text)
    assert place is not None and (place.name, place.country) == (city, country)
    assert place.match == ("alternate" if text in ("pekin", "colonia") else "name")
    assert all(o.geonameid != place.geonameid for o in others)


def test_the_alternate_name_city_stays_a_candidate() -> None:
    _, others = gazetteer().resolve("zaragoza")
    assert "Tijuana" in [o.name for o in others]


def test_a_lone_answer_is_not_pinned_on_a_larger_alternate_name_city() -> None:
    pin = result_for("map", {}, _msgs(("Leo", "zaragoza!!")))["pins"][0]
    assert (pin["name"], pin["country"]) == ("Zaragoza", "ES")


@pytest.mark.parametrize(("text", "city", "country"), [
    ("florencia", "Florence", "IT"),        # the Spanish name of a city a smaller own-name town shares
    ("gerona", "Girona", "ES"),
    ("lerida", "Lleida", "ES"),
    ("palmas", "Las Palmas de Gran Canaria", "ES"),
    ("jalapa", "Xalapa de Enríquez", "MX"),
    ("hull", "Kingston upon Hull", "GB"),
    ("odessa", "Odesa", "UA"),
    ("pittsburg", "Pittsburgh", "US"),
    ("edinburg", "Edinburgh", "GB"),
    ("puno", "Puno", "PE"),                 # a far larger city lists the name as an alternate
    ("alzira", "Alzira", "ES"),
    ("petropolis", "Petrópolis", "BR"),
    ("dili", "Dili", "TL"),
    ("thai nguyen", "Thái Nguyên", "VN"),
])
def test_a_lone_answer_lands_on_the_city_people_mean(text: str, city: str, country: str) -> None:
    pin = result_for("map", {}, _msgs(("Leo", text)))["pins"][0]
    assert (pin["name"], pin["country"]) == (city, country)


@pytest.mark.parametrize(("text", "city", "country"), [
    ("zaragoza", "Zaragoza", "ES"),
    ("cancun", "Cancún", "MX"),
    ("pekin", "Beijing", "CN"),
    ("colonia", "Köln", "DE"),
])
def test_the_alias_lines_leave_the_ranked_names_alone(text: str, city: str, country: str) -> None:
    pin = result_for("map", {}, _msgs(("Leo", text)))["pins"][0]
    assert (pin["name"], pin["country"]) == (city, country)


def test_the_room_can_still_pull_an_alternate_name_city_in() -> None:
    room = result_for("map", {}, _msgs(("Ana", "Tijuana"), ("Sam", "Mexicali, México"), ("Bea", "Tecate, México"), ("Leo", "zaragoza")))
    assert [p["country"] for p in room["pins"]] == ["MX", "MX", "MX", "MX"]
    assert room["pins"][-1]["name"] == "Tijuana"


@pytest.mark.parametrize(("text", "country"), [("España", "ES"), ("Spain", "ES"), ("Brasil", "BR"), ("EEUU", "US"), ("UK", "GB")])
def test_a_country_alone_lands_on_its_capital(text: str, country: str) -> None:
    place, _ = gazetteer().resolve(text)
    assert place is not None and place.country == country and place.precision == "country"


def test_nonsense_is_not_placed_and_gets_suggestions() -> None:
    assert gazetteer().resolve("asdf qwer")[0] is None
    assert ("Granada", "ES") in [(p.name, p.country) for p in gazetteer().suggest("Grnada")]


def _msgs(*rows: tuple[str, str]) -> list[dict]:
    return [{"id": i + 1, "sender": s, "text": t, "time": "", "received_at": 0} for i, (s, t) in enumerate(rows)]


def test_the_room_decides_an_ambiguous_city() -> None:
    alone = result_for("map", {}, _msgs(("Leo", "Valencia")))
    assert alone["pins"][0]["country"] == "VE"  # the larger Valencia
    room = result_for("map", {}, _msgs(("Ana", "Madrid, España"), ("Sam", "Sevilla, España"), ("Leo", "Valencia")))
    assert [p["country"] for p in room["pins"]] == ["ES", "ES", "ES"]
    assert room["countries"] == 1


def test_latest_answer_per_person_and_unplaced() -> None:
    r = result_for("map", {}, _msgs(("Tom", "zzzz"), ("Tom", "Barcelona"), ("Ivy", "qqqq"), ("Ana", "Madrid"), ("Bea", "Madrid")))
    assert {p["sender"]: p["name"] for p in r["pins"]} == {"Tom": "Barcelona", "Ana": "Madrid", "Bea": "Madrid"}
    assert [u["sender"] for u in r["unplaced"]] == ["Ivy"]
    assert r["cities"] == [{"name": "Madrid", "country_name": "España", "names": ["Ana", "Bea"], "count": 2}]


def test_a_place_fixed_by_hand_wins_and_is_saved(isolated_env: Path) -> None:
    sid, _ = build_demo_session(isolated_env / "sessions" / "demo" / "map", isolated_env / "sessions.local.yaml")
    live = LiveHub(SessionStore(load_config()))
    chat = ChatHub(live)
    cap = CaptureService(live, chat)
    live.activate(sid)
    live.goto(live.item_by_id("act-map")["index"])
    cap.toggle()
    chat.ingest("", [{"sender": "Ian", "text": "Grnada", "time": "", "received_at": now_ms() + 5}], baseline=False, source="zoom")
    result = live.snapshot()["state"]["capture"]["result"]
    assert [u["text"] for u in result["unplaced"]] == ["Grnada"]
    granada = next(p for p in gazetteer().suggest("Grnada") if p.country == "ES")
    cap.place(chat.messages[-1]["id"], granada.geonameid)
    result = live.snapshot()["state"]["capture"]["result"]
    assert result["unplaced"] == [] and result["pins"][0]["name"] == "Granada"
    fresh = CaptureService(live, ChatHub(live))  # read back from captures.json
    fresh._session = None
    fresh._load()
    assert fresh.places == cap.places


def test_geo_api(client, isolated_env: Path) -> None:
    got = client.get("/api/geo/suggest", params={"q": "Grnada"}).json()["places"]
    assert any(p["name"] == "Granada" for p in got)
    assert client.get("/api/geo/suggest").json() == {"places": []}
    r = client.post("/api/live/place", json={"message_id": 1, "geonameid": "123"})
    assert r.status_code == 409  # nothing is live
    assert client.post("/api/live/place", json={"message_id": 1, "geonameid": "bad id"}).status_code == 422


def test_the_world_outline_never_jumps_across_the_map() -> None:
    # A ring crossing the 180° meridian must go on past the edge: a jump back
    # across drew a filled band over the North Atlantic.
    import re

    svg = (Path(__file__).resolve().parents[1] / "app" / "activities" / "map" / "world.svg").read_text(encoding="utf-8")
    width = int(re.search(r'viewBox="\S+ \S+ (\d+)', svg).group(1))
    for ring in re.findall(r"M([^Z]+)Z", svg):
        xs = [int(p.split()[0]) for p in ring.split("L")]
        assert max(abs(b - a) for a, b in zip(xs, xs[1:], strict=False)) < width / 2
