"""The offline replay (``src.activities.replay``): a recorded session's captures recomputed, compared, drawn."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from src.activities import replay
from src.activities.registry import counted_for, parser
from tests.fixtures.demo import build_demo_session, write_demo_run


@pytest.fixture
def recorded(isolated_env: Path) -> Path:
    _, folder = build_demo_session(isolated_env / "sessions" / "demo" / "replay")
    write_demo_run(folder, pngs=False)
    return folder


def _digest(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_replay_without_overrides_equals_every_frozen_result_and_leaves_the_folder_untouched(
        recorded: Path, tmp_path: Path) -> None:
    before = _digest(recorded)
    out = tmp_path / "out"
    assert replay.main([str(recorded), "--out", str(out)]) == 0
    assert _digest(recorded) == before
    frozen = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (recorded / "live" / "captures").glob("*.json")}
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))["items"]
    assert sorted(c["item_id"] for c in summary) == sorted(frozen)
    for c in summary:
        assert c["same_result"], c["item_id"]
        replayed = json.loads((out / f"{c['item_id']}.json").read_text(encoding="utf-8"))
        assert replayed["result"] == frozen[c["item_id"]]["result"]
        assert replayed["answers"] == frozen[c["item_id"]]["answers"]
    cloud = next(c for c in summary if c["item_id"] == "act-kryptonite")
    assert cloud["whole_before"] == cloud["whole_after"] and cloud["added"] == cloud["removed"] == [] and not cloud["merged"]


def test_replay_never_writes_inside_the_session_folder(recorded: Path) -> None:
    with pytest.raises(ValueError):
        replay.replay(recorded, recorded / "replay")


def test_a_set_override_changes_only_the_named_option(recorded: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = replay.replay(recorded, out, items=["act-kryptonite", "act-weather"], sets={"terms": "words"})
    frozen_item = json.loads((recorded / "live" / "captures" / "act-kryptonite.json").read_text(encoding="utf-8"))["item"]
    item = json.loads((out / "act-kryptonite.json").read_text(encoding="utf-8"))["item"]
    assert item["options"] == {**(frozen_item["options"] or {}), "terms": "words"}
    weather = json.loads((out / "act-weather.json").read_text(encoding="utf-8"))["item"]
    assert "terms" not in (weather["options"] or {})  # a scale has no such option
    cloud = next(c for c in summary if c["item_id"] == "act-kryptonite")
    assert not cloud["same_result"]
    assert "meetings without agenda" in cloud["removed"] and "agenda" in cloud["added"]
    assert cloud["whole_after"] < cloud["whole_before"]
    assert "meetings without agenda" in [a["text"] for a in cloud["not_whole"]]


def test_compare_reports_an_entry_merged_into_another() -> None:
    mod = parser("word_cloud")
    opts = {"terms": "auto", "merge_variants": True}

    def record(texts: list[str], merge: bool) -> dict:
        msgs = [{"id": i, "sender": f"P{i}", "text": t} for i, t in enumerate(texts, 1)]
        parsed = [mod.parse(m, opts) for m in msgs]
        result = mod.aggregate([p for p in parsed if p], {**opts, "merge_variants": merge})
        return {"item": {"id": "act-x", "type": "word_cloud"}, "result": result,
                "answers": [dict(m, hidden=False, parsed=p) for m, p in zip(msgs, parsed, strict=True)]}

    texts = ["Calma", "calma", "Humor"]
    cmp = replay.compare(record(texts, merge=False), record(texts, merge=True))
    assert cmp["merged"] == {"Calma": "calma"} and cmp["count_changes"] == {"calma": [1, 2]}
    assert cmp["whole_before"] == cmp["whole_after"] == 3


@pytest.mark.parametrize(("text", "whole"), [
    ("perfeccionismo", True), ("miedo a fallar", True), ("¡Sí!", True), ("jaja reuniones", True),
    ("reuniones sin agenda y sin fin", False),
])
def test_word_cloud_says_whether_an_answer_is_shown_whole(text: str, whole: bool) -> None:
    mod = parser("word_cloud")
    opts = {"terms": "auto", "merge_variants": True, "stopwords": "es"}
    parsed = mod.parse({"text": text, "sender": "P"}, opts)
    result = mod.aggregate([parsed], opts)
    c = counted_for("word_cloud", text, parsed, result)
    assert c is not None and c["whole"] is whole and c["entries"]


def test_render_draws_each_replayed_cloud_through_the_real_stage(recorded: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert replay.main([str(recorded), "--out", str(out), "--item", "act-kryptonite", "--render"]) == 0
    with Image.open(out / "act-kryptonite.png") as im:
        assert im.size == (1920, 1080)
    assert not list(out.glob("render-*"))  # the temp copy of the session is gone
