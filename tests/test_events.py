"""Ang in-process broker na ginagamit ng kiosk WebSocket."""

from __future__ import annotations

import asyncio

from app.services.events import EventBroker


async def test_subscriber_receives_published_payload() -> None:
    broker = EventBroker()
    async with broker.subscribe("pairing:1") as queue:
        await broker.publish("pairing:1", {"event": "cards_issued", "cards": []})
        payload = await asyncio.wait_for(queue.get(), timeout=1)
    assert payload["event"] == "cards_issued"


async def test_topics_are_isolated() -> None:
    broker = EventBroker()
    async with broker.subscribe("pairing:1") as queue:
        await broker.publish("pairing:2", {"event": "cards_issued"})
        with_timeout = asyncio.wait_for(queue.get(), timeout=0.1)
        try:
            await with_timeout
        except TimeoutError:
            pass
        else:  # pragma: no cover
            raise AssertionError("hindi dapat nakarating ang event ng ibang topic")


async def test_unsubscribed_topic_is_cleaned_up() -> None:
    broker = EventBroker()
    async with broker.subscribe("pairing:1"):
        pass
    # Walang listener na natira, kaya walang publish na mag-e-error.
    await broker.publish("pairing:1", {"event": "cards_issued"})


async def test_slow_subscriber_does_not_block_publisher() -> None:
    broker = EventBroker()
    async with broker.subscribe("pairing:1") as queue:
        for index in range(64):  # higit sa queue maxsize
            await broker.publish("pairing:1", {"event": "ping", "seq": index})
        assert queue.qsize() > 0
