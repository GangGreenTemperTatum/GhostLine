"""ngrok tunnel wrapper.

The legacy code inlined pyngrok setup inside the CLI command. We isolate it
here so the FastAPI server can be booted without bringing up a tunnel in
tests, and so swapping tunnel providers later (Cloudflare, FRP) is local.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyngrok import ngrok

__all__ = ("NgrokTunnel", "Tunnel")

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class Tunnel:
    """A public tunnel that proxies to a local port."""

    public_url: str  # https://...
    ws_url: str  # wss://.../twilio


class NgrokTunnel:
    """Wraps pyngrok to expose a local port over HTTPS/WSS."""

    def __init__(self, authtoken: str) -> None:
        self._authtoken = authtoken

    def open(self, port: int, *, path: str = "/twilio") -> Tunnel:
        """Connect an ngrok tunnel and return public + WSS URLs.

        Args:
            port: Local port to forward traffic to.
            path: URL path appended to the WSS URL (default ``/twilio``).

        Returns:
            A :class:`Tunnel` with both HTTPS and WSS URLs.
        """
        ngrok.set_auth_token(self._authtoken)
        public_url = ngrok.connect(port, "http").public_url
        ws_url = _https_to_wss(public_url) + path
        _LOGGER.info("ngrok tunnel open: %s -> localhost:%d (WSS=%s)", public_url, port, ws_url)
        return Tunnel(public_url=public_url, ws_url=ws_url)

    def close(self) -> None:
        """Tear down all ngrok tunnels owned by this process."""
        ngrok.kill()
        _LOGGER.info("ngrok tunnels closed")


def _https_to_wss(url: str) -> str:
    """Convert ``https://host`` to ``wss://host`` (or http→ws)."""
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    return url
