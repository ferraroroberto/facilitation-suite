"""The Spotify login's state, declared once: idle, waiting, done or failed.

``Login.state`` holds one ``LoginState`` and moves only through ``advance``, which refuses a move
the table below does not list. Settings → Music reads the snapshot's derived flags (``waiting``,
``done``, ``failed``) instead of spelling the states (``state`` itself is still sent). State table
and readers: ``docs/music.md``.
"""

from __future__ import annotations

from enum import StrEnum


class LoginState(StrEnum):
    """The closed set of states. Adding one means handling it in ``TRANSITIONS`` and every reader."""

    IDLE = "idle"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"


MEANINGS: dict[LoginState, str] = {
    LoginState.IDLE: "no login started since the app began",
    LoginState.WAITING: "the browser is open on Spotify's consent page and the callback is listening",
    LoginState.DONE: "the token was exchanged and saved to .env",
    LoginState.FAILED: "the answer was refused, foreign, late or the exchange failed; the reason is in the detail",
}

#: Each state and the states it may move to. A login that ends is only ever replaced by a new
#: start, and a start while one is waiting replaces it, so ``waiting`` moves to itself.
TRANSITIONS: dict[LoginState, frozenset[LoginState]] = {
    LoginState.IDLE: frozenset({LoginState.WAITING}),
    LoginState.WAITING: frozenset({LoginState.WAITING, LoginState.DONE, LoginState.FAILED}),
    LoginState.DONE: frozenset({LoginState.WAITING}),
    LoginState.FAILED: frozenset({LoginState.WAITING}),
}


class IllegalLoginTransition(ValueError):
    """A move the table does not list (for example a login that is done failing afterwards)."""


def advance(current: LoginState, to: LoginState) -> LoginState:
    """``to`` when ``current`` may move there, otherwise ``IllegalLoginTransition``."""
    if to not in TRANSITIONS[current]:
        raise IllegalLoginTransition(f"spotify login cannot go from {current.value} to {to.value}")
    return to


def is_waiting(state: LoginState) -> bool:
    """True while the browser login is open."""
    return state is LoginState.WAITING


def is_done(state: LoginState) -> bool:
    """True once the token is saved."""
    return state is LoginState.DONE


def is_failed(state: LoginState) -> bool:
    """True once the login ended without a saved token."""
    return state is LoginState.FAILED
