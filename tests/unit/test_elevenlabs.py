"""Tests for the ElevenLabs voice service."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_mock
import respx
from httpx import Response

from ghostline.tts.elevenlabs import VoiceService

# A minimal but valid MP3 header (silent frame) so pydub can decode it.
_SILENT_MP3 = bytes.fromhex(
    "FFFB9064000000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000000000000000"
)
# Pad to exceed the MIN_VALID_PCM_BYTES check after pydub decode.
_SILENT_MP3 += b"\x00" * 4096


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


class TestClone:
    @respx.mock
    async def test_clone_returns_voice_id(self, tmp_path: Path, client: httpx.AsyncClient) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
        respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
            return_value=Response(200, json={"voice_id": "vid_abc"})
        )
        svc = VoiceService("test_key", client=client)
        vid = await svc.clone(str(sample), name="test")
        assert vid == "vid_abc"

    @respx.mock
    async def test_clone_returns_none_on_error(
        self, tmp_path: Path, client: httpx.AsyncClient
    ) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF....WAVE")
        respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
            return_value=Response(401, text="unauthorized")
        )
        svc = VoiceService("test_key", client=client)
        assert await svc.clone(str(sample)) is None

    @respx.mock
    async def test_clone_returns_none_when_response_lacks_voice_id(
        self, tmp_path: Path, client: httpx.AsyncClient
    ) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF....WAVE")
        respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
            return_value=Response(200, json={"unexpected": "shape"})
        )
        svc = VoiceService("test_key", client=client)
        assert await svc.clone(str(sample)) is None

    @respx.mock
    async def test_clone_returns_none_on_network_error(
        self, tmp_path: Path, client: httpx.AsyncClient
    ) -> None:
        sample = tmp_path / "voice.wav"
        sample.write_bytes(b"RIFF....WAVE")
        respx.post("https://api.elevenlabs.io/v1/voices/add").mock(
            side_effect=httpx.ConnectError("dns failure")
        )
        svc = VoiceService("test_key", client=client)
        assert await svc.clone(str(sample)) is None


class TestSynth:
    @respx.mock
    async def test_synth_returns_pcm_bytes(self, client: httpx.AsyncClient) -> None:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid").mock(
            return_value=Response(200, content=_SILENT_MP3, headers={"content-type": "audio/mpeg"})
        )
        svc = VoiceService("test_key", client=client)
        pcm = await svc.synth("hello", "vid")
        assert isinstance(pcm, bytes)
        assert len(pcm) > 0

    @respx.mock
    async def test_synth_returns_silence_on_http_error(self, client: httpx.AsyncClient) -> None:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid").mock(
            return_value=Response(500, text="server error")
        )
        svc = VoiceService("test_key", client=client)
        pcm = await svc.synth("hello", "vid")
        assert isinstance(pcm, bytes)
        # Fallback silence should be all-zero bytes
        assert all(b == 0 for b in pcm)

    @respx.mock
    async def test_synth_returns_silence_on_tiny_payload(self, client: httpx.AsyncClient) -> None:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid").mock(
            return_value=Response(200, content=b"\x00\x00")
        )
        svc = VoiceService("test_key", client=client)
        pcm = await svc.synth("hello", "vid")
        assert isinstance(pcm, bytes)
        # Should be the silence fallback, not the tiny payload
        assert len(pcm) > 100

    @respx.mock
    async def test_synth_returns_silence_on_invalid_mp3(self, client: httpx.AsyncClient) -> None:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid").mock(
            return_value=Response(200, content=b"NOT_AN_MP3" + b"x" * 5000)
        )
        svc = VoiceService("test_key", client=client)
        pcm = await svc.synth("hello", "vid")
        assert isinstance(pcm, bytes)

    @respx.mock
    async def test_synth_returns_silence_on_network_error(self, client: httpx.AsyncClient) -> None:
        respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid").mock(
            side_effect=httpx.ReadTimeout("slow")
        )
        svc = VoiceService("test_key", client=client)
        pcm = await svc.synth("hello", "vid")
        assert isinstance(pcm, bytes)
        assert all(b == 0 for b in pcm)


class TestReadAsWav:
    def test_reads_wav_file_unchanged(self, tmp_path: Path) -> None:
        sample = tmp_path / "x.wav"
        sample.write_bytes(b"RIFF content")
        assert VoiceService._read_as_wav(str(sample)) == b"RIFF content"

    def test_transcodes_m4a_to_wav(self, tmp_path: Path, mocker: pytest_mock.MockerFixture) -> None:
        # Verify the method dispatches on extension and calls pydub with m4a.
        # We mock from_file to avoid needing a real audio file / ffmpeg.
        sample = tmp_path / "x.m4a"
        sample.write_bytes(b"fake m4a")
        mock_from_file = mocker.patch(
            "ghostline.tts.elevenlabs.AudioSegment.from_file",
            side_effect=RuntimeError("pydub refused fake m4a"),
        )
        mock_export = mocker.patch("ghostline.tts.elevenlabs.AudioSegment.export")
        with pytest.raises(RuntimeError, match="pydub refused"):
            VoiceService._read_as_wav(str(sample))
        mock_from_file.assert_called_once_with(str(sample), format="m4a")
        mock_export.assert_not_called()


class TestLifecycle:
    async def test_aclose_closes_owned_client(self) -> None:
        svc = VoiceService("test_key")
        # Force client creation
        client = await svc._get_client()
        assert not client.is_closed
        await svc.aclose()
        assert client.is_closed

    async def test_injected_client_not_closed_on_aclose(self) -> None:
        # If caller injected a client, VoiceService should NOT close it
        injected = httpx.AsyncClient()
        svc = VoiceService("test_key", client=injected)
        await svc.aclose()
        assert not injected.is_closed
        await injected.aclose()
