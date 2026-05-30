"""Transcribe a clip to word timed text using faster-whisper.

This step is optional. It writes two files per clip into work/srt:
  · clipNN.srt          a plain SRT for human reading
  · clipNN.words.json   a JSON list of {"word", "start", "end"} in clip local
                        seconds, words flattened across all segments. This is
                        the data contract that the emphasis, motion, and
                        captions modules consume.

On ANY failure (missing dependency, model download error, no speech, runtime
error) it logs a warning and returns {"srt": None, "words": None} so the
overall daily run continues with no captions for this clip. The heavy
faster_whisper import is lazy, inside the function, so this module imports
cleanly even when the dependency is not installed.
"""

from __future__ import annotations

import json
import time

from pipeline import util


def _format_timestamp(seconds: float) -> str:
    """Format a time in seconds as an SRT timestamp: HH:MM:SS,mmm."""
    if seconds is None or seconds < 0:
        seconds = 0.0
    millis_total = int(round(float(seconds) * 1000.0))
    hours, millis_total = divmod(millis_total, 3600 * 1000)
    minutes, millis_total = divmod(millis_total, 60 * 1000)
    secs, millis = divmod(millis_total, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def transcribe(src: str, index: int, settings: dict, log) -> dict:
    """Transcribe ``src`` and write SRT plus word timestamps.

    Returns {"srt": path|None, "words": path|None}. Uses faster-whisper with
    the "base" model, int8 compute, on CPU, with word_timestamps enabled. The
    caption language comes from settings.captions.language ("" means auto
    detect).
    """
    start = time.time()
    clip_name = f"clip{index:02d}"
    language = util.get(settings, "captions.language", "en")
    log.info(
        "transcribe: start %s (src: %s, language: %s)",
        clip_name, src, language or "auto",
    )

    srt_path = util.SRT_DIR / f"{clip_name}.srt"
    words_path = util.SRT_DIR / f"{clip_name}.words.json"

    try:
        # Lazy import so the module loads without faster-whisper present.
        from faster_whisper import WhisperModel

        util.SRT_DIR.mkdir(parents=True, exist_ok=True)

        model = WhisperModel("base", device="cpu", compute_type="int8")

        # An empty language string means auto detect.
        lang = language if language else None
        segments, _info = model.transcribe(
            src, language=lang, word_timestamps=True
        )

        srt_lines: list[str] = []
        words: list[dict] = []
        seg_count = 0

        for segment in segments:
            text = (segment.text or "").strip()

            # Flatten word timings across every segment into clip local time.
            seg_words = getattr(segment, "words", None) or []
            for w in seg_words:
                token = (getattr(w, "word", "") or "").strip()
                if not token:
                    continue
                w_start = getattr(w, "start", None)
                w_end = getattr(w, "end", None)
                if w_start is None or w_end is None:
                    continue
                words.append({
                    "word": token,
                    "start": float(w_start),
                    "end": float(w_end),
                })

            if not text:
                continue
            seg_count += 1
            start_ts = _format_timestamp(segment.start)
            end_ts = _format_timestamp(segment.end)
            srt_lines.append(str(seg_count))
            srt_lines.append(f"{start_ts} --> {end_ts}")
            srt_lines.append(text)
            srt_lines.append("")

        if seg_count == 0 and not words:
            log.warning(
                "transcribe: no speech found for %s, continuing without captions",
                clip_name,
            )
            return {"srt": None, "words": None}

        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        words_path.write_text(
            json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        elapsed = time.time() - start
        log.info(
            "transcribe: done %s (%d segments, %d words) in %.2fs",
            clip_name, seg_count, len(words), elapsed,
        )
        return {
            "srt": str(srt_path),
            "words": str(words_path) if words else None,
        }

    except Exception as exc:  # noqa: BLE001 graceful degradation
        elapsed = time.time() - start
        log.warning(
            "transcribe: failed for %s after %.2fs, continuing without captions. reason: %s",
            clip_name, elapsed, exc,
        )
        return {"srt": None, "words": None}
