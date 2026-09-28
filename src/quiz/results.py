"""A session's quiz results (#55), read back from ``live/quiz.jsonl``.

**One source of truth.** The file is replayed with ``service.replay`` (every
record through ``Game.apply``) against the plan's game scopes from
``service.scopes_of`` — the same two functions the live service runs after a
restart — and every score, rank, streak and distribution comes from the
engine (``Game.standings``, ``Game.distribution``, ``engine.points``). Nothing
here scores anything itself, so the Results tab, the Excel report and the
session PDF cannot disagree with what the stage showed.

**One entry per game.** A lobby run is a game (``<lobby id>-<run>``;
``quiz_new_game`` starts the next run), so a session can hold several; each
is labelled by its quiz title and run. A game nobody joined and that asked no
question (the stage only passed its lobby) is left out.

Each entry carries the final leaderboard (rank, nickname, score, correct,
answered, average response time, source ``phone``/``chat``), the podium, per
closed question its distribution with the correct answer(s), and the
player × question answer rows for the Excel report.

**Pages.** ``collect.timeline`` places a question's page at its first
``quiz_phase`` ``question`` event and the podium at the game's first
``podium`` event; ``place_pages`` adds any page the timeline could not place
(a game that never reached its podium still gets its final standings, right
after its last question).

Nothing here writes; a missing ``quiz.jsonl`` means no quiz results.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from src.activities.registry import options_with_defaults
from src.jsonl import read_jsonl
from src.quiz.engine import Game, points
from src.quiz.service import LOBBY, QUIZ_FILE, Scope, replay, scopes_of

logger = logging.getLogger(__name__)

PODIUM_PAGE = "podium"  # the podium page's key in ``page_keys`` (a question's key is its item id)


def quiz_title(lobby: Optional[dict[str, Any]]) -> str:
    """The quiz's name: the lobby's ``title`` option, else "Quiz"."""
    opts = options_with_defaults(LOBBY, (lobby or {}).get("options") or {})
    return " ".join(str(opts.get("title") or "").split()) or "Quiz"


def _game(game: Game, scope: Scope, lobby: Optional[dict[str, Any]], cap_dir: Path) -> dict[str, Any]:
    questions = scope.questions
    runs = game.closed_runs(questions)
    standings = game.standings(questions)
    players = game.active()
    title = quiz_title(lobby)
    by_id = {p.id: p for p in game.players.values()}

    board, rows = [], []
    for s in standings:
        mine = [(n, run, game.answers.get((s.player_id, run.item_id))) for n, run in enumerate(runs, start=1)]
        answered = [a for _, _, a in mine if a is not None]
        board.append({
            "rank": s.rank, "id": s.player_id, "name": s.name, "score": s.score, "correct": s.correct,
            "answered": len(answered), "streak": s.streak, "source": by_id[s.player_id].source,
            "avg_ms": round(sum(a.elapsed_ms for a in answered) / len(answered)) if answered else None,
        })
        for n, run, a in mine:
            q = questions[run.item_id]
            right = a is not None and a.choice in q.correct
            rows.append({
                "player_id": s.player_id, "name": s.name, "source": by_id[s.player_id].source, "number": n,
                "item_id": run.item_id, "question": q.question,
                "choice": a.choice if a else None, "choice_text": q.answers[a.choice - 1] if a else "",
                "correct": right if a else None, "points": points(q, right, a.elapsed_ms) if a else 0,
                "elapsed_ms": a.elapsed_ms if a else None,
            })

    asked = []
    for n, run in enumerate(runs, start=1):
        q = questions[run.item_id]
        dist = game.distribution(run.item_id)
        asked.append({
            "number": n, "item_id": run.item_id, "question": q.question, "time_limit": q.time_limit,
            "points": q.points, "correct": list(q.correct), "answered": game.answered(run.item_id),
            "players": len(players), "opened_ms": run.opened_at, "closed_ms": run.closed_at,
            "answers": [{"n": i, "text": text, "count": dist[i - 1], "correct": i in q.correct}
                        for i, text in enumerate(q.answers, start=1) if text],
            "has_png": (cap_dir / f"{run.item_id}.png").is_file(),
        })

    winner = board[0]["name"] if board else ""
    summary = f"{len(players)} player{'s' if len(players) != 1 else ''} · {len(runs)} question{'s' if len(runs) != 1 else ''}"
    return {
        "id": game.game_id, "lobby_id": game.lobby_id, "run": game.run, "title": title,
        "label": f"{title} · run {game.run}", "podium_id": scope.podium_id,
        "start_ms": runs[0].opened_at if runs else None, "end_ms": runs[-1].closed_at if runs else None,
        "summary": summary + (f" · winner {winner}" if winner else ""),
        "player_count": len(players), "question_count": len(runs), "planned_questions": len(scope.order),
        "leaderboard": board, "podium": board[:3], "questions": asked, "answer_rows": rows,
    }


def quiz_results(folder: Path, run_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every game played in the session, in the order they opened (``[]`` without a ``quiz.jsonl``)."""
    records = read_jsonl(folder / "live" / QUIZ_FILE)
    if not records:
        return []
    games = replay(records)
    scopes = {s.lobby_id: s for s in scopes_of(run_items).values()}
    items = {it["id"]: it for it in run_items}
    cap_dir = folder / "live" / "captures"
    out = []
    for game in games.values():
        scope = scopes.get(game.lobby_id)
        if scope is None:
            logger.warning("⚠️ results: quiz game %s — its lobby %s is no longer in the plan, left out",
                           game.game_id, game.lobby_id)
            continue
        entry = _game(game, scope, items.get(game.lobby_id), cap_dir)
        if entry["player_count"] or entry["question_count"]:
            out.append(entry)
    return out


def page_keys(quizzes: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """``(game id, question item id | "podium")`` for every quiz page ``collect.timeline`` may place."""
    keys = {(q["id"], PODIUM_PAGE) for q in quizzes}
    keys |= {(q["id"], x["item_id"]) for q in quizzes for x in q["questions"]}
    return keys


def place_pages(pages: list[dict[str, Any]], quizzes: list[dict[str, Any]]) -> None:
    """Add, in place, each quiz page the timeline had no event for: right after its game's last page
    (a game that never reached its podium still ends on its final standings), else at the end."""
    for q in quizzes:
        want = [("quiz_question", x["item_id"]) for x in q["questions"]] + [("quiz_podium", q["podium_id"] or q["lobby_id"])]
        have = {(p["kind"], p["item_id"]) for p in pages if p.get("game") == q["id"]}
        for kind, item_id in want:
            if (kind, item_id) in have:
                continue
            mine = [n for n, p in enumerate(pages) if p.get("game") == q["id"]]
            at = mine[-1] + 1 if mine else len(pages)
            pages.insert(at, {"kind": kind, "game": q["id"], "item_id": item_id, "at": ""})
