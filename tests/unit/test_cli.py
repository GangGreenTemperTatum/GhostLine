"""Tests for the CLI commands and composition root."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_mock
from typer.testing import CliRunner

from ghostline.app import SalesAutomationApp
from ghostline.cli import app
from ghostline.settings import Settings
from ghostline.telephony.twilio import CallPlaced


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Build Settings with fake test values."""
    env = {
        "TWILIO_ACCOUNT_SID": "AC" + "a" * 32,
        "TWILIO_AUTH_TOKEN": "tok_" + "k" * 20,
        "TWILIO_FROM_NUMBER": "+15551234567",
        "DEEPGRAM_API_KEY": "dg_" + "k" * 30,
        "ELEVENLABS_API_KEY": "el_" + "k" * 30,
        "NGROK_AUTHTOKEN": "ng_" + "k" * 30,
        "LITELLM_MODEL": "openai/gpt-4o-mini",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings()


class TestCliHelp:
    def test_help_lists_commands(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "clone" in result.stdout
        assert "serve" in result.stdout
        assert "call" in result.stdout
        assert "analytics" in result.stdout

    def test_no_args_shows_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, [])
        # Typer with no_args_is_help=True exits with code 0 or 2
        assert result.exit_code in (0, 2)


class TestCloneCommand:
    def test_clone_succeeds(
        self,
        runner: CliRunner,
        fake_settings: Settings,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF content")
        mock_get_settings = mocker.patch("ghostline.cli.get_settings", return_value=fake_settings)
        mock_voice_svc = mocker.patch("ghostline.cli.VoiceService")
        mock_instance = mock_voice_svc.return_value
        mock_instance.clone = AsyncMock(return_value="vid_abc")
        mock_instance.aclose = AsyncMock()

        result = runner.invoke(app, ["clone", str(sample), "--name", "test_voice"])

        assert result.exit_code == 0
        assert "vid_abc" in result.stdout
        mock_get_settings.assert_called_once()
        mock_instance.clone.assert_called_once_with(str(sample), name="test_voice")

    def test_clone_failure_exits_nonzero(
        self,
        runner: CliRunner,
        fake_settings: Settings,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF content")
        mocker.patch("ghostline.cli.get_settings", return_value=fake_settings)
        mock_voice_svc = mocker.patch("ghostline.cli.VoiceService")
        mock_instance = mock_voice_svc.return_value
        mock_instance.clone = AsyncMock(return_value=None)
        mock_instance.aclose = AsyncMock()

        result = runner.invoke(app, ["clone", str(sample)])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()


class TestCallCommand:
    def test_call_succeeds(
        self,
        runner: CliRunner,
        fake_settings: Settings,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mocker.patch("ghostline.cli.get_settings", return_value=fake_settings)
        mocker.patch.dict("os.environ", {"NGROK_WS_URL": "wss://t.example.com/twilio"})
        mock_twilio = mocker.patch("ghostline.cli.TwilioService")
        mock_instance = mock_twilio.return_value
        mock_instance.place_call.return_value = CallPlaced(
            sid="CA_test", to_number="+15551234567", from_number="+15559876543"
        )

        result = runner.invoke(
            app, ["call", "+15559876543", "--campaign", "demo", "--persona", "urgent"]
        )

        assert result.exit_code == 0
        assert "CA_test" in result.stdout
        mock_instance.place_call.assert_called_once()
        call_args = mock_instance.place_call.call_args
        assert call_args.args[0] == "+15559876543"
        assert "campaign=demo" in call_args.args[1]
        assert "persona=urgent" in call_args.args[1]

    def test_call_without_tunnel_fails(
        self,
        runner: CliRunner,
        fake_settings: Settings,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        mocker.patch("ghostline.cli.get_settings", return_value=fake_settings)
        os.environ.pop("NGROK_WS_URL", None)
        result = runner.invoke(app, ["call", "+15559876543"])
        assert result.exit_code == 1
        assert "NGROK_WS_URL" in result.output


class TestAnalyticsCommand:
    def test_analytics_csv(
        self,
        runner: CliRunner,
        fake_settings: Settings,
        tmp_path: Path,
        mocker: pytest_mock.MockerFixture,
    ) -> None:
        output = tmp_path / "stats.csv"
        mocker.patch("ghostline.cli.get_settings", return_value=fake_settings)
        mock_app = mocker.MagicMock()
        mock_app.export_analytics = AsyncMock()
        mocker.patch("ghostline.app.SalesAutomationApp", return_value=mock_app)

        result = runner.invoke(app, ["analytics", "--output", str(output)])
        assert result.exit_code == 0


class TestSalesAutomationApp:
    async def test_voice_service_lazy_construction(self, fake_settings: Settings) -> None:
        app_instance = SalesAutomationApp(settings=fake_settings, playbook=None)
        assert app_instance._voice_service is None
        svc = app_instance.voice_service
        assert svc is not None
        # Second access returns the same instance
        assert app_instance.voice_service is svc

    async def test_ambient_lazy_construction(self, fake_settings: Settings) -> None:
        app_instance = SalesAutomationApp(settings=fake_settings, playbook=None)
        assert app_instance._ambient is None
        ambient = app_instance.ambient
        assert ambient is not None
        assert app_instance.ambient is ambient

    async def test_repository_opened_once(self, fake_settings: Settings) -> None:
        app_instance = SalesAutomationApp(settings=fake_settings, playbook=None)
        r1 = await app_instance.repository()
        r2 = await app_instance.repository()
        assert r1 is r2
        await r1.close()

    async def test_aclose_cleans_up(self, fake_settings: Settings) -> None:
        app_instance = SalesAutomationApp(settings=fake_settings, playbook=None)
        # Force construction
        _ = app_instance.voice_service
        _ = await app_instance.repository()
        await app_instance.aclose()
        assert app_instance._repository is not None
        # Second aclose should be safe
        await app_instance.aclose()
