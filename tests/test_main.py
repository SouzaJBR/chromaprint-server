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
    def fingerprint(url: str) -> tuple[float, bytes]:
        assert url == "https://samplelib.com/mp3/sample-15s.mp3"
        return 15.0, b"AQAA-example"

    monkeypatch.setattr(main, "_fingerprint_url", fingerprint)

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


def test_analysis_failure_is_unprocessable(monkeypatch) -> None:
    def fail(url: str) -> tuple[float, bytes]:
        raise main.acoustid.FingerprintGenerationError("fpcalc failed")

    monkeypatch.setattr(main, "_fingerprint_url", fail)

    response = client.post(
        "/analyze",
        json={"url": "https://example.com/not-audio"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Unable to analyze audio URL: fpcalc failed"
    }


def test_analyze_url_passes_url_to_pyacoustid(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_backend(url: str, maxlength: int) -> tuple[float, bytes]:
        captured["url"] = url
        captured["maxlength"] = maxlength
        return 15.047, b"AQAA-fingerprint"

    monkeypatch.setattr(main.acoustid, "_fingerprint_file_fpcalc", fake_backend)

    result = main.analyze_url("https://samplelib.com/mp3/sample-15s.mp3")

    assert captured == {
        "url": "https://samplelib.com/mp3/sample-15s.mp3",
        "maxlength": main.MAX_AUDIO_LENGTH_SECONDS,
    }
    assert result.duration == 15.047
    assert result.fingerprint == "AQAA-fingerprint"
