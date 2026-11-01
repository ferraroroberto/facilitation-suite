"""The local LLM hub (``copilot.hub_url``): chunk transcription and hint completions over plain HTTP.

The hub owns where whisper and each model run; this app only speaks its OpenAI-shaped endpoints.
Every call is bounded by its timeout and fails with one of three errors the presenter tells apart:
``HubDown`` (nothing answers at the URL), ``HubTimeout`` (no answer in time) and ``HubError`` (an
answer that is an error, or not JSON).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from typing import Any


class HubFailure(Exception):
    """A hub call that gave no usable answer."""


class HubDown(HubFailure):
    """Nothing answers at the hub's URL."""


class HubTimeout(HubFailure):
    """The hub answered nothing within the timeout."""


class HubError(HubFailure):
    """The hub answered with an error, or with something that is not the JSON asked for."""


def _post(url: str, body: bytes, content_type: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — the configured local hub
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        raise HubError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise HubTimeout(f"no answer from {url} within {timeout:.0f} s") from exc
        raise HubDown(f"{url} is not answering ({exc.reason})") from exc
    except TimeoutError as exc:
        raise HubTimeout(f"no answer from {url} within {timeout:.0f} s") from exc
    except ConnectionError as exc:
        raise HubDown(f"{url} dropped the connection ({exc})") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HubError(f"{url} answered something that is not JSON") from exc
    if not isinstance(data, dict):
        raise HubError(f"{url} answered JSON that is not an object")
    return data


def transcribe(hub_url: str, wav: bytes, *, model: str, language: str, timeout: float) -> dict[str, Any]:
    """``POST /v1/audio/transcriptions`` (``verbose_json``): the hub's ``{"text", "segments", ...}``."""
    boundary = uuid.uuid4().hex
    fields = {"model": model, "language": language, "response_format": "verbose_json"}
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items() if v]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="chunk.wav"\r\n'
                 "Content-Type: audio/wav\r\n\r\n".encode())
    body = b"".join(parts) + wav + f"\r\n--{boundary}--\r\n".encode()
    return _post(hub_url.rstrip("/") + "/v1/audio/transcriptions", body, f"multipart/form-data; boundary={boundary}", timeout)


def complete(hub_url: str, model: str, messages: list[dict[str, str]], *, timeout: float, max_tokens: int = 400) -> str:
    """``POST /v1/chat/completions``: the first choice's text."""
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}).encode()
    data = _post(hub_url.rstrip("/") + "/v1/chat/completions", body, "application/json", timeout)
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise HubError(f"{model}: the answer has no choices[0].message.content") from exc
