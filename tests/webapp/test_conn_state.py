"""The quiz player's connection label (``player/static/conn-state.js``): the state the page's facts say.

The module has no imports, so Node runs it as it is."""

from __future__ import annotations

import itertools
import json

import pytest

from src.config import PROJECT_ROOT
from tests.webapp.node_probe import probe_module

MODULE = PROJECT_ROOT / "app" / "webapp" / "player" / "static" / "conn-state.js"

SCRIPT = """
const m = await import(process.argv[1]);
const out = { states: Object.values(m.ConnState), meanings: Object.keys(m.MEANINGS), derived: {}, refused: [] };
for (const online of [null, true, false]) for (const polling of [false, true]) for (const wsOpen of [false, true]) for (const joined of [false, true]) {
  out.derived[JSON.stringify([online, polling, wsOpen, joined])] = m.connState({ online, polling, wsOpen, joined });
}
for (const bad of [undefined, 'yes', 1, 0]) {
  try { m.connState({ online: bad, polling: false, wsOpen: false, joined: true }); out.refused.push(false); } catch { out.refused.push(true); }
}
console.log(JSON.stringify(out));
"""


def expected(online: bool | None, polling: bool, ws_open: bool, joined: bool) -> str:
    """The label the page showed before the machine was declared, spelled out as its own table."""
    if online is False:
        return "offline"
    if not joined and online:
        return "online"
    if polling:
        return "polling"
    return "online" if ws_open else "checking"


@pytest.fixture(scope="module")
def probed() -> dict:
    return probe_module(MODULE, SCRIPT)


def test_the_state_set_is_closed_and_every_state_says_what_it_means(probed: dict) -> None:
    assert probed["states"] == ["checking", "online", "polling", "offline"]
    assert sorted(probed["meanings"]) == sorted(probed["states"])


def test_every_combination_of_facts_gets_its_state(probed: dict) -> None:
    for online, polling, ws_open, joined in itertools.product([None, True, False], [False, True], [False, True], [False, True]):
        key = json.dumps([online, polling, ws_open, joined], separators=(",", ":"))
        assert probed["derived"][key] == expected(online, polling, ws_open, joined), key


# state-machine-proof: player-connection
def test_a_ping_answer_outside_null_true_false_is_refused(probed: dict) -> None:
    assert probed["refused"] == [True, True, True, True]
