"""Composition root: constructs all services from Settings and wires them.

This is the single place where Settings → concrete instances happens.
The CLI commands and the server both go through here so there's one
source of truth for how services are configured.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from collections.abc import Awaitable
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

from ghostline.audio.ambient import AmbientNoise
from ghostline.persistence.repository import Repository
from ghostline.server.dashboard import render_dashboard_from_repo
from ghostline.server.fastapi import ServerDeps, create_app
from ghostline.settings import Settings
from ghostline.telephony.tunnel import NgrokTunnel
from ghostline.tts.elevenlabs import VoiceService

if TYPE_CHECKING:
    from ghostline.playbook import Playbook

__all__ = ("SalesAutomationApp",)

_LOGGER = logging.getLogger(__name__)


class SalesAutomationApp:
    """Owns long-lived services for one GhostLine process.

    Constructed once by the CLI; the FastAPI app reads from the
    :class:`ServerDeps` instance built in :meth:`serve`.
    """

    def __init__(self, settings: Settings, playbook: Playbook | None) -> None:
        self.settings = settings
        self.playbook = playbook
        self._voice_service: VoiceService | None = None
        self._ambient: AmbientNoise | None = None
        self._repository: Repository | None = None

    @property
    def voice_service(self) -> VoiceService:
        """Lazily construct the ElevenLabs voice service."""
        if self._voice_service is None:
            self._voice_service = VoiceService(
                api_key=self.settings.elevenlabs_api_key.get_secret_value(),
                model_id=self.settings.elevenlabs_model,
            )
        return self._voice_service

    @property
    def ambient(self) -> AmbientNoise:
        """Lazily construct the ambient noise manager."""
        if self._ambient is None:
            self._ambient = AmbientNoise(path=self.settings.babble_noise_path)
        return self._ambient

    async def repository(self) -> Repository:
        """Open the async sqlite repository (idempotent)."""
        if self._repository is None:
            self._repository = await Repository.open(self.settings.sqlite_db_path)
        return self._repository

    def serve(self, *, voice_id: str, port: int) -> None:
        """Start the FastAPI server + ngrok tunnel and serve calls."""
        # Open ngrok tunnel
        tunnel = NgrokTunnel(self.settings.ngrok_authtoken.get_secret_value())
        t = tunnel.open(port)
        os.environ[self.settings.ngrok_ws_url_env_var] = t.ws_url
        _LOGGER.info("ngrok tunnel: %s (WSS=%s)", t.public_url, t.ws_url)

        # Build server deps — repository is opened synchronously via a one-shot.
        repo = self._run_async(self.repository())

        deps = ServerDeps(
            repository=repo,
            voice_service=self.voice_service,
            ambient=self.ambient,
            deepgram_api_key=self.settings.deepgram_api_key.get_secret_value(),
            deepgram_language=self.settings.deepgram_language,
            deepgram_model=self.settings.deepgram_model,
            playbook=self.playbook,
            voice_id=voice_id,
            ngrok_ws_url=t.ws_url,
        )
        fastapi_app = create_app(deps)
        try:
            uvicorn.run(fastapi_app, host=self.settings.host, port=port)
        finally:
            tunnel.close()

    async def export_analytics(self, output: Path) -> None:
        """Export call + message analytics to CSV or HTML."""
        repo = await self.repository()
        if output.suffix.lower() == ".html":
            html = await render_dashboard_from_repo(
                repo,
                playbook_name=self.playbook.meta.name if self.playbook else None,
            )
            await asyncio.to_thread(output.write_text, html, "utf-8")
            _LOGGER.info("Wrote HTML analytics to %s", output)
            return
        calls_count = await repo.call_count()
        stage_counts = await repo.stage_counts()

        def _write_csv() -> None:
            with output.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["metric", "value"])
                writer.writerow(["total_calls", calls_count])
                for stage, count in sorted(stage_counts.items()):
                    writer.writerow([f"stage_{stage}", count])

        await asyncio.to_thread(_write_csv)
        _LOGGER.info("Wrote CSV analytics to %s", output)

    @staticmethod
    def _run_async(awaitable: Awaitable[Repository]) -> Repository:
        """Run an awaitable to completion synchronously (CLI entrypoint helper)."""
        return asyncio.run(awaitable)  # type: ignore[arg-type]

    async def aclose(self) -> None:
        """Clean shutdown: close voice service + repository."""
        if self._voice_service is not None:
            await self._voice_service.aclose()
        if self._repository is not None:
            await self._repository.close()
