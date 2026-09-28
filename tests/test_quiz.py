"""The quiz item format and the Kahoot template import (#50): the reader, the items, the API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.activities.registry import ACTIVITIES_DIR
from src.live.plan import build_run
from src.quiz.importer import build_items, import_kahoot, read_kahoot_xlsx
from src.quiz.model import POINTS, TIME_LIMITS, QuizError, question_from_item
from src.sessions.model import Session, dump_session, parse_session
from tests.fixtures.kahoot import QUESTIONS, make_kahoot_xlsx


@pytest.fixture
def template(tmp_path: Path) -> Path:
    return make_kahoot_xlsx(tmp_path / "Planets quiz.xlsx")


# ---- the reader ----

def test_the_template_reads_every_question_as_written(template: Path) -> None:
    qs = read_kahoot_xlsx(template)
    assert len(qs) == len(QUESTIONS)
    assert qs[0].question == "Which planet is known as the red planet?"
    assert qs[0].answers == ["Venus", "Mars", "Jupiter", "Saturn"]
    assert [q.correct for q in qs] == [[2], [1, 3], [1], [1], [1, 3, 4]]  # an int, "1,3", "1", "1, 3,4"
    assert [q.time_limit for q in qs] == [20, 30, 10, 60, 240]
    assert qs[2].answers == ["Yes", "No", "", ""]  # two answers are enough
    assert qs[3].question.startswith("¿Cuál") and qs[3].answers[2] == "Bogotá"  # non-ASCII kept
    assert qs[4].answers[0] == "2"  # a number typed as an answer reads as text


def test_the_questions_end_at_the_first_empty_question_cell(tmp_path: Path) -> None:
    # Column A numbers rows 9–108 (make_kahoot_xlsx), and a stray row after the gap is ignored.
    rows = [QUESTIONS[0], QUESTIONS[1], [None, "orphan", "answer", None, None, 20, 1], QUESTIONS[2]]
    qs = read_kahoot_xlsx(make_kahoot_xlsx(tmp_path / "gap.xlsx", rows))
    assert [q.question for q in qs] == [QUESTIONS[0][0], QUESTIONS[1][0]]


def test_the_header_is_found_by_its_texts_not_its_cell(tmp_path: Path) -> None:
    path = make_kahoot_xlsx(tmp_path / "moved.xlsx", QUESTIONS[:2], header_row=12, first_col=1, numbers=False)
    assert [q.correct for q in read_kahoot_xlsx(path)] == [[2], [1, 3]]


def test_an_empty_time_limit_takes_the_default(tmp_path: Path) -> None:
    path = make_kahoot_xlsx(tmp_path / "t.xlsx", [["Q?", "a", "b", None, None, None, 1]])
    assert read_kahoot_xlsx(path)[0].time_limit == 20


@pytest.mark.parametrize(("row", "reason"), [
    (["Q?", "a", "b", "c", "d", 20, None], "no_correct"),
    (["Q?", "a", "b", None, None, 20, 3], "correct_empty_answer"),
    (["Q?", "a", "b", None, None, 20, "1,4"], "correct_empty_answer"),
    (["Q?", "a", None, None, None, 20, 1], "too_few_answers"),
    (["Q?", "a", "b", "c", "d", 15, 1], "bad_time_limit"),
    (["Q?", "a", "b", "c", "d", "soon", 1], "bad_time_limit"),
    (["Q?", "a", "b", "c", "d", 20, 5], "bad_correct"),
    (["Q?", "a", "b", "c", "d", 20, "A"], "bad_correct"),
])
def test_an_invalid_row_is_refused_naming_its_row(tmp_path: Path, row: list, reason: str) -> None:
    path = make_kahoot_xlsx(tmp_path / "bad.xlsx", [QUESTIONS[0], row])
    with pytest.raises(QuizError) as err:
        read_kahoot_xlsx(path)
    assert err.value.code == "quiz_row_invalid" and err.value.status == 422
    assert str(err.value).startswith("Row 10: ")  # header row 8, the bad row is the second question
    assert err.value.detail == {"row": 10, "reason": reason}


def test_a_file_that_is_not_the_template_is_refused(tmp_path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["name", "email"])
    wb.save(tmp_path / "roster.xlsx")
    with pytest.raises(QuizError) as err:
        read_kahoot_xlsx(tmp_path / "roster.xlsx")
    assert err.value.code == "quiz_no_header"

    with pytest.raises(QuizError) as err:
        read_kahoot_xlsx(make_kahoot_xlsx(tmp_path / "blank.xlsx", []))
    assert err.value.code == "quiz_empty"

    (tmp_path / "quiz.csv").write_text("a,b", encoding="utf-8")
    for name, code in (("quiz.csv", "not_xlsx"), ("missing.xlsx", "not_found")):
        with pytest.raises(QuizError) as err:
            read_kahoot_xlsx(tmp_path / name)
        assert err.value.code == code

    (tmp_path / "broken.xlsx").write_bytes(b"not a zip")
    with pytest.raises(QuizError) as err:
        read_kahoot_xlsx(tmp_path / "broken.xlsx")
    assert err.value.code == "quiz_unreadable"


# ---- the items (the contract for the later steps) ----

def test_a_game_is_lobby_questions_podium_and_reads_back(template: Path) -> None:
    qs = read_kahoot_xlsx(template)
    items = build_items("Planets", qs)
    assert [it.type for it in items] == ["quiz_lobby"] + ["quiz"] * len(qs) + ["quiz_podium"]
    assert items[0].options == {"title": "Planets"}
    assert items[2].options == {"answer_1": "Red", "answer_2": "Yellow", "answer_3": "Blue", "answer_4": "Brown",
                                "correct": "1,3", "time_limit": 30, "points": "standard"}
    # Through session.yaml and back, each quiz item is the question it came from.
    session = parse_session(dump_session(Session(sections=[{"name": "Quiz", "items": items}])))
    back = [question_from_item(it) for it in session.all_items() if it.type == "quiz"]
    assert back == qs


def test_a_quiz_item_edited_by_hand_is_checked(template: Path) -> None:
    item = {"id": "act-q1", "question": "Q?", "options": {"answer_1": "a", "answer_2": "b", "correct": "2"}}
    q = question_from_item(item)
    assert (q.correct, q.time_limit, q.points) == ([2], 20, "standard")  # the editor's defaults
    item["options"]["points"] = "triple"
    with pytest.raises(QuizError) as err:
        question_from_item(item)
    assert err.value.code == "quiz_item_invalid" and err.value.detail == {"item_id": "act-q1", "reason": "bad_points"}


def test_the_editor_offers_exactly_the_formats_choices() -> None:
    quiz = json.loads((ACTIVITIES_DIR / "quiz" / "editor.json").read_text(encoding="utf-8"))
    opts = {o["key"]: o for o in quiz["options"]}
    assert list(opts) == ["answer_1", "answer_2", "answer_3", "answer_4", "correct", "time_limit", "points"]
    assert tuple(v for v, _ in opts["time_limit"]["choices"]) == TIME_LIMITS and opts["time_limit"]["default"] == 20
    assert tuple(v for v, _ in opts["points"]["choices"]) == POINTS and opts["points"]["default"] == "standard"
    lobby = json.loads((ACTIVITIES_DIR / "quiz_lobby" / "editor.json").read_text(encoding="utf-8"))
    assert quiz["capture"] is False  # answers go to the game engine (#51), not a chat capture window
    assert lobby["capture"] is False and [o["key"] for o in lobby["options"]] == ["title", "accept_chat"]
    podium = json.loads((ACTIVITIES_DIR / "quiz_podium" / "editor.json").read_text(encoding="utf-8"))
    assert podium["capture"] is False


def test_the_lobby_is_titled_with_the_quiz_name(template: Path) -> None:
    session = Session(sections=[{"name": "S", "items": build_items("Planets", read_kahoot_xlsx(template))}])
    run = build_run(parse_session(dump_session(session)), None)["items"]
    assert [r["title"] for r in run][:2] == ["Planets", QUESTIONS[0][0]]
    assert (run[0]["capture"], run[1]["capture"], run[-1]["capture"]) == (False, False, False)  # the engine owns answers (#51)


def test_import_into_an_existing_section_or_an_unknown_one(template: Path) -> None:
    session = parse_session({"sections": [{"name": "Warm-up", "items": [{"kind": "break"}]}]})
    sec_id = session.sections[0].id
    res = import_kahoot(session, template, "Planets", sec_id)
    assert res["section_id"] == sec_id and len(session.sections) == 1
    assert len(session.sections[0].items) == 1 + len(QUESTIONS) + 2
    before = dump_session(session)
    with pytest.raises(QuizError) as err:
        import_kahoot(session, template, "Planets", "sec-nope")
    assert err.value.code == "section_not_found" and dump_session(session) == before


# ---- the API ----

def _session(client) -> tuple[str, Path]:
    created = client.post("/api/sessions", json={"title": "Quiz night", "workshop": "w", "folder": "quiz"}).json()
    return created["id"], Path(created["path"])


def test_the_import_adds_a_saved_section(client, template: Path) -> None:
    sid, _ = _session(client)
    res = client.post(f"/api/sessions/{sid}/quiz-import", json={"path": f'"{template}"', "title": ""})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["section"], body["questions"], body["items"]) == ("Planets quiz", 5, 7)  # the file's name
    saved = client.get(f"/api/sessions/{sid}").json()["session"]
    sec = saved["sections"][-1]
    assert sec["id"] == body["section_id"] and sec["name"] == "Planets quiz"
    assert sec["minutes"] == 9  # 360 s of time limits + 30 s each = 510 s, rounded up
    types = [it["type"] for it in sec["items"]]
    assert types == ["quiz_lobby", "quiz", "quiz", "quiz", "quiz", "quiz", "quiz_podium"]
    assert sec["items"][0]["id"] == body["lobby_id"] and sec["items"][0]["options"]["title"] == "Planets quiz"
    assert [it["options"]["correct"] for it in sec["items"][1:-1]] == ["2", "1,3", "1", "1", "1,3,4"]
    assert [it["options"]["time_limit"] for it in sec["items"][1:-1]] == [20, 30, 10, 60, 240]


def test_a_refused_file_saves_nothing(client, tmp_path: Path) -> None:
    sid, folder = _session(client)
    before = (folder / "session.yaml").read_text(encoding="utf-8")
    bad = make_kahoot_xlsx(tmp_path / "bad.xlsx", [QUESTIONS[0], QUESTIONS[1], ["Q?", "a", "b", None, None, 20, 4]])
    res = client.post(f"/api/sessions/{sid}/quiz-import", json={"path": str(bad), "title": "Bad"})
    assert res.status_code == 422
    assert res.json()["error"] == {"code": "quiz_row_invalid", "message": "Row 11: the correct answer 4 points at an empty answer",
                                   "detail": {"row": 11, "reason": "correct_empty_answer"}}
    assert (folder / "session.yaml").read_text(encoding="utf-8") == before


def test_the_three_types_are_in_the_activity_list(client) -> None:
    types = {t["type"]: t for t in client.get("/api/activities").json()["types"]}
    assert {"quiz_lobby", "quiz", "quiz_podium"} <= set(types)
    assert all(types[t]["has_stage"] for t in ("quiz_lobby", "quiz", "quiz_podium"))
