"""Tests for the Twilio Media Stream protocol."""

from __future__ import annotations

import base64
import json

import pytest

from ghostline.telephony.media_stream import (
    CHUNK_SIZE,
    MediaFrame,
    StartEvent,
    encode_media_frame,
    parse_event,
    send_audio_chunk,
)


class FakeSink:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send_text(self, data: str) -> None:
        if self.closed:
            raise ConnectionError("socket closed")
        self.sent.append(data)


class TestParseStartEvent:
    def test_parses_minimal_start(self) -> None:
        raw = json.dumps(
            {
                "event": "start",
                "start": {
                    "streamSid": "ZX1",
                    "callSid": "CA_abc",
                },
            }
        )
        ev = parse_event(raw)
        assert isinstance(ev, StartEvent)
        assert ev.stream_sid == "ZX1"
        assert ev.call_sid == "CA_abc"
        assert ev.caller_number is None
        assert ev.custom_params == {}

    def test_parses_custom_params_and_caller(self) -> None:
        raw = json.dumps(
            {
                "event": "start",
                "start": {
                    "streamSid": "ZX1",
                    "callSid": "CA_abc",
                    "customParameters": {"from": "+15551234567", "campaign": "demo"},
                },
            }
        )
        ev = parse_event(raw)
        assert isinstance(ev, StartEvent)
        assert ev.caller_number == "+15551234567"
        assert ev.custom_params == {"from": "+15551234567", "campaign": "demo"}

    def test_falls_back_to_start_from_when_no_custom_from(self) -> None:
        raw = json.dumps(
            {
                "event": "start",
                "start": {"streamSid": "ZX1", "callSid": "CA_abc", "from": "+15559990000"},
            }
        )
        ev = parse_event(raw)
        assert isinstance(ev, StartEvent)
        assert ev.caller_number == "+15559990000"


class TestParseMediaEvent:
    def test_decodes_base64_payload(self) -> None:
        original = b"\x00\x01\x02\x03\xff"
        raw = json.dumps(
            {
                "event": "media",
                "media": {"payload": base64.b64encode(original).decode("ascii")},
            }
        )
        ev = parse_event(raw)
        assert isinstance(ev, MediaFrame)
        assert ev.payload == original
        assert ev.track is None

    def test_preserves_track_field(self) -> None:
        raw = json.dumps(
            {
                "event": "media",
                "media": {"payload": base64.b64encode(b"x").decode(), "track": "outbound"},
            }
        )
        ev = parse_event(raw)
        assert isinstance(ev, MediaFrame)
        assert ev.track == "outbound"


class TestParseOtherEvents:
    @pytest.mark.parametrize(
        "event_name",
        ["connected", "stop"],
    )
    def test_returns_event_name_string(self, event_name: str) -> None:
        raw = json.dumps({"event": event_name})
        ev = parse_event(raw)
        assert ev == event_name

    def test_unknown_event_returns_name(self) -> None:
        raw = json.dumps({"event": "mark"})
        assert parse_event(raw) == "mark"

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            parse_event("not json")


class TestEncodeMediaFrame:
    def test_produces_valid_json(self) -> None:
        frame = encode_media_frame("ZX1", b"\x00\x01\x02")
        parsed = json.loads(frame)
        assert parsed["event"] == "media"
        assert parsed["streamSid"] == "ZX1"
        assert parsed["media"]["payload"] == base64.b64encode(b"\x00\x01\x02").decode("ascii")

    def test_empty_chunk_produces_empty_payload(self) -> None:
        frame = encode_media_frame("ZX1", b"")
        parsed = json.loads(frame)
        assert parsed["media"]["payload"] == ""


class TestSendAudioChunk:
    async def test_splits_into_chunk_size_frames(self) -> None:
        sink = FakeSink()
        # 3 chunks worth of audio
        ulaw = b"\x00" * (CHUNK_SIZE * 3)
        n = await send_audio_chunk(sink, "ZX1", ulaw)
        assert n == 3
        assert len(sink.sent) == 3

    async def test_partial_final_chunk(self) -> None:
        sink = FakeSink()
        ulaw = b"\x00" * (CHUNK_SIZE + 50)
        n = await send_audio_chunk(sink, "ZX1", ulaw)
        assert n == 2
        assert len(sink.sent) == 2

    async def test_empty_audio_sends_nothing(self) -> None:
        sink = FakeSink()
        n = await send_audio_chunk(sink, "ZX1", b"")
        assert n == 0
        assert sink.sent == []

    async def test_custom_chunk_size(self) -> None:
        sink = FakeSink()
        ulaw = b"\x00" * 100
        n = await send_audio_chunk(sink, "ZX1", ulaw, chunk_size=30)
        assert n == 4  # 30 + 30 + 30 + 10

    async def test_stops_on_send_error(self) -> None:
        sink = FakeSink()
        sink.closed = True
        with pytest.raises(ConnectionError):
            await send_audio_chunk(sink, "ZX1", b"\x00" * 320)
