"""Throwaway webapp for the design-review walk's synthetic run (fleet-config#1383).

``/design-review facilitation-suite --synthetic`` (and ``measure --e2e``) runs this
(``[design.review.synthetic]`` in ``.fleet.toml``) with the repo's venv python, from the
repo root. It boots the same disposable instance the e2e suite does (``boot_instance`` in
``tests/e2e/conftest.py``: temp config, ledger, data dir and an empty ``.env``, free ports),
prints ``URL=<base>`` once ``/healthz`` answers, then waits until stdin closes and tears
the instance down.

Never points at the live tray on :8449.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.e2e.conftest import boot_instance, stop_instance  # noqa: E402

logger = logging.getLogger("design_review_synthetic")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    root = Path(tempfile.mkdtemp(prefix="fs-synthetic-"))
    try:
        proc, inst = boot_instance(root)
    except RuntimeError as exc:
        logger.error("❌ synthetic webapp did not start: %s", exc)
        shutil.rmtree(root, ignore_errors=True)
        return 1
    try:
        logger.info("✅ synthetic webapp up on %s (data %s)", inst.base_url, root)
        print(f"URL={inst.base_url}", flush=True)
        # The walk closes our stdin when it is done; reading to EOF blocks until then.
        sys.stdin.read()
        return 0
    finally:
        stop_instance(proc)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
