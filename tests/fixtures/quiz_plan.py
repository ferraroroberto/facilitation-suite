"""A synthetic quiz section for the player tests (#52): a lobby, two questions, a podium."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.sessions.store import SESSION_FILE

PLAYER_QUIZ: dict[str, Any] = {"id": "sec-play", "name": "Shapes quiz", "minutes": 5, "items": [
    {"kind": "activity", "id": "pq-lobby", "type": "quiz_lobby", "options": {"title": "Shapes"}},
    {"kind": "activity", "id": "pq-1", "type": "quiz", "question": "How many sides has a triangle?",
     "options": {"answer_1": "Two", "answer_2": "Three", "answer_3": "Four", "answer_4": "Five",
                 "correct": "2", "time_limit": 60, "points": "standard"}},
    {"kind": "activity", "id": "pq-2", "type": "quiz", "question": "Is a square a rectangle?",
     "options": {"answer_1": "Yes", "answer_2": "No", "correct": "1", "time_limit": 60}},
    {"kind": "activity", "id": "pq-podium", "type": "quiz_podium"},
]}

# Four answers at Kahoot's 75-character limit, with accents and "¿" (#84): the reveal must show every line.
LONG_ANSWERS: dict[str, str] = {
    "answer_1": "Compartir decisiones en público, con fechas, dueños y criterios explícitos.",
    "answer_2": "¿Revisar métricas de satisfacción cada trimestre y publicar los resultados?",
    "answer_3": "Organizar reuniones rápidas de alineación sin agenda ni acta posterior útil",
    "answer_4": "Esperar a que el líder técnico apruebe cada cambio pequeño antes de avanzar",
}


def add_quiz_section(folder: Path, section: dict[str, Any] = PLAYER_QUIZ, at: int = 1) -> None:
    """Insert ``section`` into the session's plan (``session.yaml``) at position ``at``."""
    path = folder / SESSION_FILE
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["sections"].insert(at, section)
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
