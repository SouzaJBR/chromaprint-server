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
