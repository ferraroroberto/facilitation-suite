"""The quiz on the main app (#52): the join QR code for the stage and the presenter.

Behind ``RemoteAuth`` like every other ``/api/*`` route — the public player
app (``app/player/``) never serves it.

    GET /api/quiz/qr.svg[?pin=]  → the QR of ``quiz.public_url`` + ``/play?pin=…``
                                   for the game on stage (or the game with ``pin``)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response

from src.quiz.model import QuizError
from src.quiz.qr import not_configured_svg, qr_svg

router = APIRouter(prefix="/api/quiz")

QR_STATE_HEADER = "X-Quiz-QR"  # "ok" | "not-configured" (the placeholder while quiz.public_url is empty)


@router.get("/qr.svg", include_in_schema=False)
async def join_qr(request: Request, pin: Optional[str] = None) -> Response:
    """The join QR (async: the quiz engine runs on the live hub's loop)."""
    quiz = request.app.state.quiz
    game = quiz.game_by_pin(pin) if pin else quiz.game_on_stage()
    if game is None:
        raise QuizError(404, "no_game", "No quiz game on stage" if not pin else "No game with that PIN")
    url = quiz.join_url(game.pin)
    svg, state = (qr_svg(url), "ok") if url else (not_configured_svg(), "not-configured")
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store", QR_STATE_HEADER: state})
