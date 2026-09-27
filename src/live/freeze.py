"""Render a frozen capture to PNG: ``python -m src.live.freeze <url> <out.png>``.

Opens ``/stage?freeze=<item>`` in headless Chromium at the stage's own
1920×1080, waits until the page says it has drawn the frozen result (fonts
included), and writes the screenshot next to the capture's JSON. Runs as its
own process so the server never waits on a browser.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.config import data_dir
from src.logger import configure_logging

logger = logging.getLogger("freeze")


def render(url: str, out: Path, timeout_ms: int = 20000) -> bool:
    from playwright.sync_api import Error, sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png.tmp")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # Our own server on loopback: under HTTPS its cert names the tailnet host, not 127.0.0.1.
            page = browser.new_page(viewport={"width": 1920, "height": 1080}, ignore_https_errors=True)
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            page.wait_for_selector("body[data-ready='1']", timeout=timeout_ms)
            page.screenshot(path=str(tmp), type="png", animations="disabled")
            browser.close()
        tmp.replace(out)
    except (Error, OSError) as exc:
        logger.error("❌ freeze PNG failed for %s: %s", out.name, exc)
        tmp.unlink(missing_ok=True)
        return False
    logger.info("✅ frozen %s", out.name)
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python -m src.live.freeze <url> <out.png>", file=sys.stderr)
        return 1
    configure_logging(log_file=data_dir() / "logs" / "facilitation-suite.log")
    return 0 if render(argv[1], Path(argv[2])) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
