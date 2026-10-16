"""Killable duration probe.

Run as a subprocess so a libav hang cannot wedge a server thread: the parent
enforces a timeout and kills this process. Reads the audio from stdin and
prints the duration in seconds on success.
"""

import io
import sys

import av


def duration_of(audio_bytes: bytes) -> float:
    with av.open(io.BytesIO(audio_bytes)) as container:
        streams = container.streams.audio
        if not streams:
            raise ValueError("No audio stream found")
        stream = streams[0]
        if stream.duration:
            return float(stream.duration * stream.time_base)
        if container.duration:
            return float(container.duration) / 1_000_000
    raise ValueError("Could not determine audio duration")


def main() -> int:
    data = sys.stdin.buffer.read()
    try:
        print(duration_of(data))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
