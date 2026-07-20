"""CLI commands: clone, serve, call, analytics.

Uses Typer (the modern Click replacement). Each command is a thin wrapper
that constructs the necessary services from :class:`Settings` and delegates
to the appropriate module. The CLI is the only sync entrypoint; everything
else is async.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from ghostline.settings import Settings, get_settings
from ghostline.telephony.twilio import TwilioService
from ghostline.tts.elevenlabs import VoiceService

__all__ = ("app",)

_LOGGER = logging.getLogger(__name__)

app = typer.Typer(
    name="ghostline",
    help="LLM-fueled AI-powered vishing operative for authorized security assessments.",
    no_args_is_help=True,
    add_completion=False,
)


def _configure_logging(level: str) -> None:
    """Set up structured logging at the given level."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@app.command()
def clone(
    sample: Annotated[Path, typer.Argument(help="Path to WAV or M4A voice sample.")],
    name: Annotated[
        str, typer.Option(help="Display name for the cloned voice.")
    ] = "ghostline_voice",
) -> None:
    """Clone a voice sample via ElevenLabs and print the new voice_id."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    svc = VoiceService(
        api_key=settings.elevenlabs_api_key.get_secret_value(),
        model_id=settings.elevenlabs_model,
    )

    async def _clone_and_close() -> str | None:
        """Run clone + aclose in a single event loop (httpx client is loop-bound)."""
        try:
            return await svc.clone(str(sample), name=name)
        finally:
            await svc.aclose()

    vid = asyncio.run(_clone_and_close())
    if vid:
        typer.echo(f"Voice created. ID: {vid}")
    else:
        typer.echo("Voice cloning failed.")
        raise typer.Exit(code=1)


@app.command()
def serve(
    voice_id: Annotated[str, typer.Option(help="ElevenLabs voice ID to use for TTS.")],
    port: Annotated[int, typer.Option(help="Port for the FastAPI server.")] = 8000,
    playbook: Annotated[Path | None, typer.Option(help="Path to a playbook YAML file.")] = None,
) -> None:
    """Start the FastAPI server + ngrok tunnel and serve calls."""
    from ghostline.app import SalesAutomationApp

    settings = get_settings()
    _configure_logging(settings.log_level)
    pb = None
    if playbook is not None:
        from ghostline.playbook import Playbook

        pb = Playbook.from_file(playbook)
    runner = SalesAutomationApp(settings=settings, playbook=pb)
    runner.serve(voice_id=voice_id, port=port)


@app.command()
def call(
    phone: Annotated[str, typer.Argument(help="E.164 phone number to dial, e.g. +15551234567.")],
    campaign: Annotated[str | None, typer.Option(help="Campaign name for analytics.")] = None,
    persona: Annotated[str | None, typer.Option(help="Persona style for TTS prosody.")] = None,
) -> None:
    """Place an outbound call via Twilio, connected to the running server."""
    settings = get_settings()
    _configure_logging(settings.log_level)
    ws_url = _resolve_ws_url(settings)
    if not ws_url:
        typer.echo("No NGROK_WS_URL found. Start 'serve' first.")
        raise typer.Exit(code=1)
    # Append query params
    params: list[str] = []
    if campaign:
        params.append(f"campaign={campaign}")
    if persona:
        params.append(f"persona={persona}")
    if params:
        ws_url += "?" + "&".join(params)
    svc = TwilioService(
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token.get_secret_value(),
        from_number=settings.twilio_from_number,
    )
    placed = svc.place_call(phone, ws_url)
    typer.echo(f"Call placed. SID: {placed.sid}")


@app.command()
def analytics(
    output: Annotated[
        Path, typer.Option(help="Output file path (CSV or HTML by extension).")
    ] = Path("ghostline_analytics.csv"),
) -> None:
    """Export call analytics to CSV or HTML."""
    from ghostline.app import SalesAutomationApp

    settings = get_settings()
    _configure_logging(settings.log_level)
    runner = SalesAutomationApp(settings=settings, playbook=None)
    asyncio.run(runner.export_analytics(output))


def _resolve_ws_url(settings: Settings) -> str | None:
    """Return the ngrok WSS URL from env, or None if not set."""
    return os.environ.get(settings.ngrok_ws_url_env_var)


def main() -> None:
    """Entry point for the ``ghostline`` script."""
    app()


if __name__ == "__main__":
    main()
