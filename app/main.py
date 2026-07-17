import asyncio
import io
import os
import subprocess
import urllib.request
from urllib.parse import urlparse

import av
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl


load_dotenv()

MAX_AUDIO_LENGTH_SECONDS = int(os.getenv("MAX_AUDIO_LENGTH_SECONDS", "3600"))
FPCALC = os.environ.get("FPCALC", "fpcalc")

app = FastAPI(
    title="Chromaprint Audio Analyzer",
    description="Generate a Chromaprint fingerprint and duration for audio at a URL.",
    version="0.3.0",
)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class AnalyzeResponse(BaseModel):
    duration: float
    fingerprint: str | None = None


class AnalysisError(Exception):
    pass


def server_port() -> int:
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65_535:
        raise ValueError("PORT must be between 1 and 65535")
    return port


def _download_audio(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _compute_duration(audio_bytes: bytes) -> float:
    try:
        with av.open(io.BytesIO(audio_bytes)) as container:
            streams = container.streams.audio
            if not streams:
                raise AnalysisError("No audio stream found")
            stream = streams[0]
            if stream.duration:
                return float(stream.duration * stream.time_base)
            if container.duration:
                return float(container.duration) / 1_000_000
            raise AnalysisError("Could not determine audio duration")
    except av.AVError as exc:
        raise AnalysisError(f"Unable to decode audio: {exc}") from exc


def _compute_fingerprint(audio_bytes: bytes) -> str | None:
    cmd = [FPCALC, "-length", str(MAX_AUDIO_LENGTH_SECONDS), "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=audio_bytes,
            capture_output=True,
            timeout=MAX_AUDIO_LENGTH_SECONDS + 60,
        )
    except FileNotFoundError as exc:
        raise AnalysisError("fpcalc not found") from exc
    except OSError as exc:
        raise AnalysisError(f"fpcalc invocation failed: {exc}") from exc

    if proc.returncode != 0:
        return None

    for line in proc.stdout.splitlines():
        try:
            parts = line.split(b"=", 1)
        except ValueError:
            continue
        if parts[0] == b"FINGERPRINT":
            try:
                return parts[1].decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


def analyze_url(url: str) -> AnalyzeResponse:
    if urlparse(url).scheme not in {"http", "https"}:
        raise AnalysisError("Only HTTP and HTTPS URLs are supported")

    try:
        audio_bytes = _download_audio(url)
    except Exception as exc:
        raise AnalysisError(f"Unable to download audio: {exc}") from exc

    duration = _compute_duration(audio_bytes)
    fingerprint = _compute_fingerprint(audio_bytes)

    return AnalyzeResponse(duration=duration, fingerprint=fingerprint)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    try:
        return await asyncio.to_thread(analyze_url, str(request.url))
    except AnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=server_port())
