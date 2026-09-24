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
