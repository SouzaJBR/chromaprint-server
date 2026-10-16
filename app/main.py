import asyncio
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl


load_dotenv()

MAX_AUDIO_LENGTH_SECONDS = int(os.getenv("MAX_AUDIO_LENGTH_SECONDS", "3600"))
FPCALC = os.environ.get("FPCALC", "fpcalc")

# Hard wall-clock budget for one /analyze request. Without this a single stuck
# request keeps its worker forever and the server stops answering clients.
ANALYZE_TIMEOUT = int(os.getenv("ANALYZE_TIMEOUT", "120"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "32"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "60"))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(100 * 1024 * 1024)))
FPCALC_TIMEOUT = int(os.getenv("FPCALC_TIMEOUT", "60"))
DECODE_TIMEOUT = int(os.getenv("DECODE_TIMEOUT", "30"))

_DURATION_CMD = [sys.executable, "-m", "app.duration_worker"]

app = FastAPI(
    title="Chromaprint Audio Analyzer",
    description="Generate a Chromaprint fingerprint and duration for audio at a URL.",
    version="0.2.1",
)

# Dedicated pool sized to MAX_CONCURRENCY so the number of threads uvicorn can
# accept matches the number of requests it can actually run.
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENCY, thread_name_prefix="analyze")


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


def _set_read_timeout(response: object, seconds: float) -> None:
    # urllib exposes no total-download deadline; clamp the underlying socket so
    # the next read cannot outlive the remaining budget.
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", raw)
    if hasattr(sock, "settimeout"):
        sock.settimeout(seconds)


def _download_audio(url: str) -> bytes:
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AnalysisError("Audio download timed out")
            _set_read_timeout(response, remaining)
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise AnalysisError(
                    f"Audio download exceeds {MAX_DOWNLOAD_BYTES} bytes"
                )
            chunks.append(chunk)
    return b"".join(chunks)


def _compute_duration(audio_bytes: bytes) -> float:
    # Decode in a child process so an FFmpeg hang can be killed instead of
    # pinning a worker thread (and the client's connection) forever.
    try:
        proc = subprocess.run(
            _DURATION_CMD,
            input=audio_bytes,
            capture_output=True,
            timeout=DECODE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("Audio decode timed out") from exc
    except OSError as exc:
        raise AnalysisError(f"duration worker failed: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[:200]
        raise AnalysisError(detail or "Unable to decode audio")

    try:
        return float(proc.stdout.decode("ascii").strip())
    except ValueError as exc:
        raise AnalysisError("Invalid duration from worker") from exc


def _compute_fingerprint(audio_bytes: bytes) -> str | None:
    cmd = [FPCALC, "-length", str(MAX_AUDIO_LENGTH_SECONDS), "-"]
    try:
        proc = subprocess.run(
            cmd,
            input=audio_bytes,
            capture_output=True,
            timeout=FPCALC_TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise AnalysisError("fpcalc not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("fpcalc timed out") from exc
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
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_executor, analyze_url, str(request.url)),
            timeout=ANALYZE_TIMEOUT,
        )
    except TimeoutError:
        raise HTTPException(
            status_code=504, detail="Analysis timed out"
        ) from None
    except AnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=server_port(),
        # Backpressure: past capacity, answer 503 instead of accepting more
        # connections that would only queue and never be answered.
        limit_concurrency=MAX_CONCURRENCY,
    )
