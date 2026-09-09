"""In-process pub/sub para sa pairing at draw events.

Sapat ito sa single-worker na local dev. Sa multi-worker o multi-instance na
deployment, palitan ng Redis pub/sub — hindi nagsasalo ng memory ang mga worker.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

_QUEUE_MAXSIZE = 16


class EventBroker:
    def __init__(self) -> None:
        self._topics: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            listeners = list(self._topics.get(topic, ()))
        for queue in listeners:
            # Huwag i-block ang publisher kapag mabagal o patay na ang listener.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    @contextlib.asynccontextmanager
    async def subscribe(self, topic: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._lock:
            self._topics.setdefault(topic, set()).add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                listeners = self._topics.get(topic)
                if listeners is not None:
                    listeners.discard(queue)
                    if not listeners:
                        del self._topics[topic]


_broker = EventBroker()


def get_broker() -> EventBroker:
    return _broker
