"""Integration tests for the FastAPI app: HTTP routes + WS protocol.

We use httpx.AsyncClient (via ASGI transport) for HTTP routes and a
mocked WebSocket for the /twilio endpoint. The VoiceService and
DeepgramClient are stubbed so no external services are hit.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_mock
from fastapi import FastAPI
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from ghostline.audio.ambient import AmbientNoise
from ghostline.persistence.repository import CallRecord, MessageRecord, Repository
from ghostline.playbook import Playbook
from ghostline.server.fastapi import ServerDeps, create_app
from ghostline.tts.elevenlabs import VoiceService


@pytest.fixture
def playbook() -> Playbook:
    return Playbook.from_string(
        """
meta: {name: Integration Test}
defaults: {persona: professional, silent_until: 999}
sequence:
  - stage: RAPPORT
    custom_prompt: "Hello from integration test."
  - stage: CLOSE
    success_regex: "\\\\bpassword\\\\b"
    goto_on_success: REPORTING
  - stage: REPORTING
"""
    )


@pytest.fixture
def ambient(tmp_path: object) -> AmbientNoise:
    return AmbientNoise(path=tmp_path / "nope.wav")  # type: ignore[arg-type]


@pytest.fixture
def voice_service(mocker: pytest_mock.MockerFixture) -> VoiceService:
    svc = mocker.MagicMock(spec=VoiceService)
    # Synth returns a deterministic PCM buffer
    svc.synth = AsyncMock(return_value=b"\x00\x01" * 800)
    svc.aclose = AsyncMock()
    return svc  # type: ignore[return-value]


@pytest.fixture
async def repo() -> Repository:
    r = await Repository.open(":memory:")
    try:
        yield r
    finally:
        await r.close()


@pytest.fixture
def deps(
    repo: Repository,
    voice_service: VoiceService,
    ambient: AmbientNoise,
    playbook: Playbook,
) -> ServerDeps:
    return ServerDeps(
        repository=repo,
        voice_service=voice_service,
        ambient=ambient,
        deepgram_api_key="test_key",
        deepgram_language="en-US",
        deepgram_model="nova-2",
        playbook=playbook,
        voice_id="test_voice_id",
        ngrok_ws_url="wss://tunnel.example.com/twilio",
    )


@pytest.fixture
def app(deps: ServerDeps) -> FastAPI:
    return create_app(deps)


@pytest.fixture
async def client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestDashboardRoute:
    async def test_root_returns_html(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "GhostLine Dashboard" in resp.text
        assert "Integration Test" in resp.text  # playbook name

    async def test_dashboard_shows_stats_after_data(
        self,
        client: httpx.AsyncClient,
        repo: Repository,
    ) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_int_1"))
        await repo.insert_message(
            MessageRecord(
                call_sid="CA_int_1",
                role="user",
                content="hi",
                sales_stage="RAPPORT",
            )
        )
        resp = await client.get("/")
        assert "RAPPORT" in resp.text


class TestStatsRoute:
    async def test_returns_json(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "call_count" in data
        assert "stage_counts" in data
        assert data["playbook"] == "Integration Test"

    async def test_reflects_inserted_data(
        self,
        client: httpx.AsyncClient,
        repo: Repository,
    ) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_stats_1"))
        resp = await client.get("/api/stats")
        assert resp.json()["call_count"] == 1


class TestVoiceRoute:
    async def test_returns_twiml(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/voice")
        assert resp.status_code == 200
        assert "<?xml" in resp.text
        assert "wss://tunnel.example.com/twilio" in resp.text
        assert "<Stream" in resp.text

    async def test_appends_query_params_to_url(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/voice?campaign=demo&persona=urgent")
        assert resp.status_code == 200
        assert "campaign=demo" in resp.text
        assert "persona=urgent" in resp.text
        # & must be XML-escaped in the TwiML
        assert "&amp;" in resp.text

    async def test_returns_500_when_no_tunnel(self, app: FastAPI, deps: ServerDeps) -> None:
        deps.ngrok_ws_url = None
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/voice")
        assert resp.status_code == 500


class TestTwilioWebsocket:
    """Test the /twilio WS endpoint with a simulated Twilio Media Stream."""

    async def test_ws_sends_greeting_after_start(
        self,
        app: FastAPI,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        # Mock DeepgramClient so it doesn't hit the network.
        mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.connect",
            new=AsyncMock(),
        )
        mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.close",
            new=AsyncMock(),
        )
        mocker.patch(
            "ghostline.server.fastapi.DeepgramClient.send",
            new=AsyncMock(),
        )
        transport = ASGIWebSocketTransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
            aconnect_ws("/twilio?campaign=test&persona=urgent", client) as ws,
        ):
            # Send a start event
            start_msg = {
                "event": "start",
                "start": {
                    "streamSid": "ZX_test",
                    "callSid": "CA_ws_test_1",
                    "customParameters": {"from": "+15551234567"},
                },
            }
            await ws.send_text(json.dumps(start_msg))
            # The server should send greeting audio frames
            msg = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            data = json.loads(msg)
            assert data["event"] == "media"
            assert data["streamSid"] == "ZX_test"
            # Now send a stop event to cleanly close
            await ws.send_text(json.dumps({"event": "stop"}))
