import subprocess
import time
import urllib.request

import pytest
from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_server_port_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "9000")

    assert main.server_port() == 9000


def test_analyze_returns_duration_and_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(main, "_download_audio", lambda url: b"audio-data")
    monkeypatch.setattr(main, "_compute_duration", lambda data: 15.0)
    monkeypatch.setattr(main, "_compute_fingerprint", lambda data: "AQAA-example")

    response = client.post(
        "/analyze",
        json={"url": "https://samplelib.com/mp3/sample-15s.mp3"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "duration": 15.0,
        "fingerprint": "AQAA-example",
    }


def test_analyze_rejects_invalid_url() -> None:
    response = client.post("/analyze", json={"url": "not-a-url"})

    assert response.status_code == 422


def test_analyze_short_audio_no_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(main, "_download_audio", lambda url: b"short-audio")
    monkeypatch.setattr(main, "_compute_duration", lambda data: 0.5)
    monkeypatch.setattr(main, "_compute_fingerprint", lambda data: None)

    response = client.post(
        "/analyze",
        json={"url": "https://example.com/short.mp3"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "duration": 0.5,
        "fingerprint": None,
    }


def test_download_failure_is_unprocessable(monkeypatch) -> None:
    def fail(url: str) -> bytes:
        raise main.AnalysisError("download failed")

    monkeypatch.setattr(main, "_download_audio", fail)

    response = client.post(
        "/analyze",
        json={"url": "https://example.com/not-audio"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Unable to download audio: download failed"
    }


def test_analyze_url_passes_url_to_download(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_download(url: str) -> bytes:
        captured["url"] = url
        return b"audio-data"

    monkeypatch.setattr(main, "_download_audio", fake_download)
    monkeypatch.setattr(main, "_compute_duration", lambda data: 15.047)
    monkeypatch.setattr(main, "_compute_fingerprint", lambda data: "AQAA-fp")

    result = main.analyze_url("https://samplelib.com/mp3/sample-15s.mp3")

    assert captured == {"url": "https://samplelib.com/mp3/sample-15s.mp3"}
    assert result.duration == 15.047
    assert result.fingerprint == "AQAA-fp"


def test_analyze_times_out_with_504(monkeypatch) -> None:
    monkeypatch.setattr(main, "ANALYZE_TIMEOUT", 0.05)

    def slow(_url: str) -> main.AnalyzeResponse:
        time.sleep(0.5)
        return main.AnalyzeResponse(duration=1.0)

    monkeypatch.setattr(main, "analyze_url", slow)

    response = client.post("/analyze", json={"url": "https://example.com/a.mp3"})

    assert response.status_code == 504
    assert response.json() == {"detail": "Analysis timed out"}


class _FakeHTTPResponse:
    def __init__(self, body: bytes, chunk_delay: float = 0.0) -> None:
        self._body = body
        self._pos = 0
        self._delay = chunk_delay

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self, amt: int | None = None) -> bytes:
        if self._delay:
            time.sleep(self._delay)
        if self._pos >= len(self._body):
            return b""
        if amt is None:
            data = self._body[self._pos :]
            self._pos = len(self._body)
            return data
        data = self._body[self._pos : self._pos + amt]
        self._pos += len(data)
        return data


def test_download_rejects_oversized_body(monkeypatch) -> None:
    monkeypatch.setattr(main, "MAX_DOWNLOAD_BYTES", 10)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeHTTPResponse(b"x" * 100)
    )

    with pytest.raises(main.AnalysisError, match="exceeds"):
        main._download_audio("https://example.com/a.mp3")


def test_download_enforces_total_deadline(monkeypatch) -> None:
    monkeypatch.setattr(main, "DOWNLOAD_TIMEOUT", 0.05)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _FakeHTTPResponse(b"x" * 100, chunk_delay=0.1),
    )

    with pytest.raises(main.AnalysisError, match="timed out"):
        main._download_audio("https://example.com/a.mp3")


def test_fpcalc_timeout_is_analysis_error(monkeypatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="fpcalc", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(main.AnalysisError, match="timed out"):
        main._compute_fingerprint(b"audio")


def _wav_bytes(seconds: float, rate: int = 44100) -> bytes:
    import io
    import math
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        )
        w.writeframes(frames)
    return buf.getvalue()


def test_compute_duration_in_isolated_process() -> None:
    assert main._compute_duration(_wav_bytes(0.5)) == pytest.approx(0.5, abs=0.01)


def test_compute_duration_timeout_is_analysis_error(monkeypatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="duration_worker", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(main.AnalysisError, match="decode timed out"):
        main._compute_duration(b"audio")


def test_compute_duration_worker_error_surfaces_stderr(monkeypatch) -> None:
    class FakeProc:
        returncode = 1
        stdout = b""
        stderr = b"No audio stream found\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())

    with pytest.raises(main.AnalysisError, match="No audio stream found"):
        main._compute_duration(b"audio")
