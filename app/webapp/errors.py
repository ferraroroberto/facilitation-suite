"""The one JSON error envelope: ``{"error": {"code", "message", "detail"?}}``."""

from __future__ import annotations

from typing import Any, Optional

from starlette.responses import JSONResponse


class AppError(Exception):
    """A domain error with its HTTP status and a stable machine code."""

    def __init__(self, status: int, code: str, message: str, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.detail = detail


def error_response(status: int, code: str, message: str, detail: Optional[Any] = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse({"error": body}, status_code=status)
