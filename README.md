# Chromaprint Audio Analyzer API

This service accepts an HTTP(S) audio URL and returns its duration and
Chromaprint fingerprint. The application passes the URL to `pyacoustid`,
which delegates retrieval and fingerprinting to Chromaprint. The service
does not download or create a local audio file itself and does not use
FFmpeg/libav directly.

## Run with Docker Compose

```sh
cp .env.example .env
docker compose up --build
```

Analyze the prototype sample:

```sh
curl -X POST http://localhost:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://samplelib.com/mp3/sample-15s.mp3"}'
```

Response:

```json
{
  "duration": 19.0,
  "fingerprint": "AQAA..."
}
```

Interactive API documentation is available at <http://localhost:8000/docs>.
The health endpoint is <http://localhost:8000/health>.

## Development with uv

The project uses `uv` exclusively for dependency management and execution:

```sh
uv sync
uv run pytest
cp .env.example .env
uv run python -m app.main
```

Set `PORT` in `.env` to change both the application listen port and the Docker Compose host/container port. It defaults to `8000`.

The host must provide Chromaprint's `fpcalc` backend. On Debian/Ubuntu:

```sh
sudo apt-get install libchromaprint-tools
```
