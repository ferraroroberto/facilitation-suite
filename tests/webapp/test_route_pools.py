"""A stalled outbound call fills only its own pool: an unrelated request still answers at once.

The quiz link's reach check below hangs on an injected event (no CPU burn), and
more of them are in flight than the outbound pool has threads, so that pool is
full and queueing. A Settings save runs on its own pool and must answer at once,
and so must a call on the loop's default executor, sized small here so checks
left on it would fill it.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx2 as httpx

from app.webapp import route_pools

_HANG_S = 6.0
_BOUND_S = 1.0
_DEFAULT_WORKERS = 2


class _Stall:
    """A call that blocks until released (or :data:`_HANG_S` passes), counting entries."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.entered = 0
        self.lock = threading.Lock()
        self.release = threading.Event()

    def __call__(self, *_args: Any, **_kwargs: Any) -> Any:
        with self.lock:
            self.entered += 1
        self.release.wait(_HANG_S)
        return self.answer


async def _until(check: Callable[[], bool], timeout: float = 3.0) -> None:
    """Wait for ``check``; on timeout, return and let the assertions say what went wrong."""
    end = time.monotonic() + timeout
    while not check() and time.monotonic() < end:
        await asyncio.sleep(0.01)


def test_a_stalled_reach_check_leaves_other_requests_free(isolated_env, monkeypatch):
    from app.webapp.server import create_app

    monkeypatch.setattr("src.features.enabled", lambda _cfg, _key: True)
    app = create_app()
    stall = _Stall({"state": "unknown"})
    app.state.quiz_reach.check_now = stall
    pool = route_pools.OUTBOUND
    stalls = pool._max_workers + 1

    async def run() -> tuple[int, float, float, int]:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=_DEFAULT_WORKERS))
        app.state.live.bind(loop)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            checks = [asyncio.create_task(client.post("/api/quiz/reach")) for _ in range(stalls)]
            try:
                await _until(lambda: stall.entered >= stalls - 1)
                await asyncio.sleep(0.1)  # time for the last check to queue, not to enter
                started = time.perf_counter()
                saved = await client.put("/api/settings/appearance", json={"appearance": "dark"})
                saved_s = time.perf_counter() - started
                started = time.perf_counter()
                await asyncio.to_thread(lambda: None)
                free_s = time.perf_counter() - started
                entered = stall.entered
            finally:
                stall.release.set()
                await asyncio.gather(*checks, return_exceptions=True)
        return saved.status_code, saved_s, free_s, entered

    status, saved_s, free_s, entered = asyncio.run(run())
    assert status == 200
    assert saved_s < _BOUND_S, f"a Settings save took {saved_s:.2f}s behind stalled reach checks"
    assert entered == stalls - 1, f"{entered} of {stalls} checks ran: {pool.pool_name} is not the pool they used"
    assert free_s < _BOUND_S, f"a default-executor call waited {free_s:.2f}s behind stalled reach checks"


def test_run_on_uses_the_named_pool_and_the_callers_context():
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="unset")

    async def run() -> tuple[str, str]:
        var.set("request")
        return await route_pools.run_on(route_pools.SESSION_WORK,
                                        lambda: (threading.current_thread().name, var.get()))

    thread, seen = asyncio.run(run())
    assert thread.startswith("session-work")
    assert seen == "request"
