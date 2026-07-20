"""Tests for the Deepgram streaming client."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from ghostline.stt.deepgram import (
    DeepgramClient,
    TranscriptEvent,
    UtteranceEndEvent,
)


def make_results_payload(
    transcript: str = "hello world",
    *,
    speech_final: bool = True,
    is_final: bool = True,
    confidence: float = 0.95,
) -> dict[str, Any]:
    return {
        "type": "Results",
        "channel": {
            "alternatives": [
                {
                    "transcript": transcript,
                    "confidence": confidence,
                    "words": [{"word": "hello", "start": 0.0, "end": 0.3}],
                }
            ]
        },
        "speech_final": speech_final,
        "is_final": is_final,
    }


def make_utterance_end_payload(last_word_end: float = 1.5) -> dict[str, Any]:
    return {"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": last_word_end}


class FakeWebSocket:
    """A minimal stand-in for aiohttp.ClientWebSocketResponse."""

    def __init__(self) -> None:
        self.closed = False
        self._incoming: asyncio.Queue[aiohttp.WSMsgType | str] = asyncio.Queue()
        self.sent_bytes: list[bytes] = []
        self.sent_strs: list[str] = []
        self.pinged = 0

    async def ws_connect(self, *args: Any, **kwargs: Any) -> FakeWebSocket:
        return self

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def send_str(self, data: str) -> None:
        self.sent_strs.append(data)

    async def ping(self) -> None:
        self.pinged += 1

    async def receive(self) -> aiohttp.WSMsgType:
        item = await self._incoming.get()
        if isinstance(item, aiohttp.WSMsgType):
            return _MockMessage(item)
        return _MockMessage(aiohttp.WSMsgType.TEXT, data=item)

    async def close(self) -> None:
        self.closed = True

    def queue_text(self, data: str) -> None:
        self._incoming.put_nowait(data)

    def queue_close(self) -> None:
        self._incoming.put_nowait(aiohttp.WSMsgType.CLOSED)


class _MockMessage:
    def __init__(self, mtype: aiohttp.WSMsgType, *, data: Any = None) -> None:
        self.type = mtype
        self.data = data


@pytest.fixture
async def fake_ws(monkeypatch: pytest.MonkeyPatch) -> FakeWebSocket:
    ws = FakeWebSocket()

    # Patch the ClientSession.ws_connect method on the class
    async def fake_ws_connect(
        self: aiohttp.ClientSession, *args: Any, **kwargs: Any
    ) -> FakeWebSocket:
        return ws

    monkeypatch.setattr(aiohttp.ClientSession, "ws_connect", fake_ws_connect)
    # Prevent actual session from being created
    monkeypatch.setattr(aiohttp.ClientSession, "_request", AsyncMock(return_value=MagicMock()))
    return ws


class TestUrlConstruction:
    def test_default_url_contains_required_params(self) -> None:
        c = DeepgramClient("k")
        assert "encoding=mulaw" in c.url
        assert "sample_rate=8000" in c.url
        assert "model=nova-2" in c.url
        assert "language=en-US" in c.url
        assert "interim_results=true" in c.url
        assert "smart_format=true" in c.url

    def test_custom_params_propagate(self) -> None:
        c = DeepgramClient("k", language="fr-FR", model="nova-2-phonecall")
        assert "language=fr-FR" in c.url
        assert "model=nova-2-phonecall" in c.url


class TestEventHandling:
    async def test_results_emits_transcript_event(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        fake_ws.queue_text(json.dumps(make_results_payload("hello there")))
        events = []
        async for ev in c.events():
            events.append(ev)
            assert isinstance(ev, TranscriptEvent)
            assert ev.transcript == "hello there"
            assert ev.speech_final is True
            break
        await c.close()

    async def test_utterance_end_emits_event(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        fake_ws.queue_text(json.dumps(make_utterance_end_payload(2.5)))
        async for ev in c.events():
            assert isinstance(ev, UtteranceEndEvent)
            assert ev.last_word_end == 2.5
            break
        await c.close()

    async def test_non_final_results_ignored(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        payload = make_results_payload("interim", is_final=False)
        fake_ws.queue_text(json.dumps(payload))
        fake_ws.queue_text(json.dumps(make_results_payload("final")))
        async for ev in c.events():
            assert isinstance(ev, TranscriptEvent)
            assert ev.transcript == "final"
            break
        await c.close()

    async def test_empty_transcript_ignored(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        payload = make_results_payload("   ")
        fake_ws.queue_text(json.dumps(payload))
        fake_ws.queue_text(json.dumps(make_results_payload("real")))
        async for ev in c.events():
            assert ev.transcript == "real"
            break
        await c.close()

    async def test_invalid_json_ignored(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        fake_ws.queue_text("not json")
        fake_ws.queue_text(json.dumps(make_results_payload("after")))
        async for ev in c.events():
            assert ev.transcript == "after"
            break
        await c.close()


class TestSend:
    async def test_send_forwards_bytes(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        await c.send(b"\x00\x01\x02")
        assert fake_ws.sent_bytes == [b"\x00\x01\x02"]
        await c.close()

    async def test_keepalive_sends_json(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        await c.send_keepalive()
        assert fake_ws.sent_strs == [json.dumps({"type": "KeepAlive"})]
        await c.close()


class TestLifecycle:
    async def test_close_sets_closed_flag(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        await c.close()
        # After close, connect should refuse
        with pytest.raises(RuntimeError, match="closed"):
            await c.connect()

    async def test_connect_idempotent_when_already_open(self, fake_ws: FakeWebSocket) -> None:
        c = DeepgramClient("k", heartbeat_interval=999)
        await c.connect()
        # Second connect should not raise (tasks already running)
        await c.connect()
        await c.close()
