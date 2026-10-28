"""The analysis dump (``python -m src.results.dump``): one JSON document of a session's results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.jsonl import read_jsonl
from src.results.aliases import Aliases, write_aliases
from src.results.collect import load_results, participation
from src.results.dump import dump, main
from src.sessions.model import parse_session
from tests.fixtures.demo import PLAN, build_demo_session, write_demo_run

PLAYBOOK = "# Playbook\n\n## Output format\n\n- Notes per participant\n"
NOTES = "Two people joined late.\n"


def _demo(isolated_env: Path, name: str = "results") -> Path:
    _, folder = build_demo_session(isolated_env / "sessions" / "demo" / name, isolated_env / "sessions.local.yaml")
    write_demo_run(folder)
    return folder


@pytest.fixture(autouse=True)
def _no_log_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI installs the process logging; under pytest the log stays with caplog."""
    monkeypatch.setattr("src.results.dump.configure_logging", lambda **_: None)


def _cli(capsys: pytest.CaptureFixture[str], arg: str) -> tuple[int, dict | None, str]:
    code = main(["dump", arg])
    out, err = capsys.readouterr()
    return code, json.loads(out) if code == 0 else None, err


def test_the_dump_is_json_with_the_results_and_counts_match_participation(isolated_env: Path, capsys) -> None:
    folder = _demo(isolated_env)
    code, doc, _ = _cli(capsys, str(folder))
    assert code == 0
    assert doc["session"] == {"title": PLAN["title"], "date": "2026-10-27T18:00:00+01:00"}
    assert doc["report_dir"] == str(folder / "report") and not (folder / "report").exists()  # it writes nothing
    assert [a["number"] for a in doc["activities"]] == list(range(1, 7))
    assert doc["playbook"] is None and doc["notes"] is None

    data = load_results(folder, parse_session(PLAN))
    people = {p["name"]: p for p in participation(data["activities"], read_jsonl(folder / "live" / "chat.jsonl"))}
    rows = {r["name"]: r for r in doc["participation"]["rows"]}
    assert rows.keys() == people.keys()
    assert all((rows[n]["total"], rows[n]["messages"]) == (p["total"], p["messages"]) for n, p in people.items())
    assert all(r["early"] + r["late"] == r["total"] for r in rows.values())
    assert sum(len(a["answer_rows"]) for a in doc["activities"]) == sum(r["total"] for r in rows.values())


def test_a_hidden_answer_is_not_in_the_dump(isolated_env: Path) -> None:
    kr = next(a for a in dump(_demo(isolated_env))["activities"] if a["id"] == "act-kryptonite")
    assert (kr["answers"], kr["hidden"], len(kr["answer_rows"])) == (8, 1, 8)
    assert "hello" not in {r["text"] for r in kr["answer_rows"]}


def test_the_playbook_and_the_notes_ride_along_when_present(isolated_env: Path) -> None:
    folder = _demo(isolated_env)
    (folder / "playbook.md").write_text(PLAYBOOK, encoding="utf-8")
    (folder / "notes.md").write_text(NOTES, encoding="utf-8")
    doc = dump(folder)
    assert (doc["playbook"], doc["notes"]) == (PLAYBOOK, NOTES)


def test_merged_names_and_excluded_senders_shape_the_people(isolated_env: Path) -> None:
    folder = _demo(isolated_env)
    first, second, host = list(dict.fromkeys(r["sender"] for r in dump(folder)["activities"][0]["answer_rows"]))[:3]
    write_aliases(folder, Aliases({first: [second]}))
    raw = yaml.safe_load((folder / "session.yaml").read_text(encoding="utf-8"))
    (folder / "session.yaml").write_text(yaml.safe_dump({**raw, "excluded_senders": [host]}), encoding="utf-8")
    doc = dump(folder)
    names = {r["name"] for r in doc["participation"]["rows"]}
    senders = {r["sender"] for a in doc["activities"] for r in a["answer_rows"]}
    assert first in names and not {second, host} & (names | senders)
    assert any(r["as"] == second for a in doc["activities"] for r in a["answer_rows"])
    assert (doc["merged_names"], doc["excluded_senders"]) == ({first: [second]}, [host])


def test_a_folder_without_a_session_fails_with_a_clear_message(tmp_path: Path, capsys, caplog) -> None:
    code, _, _ = _cli(capsys, str(tmp_path))
    assert code == 2 and "session.yaml is missing" in caplog.text
    assert main(["dump"]) == 1 and "usage" in capsys.readouterr().err


def test_last_picks_the_ledger_session_with_the_latest_date(isolated_env: Path, capsys) -> None:
    older = _demo(isolated_env, "older")
    newer = _demo(isolated_env, "newer")
    raw = yaml.safe_load((older / "session.yaml").read_text(encoding="utf-8"))
    (older / "session.yaml").write_text(yaml.safe_dump({**raw, "date": "2026-09-01T18:00:00+02:00"}), encoding="utf-8")
    code, doc, _ = _cli(capsys, "--last")
    assert code == 0 and doc["folder"] == str(newer)


def test_likely_merges_are_suggested_as_the_results_tab_suggests_them(isolated_env: Path) -> None:
    folder = _demo(isolated_env)
    first = dump(folder)["activities"][0]["answer_rows"][0]["sender"]
    with (folder / "live" / "chat.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"sender": f"iPhone de {first}", "text": "back", "time": "19:00"}) + "\n")
    assert {"canonical": first, "aliases": [f"iPhone de {first}"]} in dump(folder)["merge_suggestions"]
