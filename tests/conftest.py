"""Shared pytest fixtures for GhostLine tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ghostline.persistence.repository import Repository
from ghostline.settings import clear_settings_cache

# Valid baseline env vars for tests that construct Settings.
TEST_ENV: dict[str, str] = {
    "TWILIO_ACCOUNT_SID": "AC" + "a" * 32,
    "TWILIO_AUTH_TOKEN": "test_token_1234567890",
    "TWILIO_FROM_NUMBER": "+15551234567",
    "DEEPGRAM_API_KEY": "dg_" + "k" * 30,
    "ELEVENLABS_API_KEY": "el_" + "k" * 30,
    "NGROK_AUTHTOKEN": "ng_" + "k" * 30,
    "LITELLM_MODEL": "openai/gpt-4o-mini",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all GhostLine env vars before each test to avoid leakage.

    Also patches Settings so it doesn't read the developer's real .env file.
    """
    for key in list(TEST_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("NGROK_WS_URL", raising=False)
    clear_settings_cache()


@pytest.fixture
def fake_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set all TEST_ENV vars and return the dict."""
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)
    clear_settings_cache()
    return TEST_ENV


@pytest.fixture
async def tmp_repo(tmp_path: Path) -> AsyncIterator[Repository]:
    """A temporary in-memory or file-backed repository, closed after the test."""
    db_path = tmp_path / "test.db"
    repo = await Repository.open(db_path)
    try:
        yield repo
    finally:
        await repo.close()
