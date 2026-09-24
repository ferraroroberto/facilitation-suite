"""Groups: the ported algorithm's rules for every group size, the roster, groups.yaml,
the Zoom exports, the reveal, and the API."""

from __future__ import annotations

import random
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from src.groups import roster as rs
from src.groups.shuffle import _phase3_target_sizes, build_groups
from src.live.plan import build_run
from src.sessions.model import parse_session
from tests.fixtures.demo import PLAN, build_demo_session


def _people(n: int) -> list[str]:
    return [f"P{i:02d}" for i in range(n)]


@pytest.mark.parametrize("n", range(2, 46))
def test_every_round_places_everyone_once_and_keeps_the_rules(n: int) -> None:
    random.seed(n)
    present = _people(n)
    r = build_groups(present)
    for key in ("pairs", "g4a", "g4b"):
        flat = [p for g in r[key] for p in g]
        assert sorted(flat) == sorted(present), key  # a partition: nobody lost, nobody twice

    sizes = [len(u) for u in r["pairs"]]
    trios = sizes.count(3)
    assert set(sizes) <= {2, 3} and trios == (1 if n % 2 else 0)
    if n % 4 == 1:
        assert sizes[0] == 3  # the trio first …
    if n % 4 == 3:
        assert sizes[-1] == 3  # … or last, so merges never split it

    home = {p: i for i, g in enumerate(r["g4a"]) for p in g}
    for unit in r["pairs"]:
        assert len({home[p] for p in unit}) == 1  # round A never splits a pair
    if n % 4 == 2 and n > 2:
        assert sorted(len(g) for g in r["g4a"])[-1] == 6  # one block of six, not a stray pair

    assert sorted(len(g) for g in r["g4b"]) == sorted(_phase3_target_sizes(n))
    if n >= 3:
        assert min(len(g) for g in r["g4b"]) >= 3


def test_round_b_mixes_when_it_can() -> None:
    random.seed(1)
    r = build_groups(_people(24))
    assert rs.mixing(r) == 0  # 24 people: a layout with no round-A reunions exists and is found


def _roster(path: Path, rows: list[list], header: list[str]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_roster_columns_by_header_in_any_order(tmp_path: Path) -> None:
    path = _roster(tmp_path / "r.xlsx", [["x@example.com", "Zoe", "0", "Madrid"], [None, "Álvaro", "1", "Lima"],
                                          ["", "  ", "1", ""], ["a@example.com", "Ana", None, ""]],
                   ["Email", "Name", "Present", "Country"])
    people = rs.read_roster(path)
    assert [p.name for p in people] == ["Álvaro", "Ana", "Zoe"]  # sorted, accents ignored, blank names dropped
    assert [p.present for p in people] == [True, True, False]
    assert people[2].email == "x@example.com" and people[0].country == "Lima"


def test_roster_without_a_name_header_uses_the_first_column(tmp_path: Path) -> None:
    path = _roster(tmp_path / "r.xlsx", [["Ana"], ["Bea"]], ["Participante"])
    assert [p.name for p in rs.read_roster(path)] == ["Ana", "Bea"]


def test_import_presence_shuffle_and_zoom_exports(tmp_path: Path) -> None:
    folder = tmp_path / "session"
    folder.mkdir()
    src = _roster(tmp_path / "list.xlsx", [[f"Person {i}", 1, f"p{i}@example.com"] for i in range(9)] + [["No Mail", 1, ""]],
                  ["name", "present", "email"])
    rs.import_roster(folder, src)
    assert (folder / "roster.xlsx").is_file()
    rs.set_present(folder, "Person 3", False)
    state = rs.shuffle(folder, seed=3)
    assert state["present_count"] == 9 and "Person 3" not in [p for g in state["rounds"]["pairs"] for p in g]
    saved = yaml.safe_load((folder / "groups.yaml").read_text(encoding="utf-8"))
    assert saved["absent"] == ["Person 3"] and saved["rounds"] == state["rounds"]

    people, _ = rs.roster_state(folder)
    text, missing = rs.zoom_csv(people, state["rounds"]["pairs"])
    assert text is None and missing == ["No Mail"]
    rs.set_present(folder, "No Mail", False)
    state = rs.shuffle(folder, seed=3)
    people, _ = rs.roster_state(folder)
    text, missing = rs.zoom_csv(people, state["rounds"]["g4a"])
    lines = text.splitlines()
    assert lines[0] == "Pre-assign Room Name,Email Address" and len(lines) == 9 and missing == []
    assert rs.zoom_text([["A", "B"], ["C", "D", "E"]]) == "Room 1: A, B\nRoom 2: C, D, E"
    assert rs.payload(folder)["stale"] is False
    rs.set_present(folder, "Person 3", True)
    assert rs.payload(folder)["stale"] is True  # presence changed since the shuffle


def test_unknown_person_and_too_few(tmp_path: Path) -> None:
    folder = tmp_path / "s"
    folder.mkdir()
    rs.import_roster(folder, _roster(tmp_path / "x.xlsx", [["Solo"]], ["name"]))
    with pytest.raises(rs.RosterError) as err:
        rs.set_present(folder, "Nobody", False)
    assert err.value.code == "unknown_person"
    with pytest.raises(rs.RosterError) as err:
        rs.shuffle(folder)
    assert err.value.code == "too_few"


def test_the_reveal_item_carries_its_rooms() -> None:
    rounds = {"pairs": [["A", "B"]], "g4a": [["A", "B", "C", "D"]], "g4b": [["A", "C", "B", "D"]]}
    plan = dict(PLAN)
    run = build_run(parse_session(plan), None, rounds)
    assert all("rooms" not in it for it in run["items"])  # the demo's reveal is switched off
    plan["sections"] = [{"name": "S", "items": [{"kind": "activity", "type": "groups_reveal", "options": {"round": "g4a"}}]}]
    run = build_run(parse_session(plan), None, rounds)
    assert run["items"][0]["rooms"] == [["A", "B", "C", "D"]]


def test_groups_api(client, isolated_env: Path) -> None:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "groups", isolated_env / "sessions.local.yaml")
    base = f"/api/sessions/{sid}"
    g = client.get(f"{base}/groups").json()
    assert (g["present"], g["total"], g["rounds"]) == (38, 40, None)
    readiness = {c["key"]: c for c in client.get(base).json()["readiness"]}
    assert readiness["roster"]["detail"] == "38 present of 40" and readiness["groups"]["state"] == "todo"

    g = client.post(f"{base}/groups/shuffle", json={"seed": 5}).json()
    assert len(g["rounds"]["pairs"]) == 19 and g["texts"]["pairs"].startswith("Room 1: ")
    assert len(g["missing_emails"]) == 2  # the demo roster has two people without an email
    r = client.get(f"{base}/groups/zoom.csv", params={"round": "pairs"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "missing_emails"

    g = client.put(f"{base}/groups/presence", json={"name": g["missing_emails"][0], "present": False}).json()
    assert g["stale"] is True
    readiness = {c["key"]: c for c in client.get(base).json()["readiness"]}
    assert readiness["groups"]["state"] == "warn"

    added = client.post(f"{base}/groups/reveal", json={"round": "g4a"}).json()
    session = client.get(base).json()["session"]
    assert session["sections"][0]["items"][-1]["id"] == added["item_id"]
    assert client.post(f"{base}/roster", json={"path": str(folder / "nope.xlsx")}).status_code == 404
