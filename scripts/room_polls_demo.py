"""Create (or reset) the Room Polls demo session on a running app, then check the public link.

    python -m scripts.room_polls_demo [--base https://127.0.0.1:8449] [--no-reach]

The app writes the session itself, through its own API, into the session root
from Settings, so the demo lands where any new session would. Run it again to
reset the demo's plan; live data already in the folder stays. The plan is the
synthetic ``tests/fixtures/demo-room-polls/session.yaml``. The owner test script
that uses it is in ``docs/room-polls.md``.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

import yaml

DEMO_FILE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "demo-room-polls" / "session.yaml"
TITLE = "Room Polls demo"
TIMEOUT_S = 30  # the public-link check alone can take several seconds


def _call(base: str, method: str, path: str, body: Optional[dict[str, Any]] = None) -> tuple[int, Any]:
    """One JSON request to the app; the status and the decoded body (an error body too)."""
    # :8449 serves a tailnet certificate, which does not name 127.0.0.1: this only ever talks to our own app
    insecure = ssl.create_default_context()
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=insecure) as resp:  # noqa: S310 — our own app
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def merged_plan(current: dict[str, Any], demo: dict[str, Any]) -> dict[str, Any]:
    """The session as the app holds it (its look, theme, font) with the demo's title and plan."""
    return {**current, "title": demo["title"], "duration_minutes": demo["duration_minutes"],
            "language": demo.get("language", current.get("language", "en")), "sections": demo["sections"]}


def ensure_session(base: str) -> str:
    """The demo session's id: created when missing, found by its title when it already exists."""
    status, body = _call(base, "POST", "/api/sessions", {"title": TITLE, "workshop": "demo", "folder": "room-polls-demo"})
    if status == 201:
        return str(body["id"])
    if status != 409:
        raise RuntimeError(f"creating the demo session failed: HTTP {status} {body}")
    _, listing = _call(base, "GET", "/api/sessions")
    for row in listing.get("sessions") or []:
        if row.get("title") == TITLE:
            return str(row["id"])
    raise RuntimeError("the demo folder exists but is not in the session list: add it from the Sessions tab")


def load_demo(base: str) -> str:
    """Write the demo plan into the app's demo session; returns its id."""
    sid = ensure_session(base)
    status, body = _call(base, "GET", f"/api/sessions/{sid}")
    if status != 200:
        raise RuntimeError(f"reading the demo session failed: HTTP {status} {body}")
    demo = yaml.safe_load(DEMO_FILE.read_text(encoding="utf-8"))
    status, body = _call(base, "PUT", f"/api/sessions/{sid}", {"session": merged_plan(body["session"], demo)})
    if status != 200:
        raise RuntimeError(f"saving the demo plan failed: HTTP {status} {body}")
    return sid


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="https://127.0.0.1:8449", help="the running app")
    ap.add_argument("--no-reach", action="store_true", help="skip the public-link check")
    args = ap.parse_args(argv)
    try:
        sid = load_demo(args.base)
    except (OSError, RuntimeError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"SESSION={sid}")
    print(f"PRESENTER={args.base.rstrip('/')}/presenter?session={sid}")
    if args.no_reach:
        return 0
    status, reach = _call(args.base, "POST", "/api/quiz/reach")
    state = reach.get("state", "unknown") if status == 200 and isinstance(reach, dict) else "unknown"
    print(f"REACH={state}")
    return 0 if state == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
