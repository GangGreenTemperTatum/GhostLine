"""Tests for the settings module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ghostline.settings import Settings, clear_settings_cache, get_settings

VALID_ENV: dict[str, str] = {
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
    """Clear all ghostline env vars, then set the valid baseline."""
    for key in list(VALID_ENV):
        monkeypatch.delenv(key, raising=False)
    clear_settings_cache()


class TestSettingsValidation:
    def test_valid_env_loads_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            monkeypatch.setenv(k, v)
        clear_settings_cache()
        s = get_settings()
        assert s.twilio_account_sid == VALID_ENV["TWILIO_ACCOUNT_SID"]
        assert s.twilio_from_number == "+15551234567"
        assert s.litellm_model == "openai/gpt-4o-mini"

    def test_missing_required_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            if k == "DEEPGRAM_API_KEY":
                continue
            monkeypatch.setenv(k, v)
        clear_settings_cache()
        with pytest.raises(ValidationError) as exc:
            Settings()
        assert "deepgram_api_key" in str(exc.value).lower()

    def test_sid_must_start_with_ac(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {**VALID_ENV, "TWILIO_ACCOUNT_SID": "XX" + "a" * 32}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValidationError, match="AC"):
            Settings()

    def test_from_number_must_be_e164(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {**VALID_ENV, "TWILIO_FROM_NUMBER": "555-123-4567"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValidationError, match=r"E\.164"):
            Settings()

    def test_log_level_must_be_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {**VALID_ENV, "LOG_LEVEL": "VERBOSE"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        with pytest.raises(ValidationError, match="log_level"):
            Settings()


class TestSecretStrHandling:
    def test_secrets_are_secret_str(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            monkeypatch.setenv(k, v)
        s = Settings()
        assert s.twilio_auth_token.get_secret_value() == VALID_ENV["TWILIO_AUTH_TOKEN"]
        # repr must not leak the secret value
        assert VALID_ENV["TWILIO_AUTH_TOKEN"] not in repr(s.twilio_auth_token)


class TestDefaults:
    def test_defaults_apply_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            monkeypatch.setenv(k, v)
        s = Settings()
        assert s.deepgram_model == "nova-2"
        assert s.deepgram_language == "en-US"
        assert s.elevenlabs_model == "eleven_monolingual_v1"
        assert s.port == 8000
        assert s.host == "0.0.0.0"


class TestSettingsCache:
    def test_get_settings_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            monkeypatch.setenv(k, v)
        clear_settings_cache()
        first = get_settings()
        second = get_settings()
        assert first is second

    def test_clear_cache_forces_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for k, v in VALID_ENV.items():
            monkeypatch.setenv(k, v)
        clear_settings_cache()
        first = get_settings()
        monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-3-5-sonnet-latest")
        clear_settings_cache()
        second = get_settings()
        assert first is not second
        assert second.litellm_model == "anthropic/claude-3-5-sonnet-latest"
