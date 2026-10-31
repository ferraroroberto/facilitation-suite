"""The quiz tests' shared fixtures: the demo session with a quiz section, a fake clock and a rig on both."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.live.hub import LiveHub
from src.quiz.service import QuizService
from src.sessions.store import SESSION_FILE
from tests.fixtures.demo import build_demo_session
from tests.fixtures.quiz_rig import QUIZ_SECTION, Clock, make_rig


@pytest.fixture
def quiz_session(isolated_env: Path) -> tuple[str, Path]:
    sid, folder = build_demo_session(isolated_env / "sessions" / "demo" / "quiz", isolated_env / "sessions.local.yaml")
    raw = yaml.safe_load((folder / SESSION_FILE).read_text(encoding="utf-8"))
    raw["sections"].insert(1, QUIZ_SECTION)
    (folder / SESSION_FILE).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return sid, folder


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def rig(quiz_session: tuple[str, Path], clock: Clock) -> tuple[LiveHub, QuizService]:
    return make_rig(quiz_session[0], clock)
