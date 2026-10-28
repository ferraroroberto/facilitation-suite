"""The one JSON error envelope: ``{"error": {"code", "message", "detail"?}}``."""

from __future__ import annotations

import functools
import ipaddress
import socket
from typing import Any, Optional
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from src.certs import cert_hostname
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


@functools.lru_cache(maxsize=1)
def this_pc_names() -> frozenset[str]:
    """The host names this PC answers to: ``localhost``, its own name and the
    tailnet name on its certificate. Read once: the certificate's name only
    changes with a new certificate, which needs a restart to be served anyway."""
    names = {"localhost", socket.gethostname(), cert_hostname() or ""}
    return frozenset(n.lower().rstrip(".") for n in names if n)


def names_this_pc(host: str) -> bool:
    """A ``Host`` header that names this PC: an IP address or one of :func:`this_pc_names`."""
    name = (urlsplit("//" + host).hostname or "").rstrip(".")
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name in this_pc_names()


def host_header(scope: Any) -> Optional[str]:
    """The ``Host`` the caller asked for, ``None`` when it sent none."""
    for key, value in scope.get("headers") or []:
        if key.lower() == b"host":
            return value.decode("latin-1")
    return None


def is_local(scope: Any) -> bool:
    """The caller is this PC: loopback, or a connection whose two ends are the
    same address (this PC opening its own tailnet name — the address it was
    reached on is the address it came from). A phone never shares the PC's IP.

    The connection alone is not enough: the ``Host`` it asked for must name this
    PC too. A request that reached this PC under some other name was not made
    by this app's own pages, so it goes through the token gate like any device."""
    client = scope.get("client") or ("", 0)
    server = scope.get("server") or ("", 0)
    if not (client[0] in LOOPBACK_HOSTS or (bool(client[0]) and client[0] == server[0])):
        return False
    host = host_header(scope)
    return host is None or names_this_pc(host)


def require_local(request: Any, message: str) -> None:
    """403 ``local_only`` unless the caller is on this PC."""
    if not is_local(request.scope):
        raise AppError(403, "local_only", message)
