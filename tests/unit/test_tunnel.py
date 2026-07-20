"""Tests for the ngrok tunnel wrapper."""

from __future__ import annotations

import pytest_mock

from ghostline.telephony.tunnel import NgrokTunnel, Tunnel, _https_to_wss


class TestHttpsToWss:
    def test_https_becomes_wss(self) -> None:
        assert _https_to_wss("https://abc.ngrok.io") == "wss://abc.ngrok.io"

    def test_http_becomes_ws(self) -> None:
        assert _https_to_wss("http://localhost:8000") == "ws://localhost:8000"

    def test_other_scheme_unchanged(self) -> None:
        assert _https_to_wss("ftp://x.io") == "ftp://x.io"


class TestNgrokTunnel:
    def test_open_returns_tunnel_with_ws_url(self, mocker: pytest_mock.MockerFixture) -> None:
        mock_ngrok = mocker.patch("ghostline.telephony.tunnel.ngrok")
        mock_ngrok.connect.return_value.public_url = "https://abc.ngrok.io"

        t = NgrokTunnel("ngrok_token").open(8000)

        assert isinstance(t, Tunnel)
        assert t.public_url == "https://abc.ngrok.io"
        assert t.ws_url == "wss://abc.ngrok.io/twilio"
        mock_ngrok.set_auth_token.assert_called_once_with("ngrok_token")
        mock_ngrok.connect.assert_called_once_with(8000, "http")

    def test_open_with_custom_path(self, mocker: pytest_mock.MockerFixture) -> None:
        mock_ngrok = mocker.patch("ghostline.telephony.tunnel.ngrok")
        mock_ngrok.connect.return_value.public_url = "https://x.ngrok.io"

        t = NgrokTunnel("tok").open(9000, path="/custom")

        assert t.ws_url == "wss://x.ngrok.io/custom"

    def test_close_kills_ngrok(self, mocker: pytest_mock.MockerFixture) -> None:
        mock_ngrok = mocker.patch("ghostline.telephony.tunnel.ngrok")
        NgrokTunnel("tok").close()
        mock_ngrok.kill.assert_called_once()
