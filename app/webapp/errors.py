"""The one JSON error envelope: ``{"error": {"code", "message", "detail"?}}``."""

from __future__ import annotations

from typing import Any, Optional

from starlette.responses import JSONResponse

from src.errors import DomainError


class AppError(DomainError):
    """An error raised by the webapp itself (a route's own check); the domain
    modules raise their own ``DomainError`` subclasses. Both render the same."""


def error_response(status: int, code: str, message: str, detail: Optional[Any] = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse({"error": body}, status_code=status)


LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def is_local(scope: Any) -> bool:
    """The caller is this PC: loopback, or a connection whose two ends are the
    same address (this PC opening its own tailnet name — the address it was
    reached on is the address it came from). A phone never shares the PC's IP."""
    client = scope.get("client") or ("", 0)
    server = scope.get("server") or ("", 0)
    return client[0] in LOOPBACK_HOSTS or (bool(client[0]) and client[0] == server[0])


def require_local(request: Any, message: str) -> None:
    """403 ``local_only`` unless the caller is on this PC."""
    if not is_local(request.scope):
        raise AppError(403, "local_only", message)
