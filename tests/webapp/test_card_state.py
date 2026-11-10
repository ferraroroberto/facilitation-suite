"""A card's load state (``static/js/card-state.js``): a closed set and a transition table that refuses a bad move.

The module has no imports, so Node runs it as it is."""

from __future__ import annotations

import pytest

from src.config import PROJECT_ROOT
from tests.webapp.node_probe import probe_module

MODULE = PROJECT_ROOT / "app" / "webapp" / "static" / "js" / "card-state.js"
STATES = ["loading", "ready", "empty", "error"]

SCRIPT = """
const m = await import(process.argv[1]);
const all = Object.values(m.CardState);
const out = { states: all, meanings: Object.keys(m.MEANINGS), table: Object.keys(m.TRANSITIONS), moves: {}, starts: {} };
for (const a of all) {
  for (const b of all) {
    try { m.advance(a, b); out.moves[a + '>' + b] = true; } catch { out.moves[a + '>' + b] = false; }
  }
}
for (const b of all) {
  try { m.advance(undefined, b); out.starts[b] = true; } catch { out.starts[b] = false; }
}
try { m.advance('ready', 'bogus'); out.unknown = true; } catch { out.unknown = false; }
const card = { dataset: {} };
m.setCardState(card, 'loading');
m.setCardState(card, m.settledState(3));
out.settled = [m.settledState(0), m.settledState(3), card.dataset.state];
try { m.setCardState(card, 'loading'); m.setCardState(card, 'loading'); out.twice = true; } catch { out.twice = false; }
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def probed() -> dict:
    return probe_module(MODULE, SCRIPT)


def test_the_state_set_is_closed_and_every_state_says_what_it_means(probed: dict) -> None:
    assert probed["states"] == STATES
    assert probed["meanings"] == probed["table"] == STATES


# state-machine-proof: card-load
def test_an_illegal_move_is_refused(probed: dict) -> None:
    refused = {k for k, ok in probed["moves"].items() if not ok}
    assert refused == {"loading>loading", "empty>loading", "error>loading"}  # only a ready card goes back to loading
    assert probed["unknown"] is False and probed["twice"] is False  # a state outside the set, and a second reload


def test_a_listed_move_is_allowed_and_a_new_card_may_start_anywhere(probed: dict) -> None:
    assert probed["moves"]["loading>ready"] and probed["moves"]["ready>loading"] and probed["moves"]["error>ready"]
    assert all(probed["starts"].values())


def test_a_finished_read_settles_ready_or_empty(probed: dict) -> None:
    assert probed["settled"] == ["empty", "ready", "ready"]
