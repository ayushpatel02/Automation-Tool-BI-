"""In-process per-session event bus for streaming generation progress over SSE.

A simple asyncio.Queue per session id. Suitable for a single-process deployment; for a
multi-worker deployment this would be swapped for Redis pub/sub (the interface stays the
same, so callers do not change).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

_queues: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
_SENTINEL = {"stage": "__end__"}


async def publish(session_id: str, event: dict) -> None:
    await _queues[session_id].put(event)


async def close(session_id: str) -> None:
    await _queues[session_id].put(_SENTINEL)


async def subscribe(session_id: str):
    """Async generator yielding events until the stream is closed."""
    queue = _queues[session_id]
    while True:
        event = await queue.get()
        if event is _SENTINEL or event == _SENTINEL:
            break
        yield event
    _queues.pop(session_id, None)


def make_progress_cb(session_id: str):
    async def _cb(event: dict) -> None:
        await publish(session_id, event)

    return _cb
