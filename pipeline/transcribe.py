"""Transcribe a normalized clip to an .srt subtitle file using faster-whisper.

This step is optional. On any failure (missing dependency, model download
error, runtime error) it logs a warning and returns None so the overall run
continues without subtitles for this clip.
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


def _format_timestamp(seconds: float) -> str:
    """Format a time in seconds as an SRT timestamp: HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    millis_total = int(round(seconds * 1000.0))
    hours, millis_total = divmod(millis_total, 3600 * 1000)
    minutes, millis_total = divmod(millis_total, 60 * 1000)
    secs, millis = divmod(millis_total, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def transcribe(src: str, index: int, language: str, log) -> str | None:
    """Transcribe ``src`` to an SRT file and return its path, or None on failure.

    Uses faster-whisper with the "base" model, int8 compute, on CPU. The heavy
    import lives inside this function so the module imports cleanly without the
    dependency installed.

    Returns the absolute path to work/srt/clipNN.srt, or None if anything fails.
    """
    start = time.time()
    clip_name = f"clip{index:02d}"
    log.info("transcribe: start %s (src: %s, language: %s)", clip_name, src, language)

    out_path = util.SRT_DIR / f"{clip_name}.srt"

    try:
        # Lazy import so the module loads without faster-whisper present.
        from faster_whisper import WhisperModel

        util.SRT_DIR.mkdir(parents=True, exist_ok=True)

        model = WhisperModel("base", device="cpu", compute_type="int8")

        # An empty language string means auto detect.
        lang = language if language else None
        segments, info = model.transcribe(src, language=lang)

        lines: list[str] = []
        count = 0
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            count += 1
            start_ts = _format_timestamp(segment.start)
            end_ts = _format_timestamp(segment.end)
            lines.append(str(count))
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(text)
            lines.append("")

        if count == 0:
            log.warning(
                "transcribe: no speech segments found for %s, writing empty srt",
                clip_name,
            )

        out_path.write_text("\n".join(lines), encoding="utf-8")

        elapsed = time.time() - start
        log.info(
            "transcribe: done %s (%d segments, %s) in %.2fs",
            clip_name,
            count,
            out_path,
            elapsed,
        )
        return str(out_path)

    except Exception as exc:  # noqa: BLE001 graceful degradation
        elapsed = time.time() - start
        log.warning(
            "transcribe: failed for %s after %.2fs, continuing without subtitles. reason: %s",
            clip_name,
            elapsed,
            exc,
        )
        return None
