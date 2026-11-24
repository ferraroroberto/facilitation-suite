"""A synthetic feedback form for the forms tests: one question of every type, in its own section."""

from __future__ import annotations

from typing import Any

FORM_QUESTIONS: list[dict[str, Any]] = [
    {"id": "q1", "type": "text", "text": "What should we keep?", "required": True},
    {"id": "q2", "type": "number", "text": "Hours a week you could spend", "min": 0, "max": 40, "unit": "h"},
    {"id": "q3", "type": "scale", "text": "How useful was it?", "low": "Not at all", "high": "Very"},
    {"id": "q4", "type": "stars", "text": "Rate the venue"},
    {"id": "q5", "type": "nps", "text": "Would you recommend it?", "required": True},
    {"id": "q6", "type": "choice", "text": "Which parts?", "choices": ["Talks", "Games", "Lunch"], "multiple": True},
]
FORM_SECTION: dict[str, Any] = {"id": "sec-feedback", "name": "Feedback", "minutes": 5, "items": [
    {"kind": "activity", "id": "act-form", "type": "form", "title": "Workshop feedback",
     "options": {"intro": "Two minutes, thank you!", "questions": FORM_QUESTIONS}},
]}
