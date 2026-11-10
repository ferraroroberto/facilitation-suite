"""The deck-import job state: a closed set, a transition table, and the wire fields derived from it."""

from __future__ import annotations

import pytest

from src.importer.job_state import (
    MEANINGS,
    TRANSITIONS,
    IllegalImportTransition,
    ImportState,
    advance,
    is_active,
    is_succeeded,
)
from src.importer.service import ImportJob


def test_the_state_set_is_closed_and_every_state_says_what_it_means() -> None:
    assert {s.value for s in ImportState} == {"queued", "running", "done", "error"}
    assert set(MEANINGS) == set(ImportState) == set(TRANSITIONS) and all(MEANINGS.values())


# state-machine-proof: import-job
@pytest.mark.parametrize("current,to", [
    (ImportState.QUEUED, ImportState.DONE),     # skipping the export
    (ImportState.QUEUED, ImportState.QUEUED),
    (ImportState.RUNNING, ImportState.QUEUED),  # going back
    (ImportState.DONE, ImportState.RUNNING),    # a finished job running again
    (ImportState.DONE, ImportState.ERROR),
    (ImportState.ERROR, ImportState.DONE),
])
def test_an_illegal_move_is_refused(current: ImportState, to: ImportState) -> None:
    job = ImportJob(id="j", session_id="s", deck="d.pptx", state=current)
    with pytest.raises(IllegalImportTransition):
        advance(current, to)
    with pytest.raises(IllegalImportTransition):
        job.move(to)
    assert job.state is current


@pytest.mark.parametrize("final", [ImportState.DONE, ImportState.ERROR])
def test_the_legal_path_runs_queued_to_running_to_a_final_state(final: ImportState) -> None:
    job = ImportJob(id="j", session_id="s", deck="d.pptx")
    assert job.state is ImportState.QUEUED
    job.move(ImportState.RUNNING)
    job.move(final)
    assert job.state is final


def test_the_wire_form_keeps_state_and_derives_active_and_succeeded() -> None:
    rows = {s: ImportJob(id="j", session_id="s", deck="d", state=s).as_dict() for s in ImportState}
    assert {s: (r["state"], r["active"], r["succeeded"]) for s, r in rows.items()} == {
        ImportState.QUEUED: ("queued", True, False),
        ImportState.RUNNING: ("running", True, False),
        ImportState.DONE: ("done", False, True),
        ImportState.ERROR: ("error", False, False),
    }
    assert all(is_active(s) == r["active"] and is_succeeded(s) == r["succeeded"] for s, r in rows.items())
