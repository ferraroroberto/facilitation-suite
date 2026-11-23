"""Named thread pools for the server's blocking work.

The event loop's default executor is one shared pool, and FastAPI runs every
plain ``def`` handler on it too. A results print that takes half a minute, a
Spotify call that waits on the network or a reader process that is slow to
stop would each hold one of its threads, and every other request that needs
that pool queues behind them, the presenter's live views included.

So each kind of blocking work gets its own bounded pool, and a stall fills only
the pool of its kind:

* :data:`PROCESSES`: starting and stopping the helper processes (the chat
  reader, the copilot overlay, the Spotify login) and closing the music player;
* :data:`SETTINGS_WRITES`: writes to ``config.json``, one at a time so two
  Settings saves never interleave their read-modify-write;
* :data:`SESSION_WORK`: a session's heavy local work (printing the results
  PDFs, the wrap-up's rebuild, workbook and analysis, a group shuffle, a place
  lookup in the gazetteer);
* :data:`OUTBOUND`: calls that wait on the network (the Spotify account, the
  quiz link's public reach check).

The pools are :class:`TimedExecutor`, so a request's ``Server-Timing`` names
the pool its time went to. A pool's threads start on first use.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from typing import Any

from src.request_timing import TimedExecutor

# Sizes cover the parallel calls of that kind one person can cause at once, plus
# one stalled call, while a stuck kind never holds an unbounded number of threads.
PROCESSES = TimedExecutor("process-control", max_workers=4)
SETTINGS_WRITES = TimedExecutor("settings-writes", max_workers=1)
SESSION_WORK = TimedExecutor("session-work", max_workers=3)
OUTBOUND = TimedExecutor("outbound", max_workers=4)


async def run_on[T](pool: TimedExecutor, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """``fn(*args, **kwargs)`` on ``pool``, with the caller's context, as ``asyncio.to_thread`` does."""
    loop = asyncio.get_running_loop()
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await loop.run_in_executor(pool, call)
