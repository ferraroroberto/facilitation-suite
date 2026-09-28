"""The one domain-error shape: an HTTP status, a stable machine code and a message.

Every domain module's error (``SessionError``, ``ReviewError``, ``LiveError``,
``RosterError``, ``SettingsError``) subclasses it, and the webapp turns any of
them into its JSON error envelope with one exception handler
(``app/webapp/server.py``), so routers let them propagate instead of
translating each one.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """A domain error with its HTTP status, a stable machine code and optional detail."""

    def __init__(self, status: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail
