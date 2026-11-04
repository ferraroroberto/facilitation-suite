"""A stand-in for the local LLM hub: the two OpenAI-shaped endpoints the copilot calls.

``POST /v1/audio/transcriptions`` answers ``verbose_json`` with the next scripted text, whole answer
or error status (or a fixed sentence); ``POST /v1/chat/completions`` answers each model with its scripted hints JSON, and a
rolling-summary request with ``summary``. A model in
``failing`` gets an HTTP 500, ``delay_s`` holds every answer back (a slow hub). Every call is
recorded with the form fields or JSON body it carried. Synthetic data only.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_HINTS = {"hints": [{"type": "next_q", "text": "Ask which idea they would test first"},
                           {"type": "notice", "text": "Two people mentioned the budget twice"}]}


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # a client that gave up (a timeout test) leaves a broken pipe: expected, not news


class FakeHub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.transcripts: list[str | dict[str, Any] | int] = []
        self.answers: dict[str, Any] = {}  # model → the hints object (or raw text) it answers
        self.failing: set[str] = set()
        self.delay_s = 0.0
        self.summary = "They agreed on a change freeze one week before each release."
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802 — http.server's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if hub.delay_s:
                    time.sleep(hub.delay_s)
                if self.path == "/v1/audio/transcriptions":
                    fields = dict(re.findall(rb'name="(\w+)"\r\n\r\n([^\r]*)\r\n', body))
                    hub.calls.append({"path": self.path, **{k.decode(): v.decode() for k, v in fields.items()},
                                      "bytes": len(body)})
                    script = hub.transcripts.pop(0) if hub.transcripts else "We could test the cheapest idea first."
                    if isinstance(script, int):  # a scripted error status
                        self._json(script, {"error": {"message": "whisper failed"}})
                    elif isinstance(script, dict):  # a whole verbose_json answer, segments and all
                        self._json(200, script)
                    else:
                        self._json(200, {"text": script, "language": "english", "segments": []})
                elif self.path == "/v1/chat/completions":
                    req = json.loads(body)
                    hub.calls.append({"path": self.path, "model": req["model"], "messages": req["messages"]})
                    if req["model"] in hub.failing:
                        self._json(500, {"error": {"message": "backend down"}})
                        return
                    if hub.is_summary(hub.calls[-1]):
                        content = hub.summary
                    else:
                        answer = hub.answers.get(req["model"], DEFAULT_HINTS)
                        content = answer if isinstance(answer, str) else json.dumps(answer)
                    self._json(200, {"choices": [{"message": {"role": "assistant", "content": content}}]})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def _json(self, status: int, data: dict[str, Any]) -> None:
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def calls_to(self, path: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["path"] == path]

    @staticmethod
    def is_summary(call: dict[str, Any]) -> bool:
        """A rolling-summary request (its system message), not a hints one."""
        return "running summary" in call["messages"][0]["content"]

    def hint_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls_to("/v1/chat/completions") if not self.is_summary(c)]

    def summary_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls_to("/v1/chat/completions") if self.is_summary(c)]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
