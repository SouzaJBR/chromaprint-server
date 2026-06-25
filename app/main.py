import asyncio
import os
from urllib.parse import urlparse

import acoustid
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl


load_dotenv()

MAX_AUDIO_LENGTH_SECONDS = int(os.getenv("MAX_AUDIO_LENGTH_SECONDS", "3600"))

app = FastAPI(
    title="Chromaprint Audio Analyzer",
    description="Generate a Chromaprint fingerprint and duration for audio at a URL.",
    version="0.1.0",
)


class AnalyzeRequest(BaseModel):
    url: HttpUrl


class AnalyzeResponse(BaseModel):
    duration: float
    fingerprint: str


class AnalysisError(Exception):
    pass


def server_port() -> int:
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65_535:
        raise ValueError("PORT must be between 1 and 65535")
    return port


def _fingerprint_url(url: str) -> tuple[float, bytes]:
    """Pass a URL unchanged through pyacoustid's fpcalc backend.

    pyacoustid.fingerprint_file() calls os.path.abspath() on its argument,
    which makes it unsuitable for URLs. This backend is the pyacoustid path
    that preserves the URL and delegates retrieval and fingerprinting to
    Chromaprint's fpcalc.
    """
    return acoustid._fingerprint_file_fpcalc(
        url,
        MAX_AUDIO_LENGTH_SECONDS
    )


def analyze_url(url: str) -> AnalyzeResponse:
    if urlparse(url).scheme not in {"http", "https"}:
        raise AnalysisError("Only HTTP and HTTPS URLs are supported")

    try:
        duration, fingerprint = _fingerprint_url(url)
    except (
        acoustid.FingerprintGenerationError,
        acoustid.NoBackendError,
        OSError,
    ) as exc:
        raise AnalysisError(f"Unable to analyze audio URL: {exc}") from exc

    try:
        fingerprint_text = fingerprint.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AnalysisError("pyacoustid returned an invalid fingerprint") from exc

    return AnalyzeResponse(duration=duration, fingerprint=fingerprint_text)


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
