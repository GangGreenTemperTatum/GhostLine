"""Live end-to-end smoke test: exercises the real PSTN/STT/TTS/LLM stack.

Marked ``@pytest.mark.live`` so it's skipped by default. Run with::

    uv run pytest -m live

Requires real credentials in ``.env`` and a Twilio number configured to
point at the ngrok tunnel started by ``ghostline serve``.

This test is intentionally minimal: it verifies that the CLI commands can
be constructed and that the FastAPI app boots. A full live call requires
manual setup (clone a voice, start serve, place a call from a real phone).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ghostline.app import SalesAutomationApp
from ghostline.persistence.repository import CallRecord, Repository
from ghostline.playbook import Playbook
from ghostline.settings import get_settings

pytestmark = pytest.mark.live

# Compute playbook path at module level (avoids ASYNC240 in async test bodies).
_PLAYBOOK_PATH = Path(__file__).resolve().parents[2] / "playbooks" / "it-test.yaml"


class TestLiveSmoke:
    """Boots the real stack and verifies it doesn't crash on startup."""

    async def test_settings_load_from_env(self) -> None:
        """Settings must load from a real .env file."""
        s = get_settings()
        assert s.twilio_account_sid.startswith("AC")
        assert s.deepgram_api_key.get_secret_value()
        assert s.elevenlabs_api_key.get_secret_value()

    async def test_repository_opens_real_db(self, tmp_path: Path) -> None:
        """The async sqlite repo must open a real file."""
        db = tmp_path / "live_test.db"
        async with Repository.open(db) as repo:
            await repo.insert_call(CallRecord(call_sid="CA_live_smoke"))
            assert await repo.call_count() == 1
        assert await asyncio.to_thread(db.exists)

    async def test_bundled_playbook_loads(self) -> None:
        """At least one bundled playbook must load from the playbooks/ dir."""
        if not await asyncio.to_thread(_PLAYBOOK_PATH.exists):
            pytest.skip("bundled playbook not present")
        pb = await asyncio.to_thread(Playbook.from_file, _PLAYBOOK_PATH)
        assert pb.first_stage.name == "RAPPORT"

    async def test_app_constructs_with_real_settings(self) -> None:
        """The composition root must construct from real Settings."""
        settings = get_settings()
        pb = None
        if await asyncio.to_thread(_PLAYBOOK_PATH.exists):
            pb = await asyncio.to_thread(Playbook.from_file, _PLAYBOOK_PATH)
        app_instance = SalesAutomationApp(settings=settings, playbook=pb)
        assert app_instance.voice_service is not None
        assert app_instance.ambient is not None
        await app_instance.aclose()
