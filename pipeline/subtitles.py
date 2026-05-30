"""Subtitle handling: merge per clip SRT files with cumulative time offsets,
then burn the merged subtitles into a video via ffmpeg.

Both functions take a `log` logger and log start plus elapsed seconds.
Optional behavior degrades gracefully: merge returns None when there are
no cues at all, and the caller may then skip the burn step.
"""

import re
import time
from pathlib import Path

from pipeline import util


# SRT timestamp pattern: HH:MM:SS,mmm --> HH:MM:SS,mmm  (the "-->" arrow is
# SRT syntax, not prose punctuation, so it must stay exactly as written).
_TIME_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _ms_to_timestamp(ms: int) -> str:
    """Convert milliseconds to an SRT timestamp HH:MM:SS,mmm."""
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _parse_timestamp(h, m, s, ms) -> int:
    """Convert split timestamp groups to milliseconds."""
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)


def _probe_duration(path: str, log) -> float:
    """Return the duration of a media file in seconds via ffprobe.

    Returns 0.0 if the probe fails or yields no value so a bad clip does not
    abort the whole merge.
    """
    args = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        result = util.run_cmd(args, log)
        text = (result.stdout or "").strip()
        return float(text) if text else 0.0
    except Exception as exc:
        log.warning("ffprobe duration failed for %s: %s", path, exc)
        return 0.0


def _read_cues(srt_path: str, offset_ms: int, log) -> list[tuple]:
    """Read cues from one SRT and shift every timestamp by offset_ms.

    Returns a list of (start_ms, end_ms, text_lines) tuples. Indices are
    intentionally dropped here and renumbered when the merged file is written.
    """
    cues: list[tuple] = []
    try:
        raw = Path(srt_path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("could not read srt %s: %s", srt_path, exc)
        return cues

    # Split into blocks on blank lines, tolerant of CRLF.
    blocks = re.split(r"\r?\n\s*\r?\n", raw.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip() != ""] if block else []
        if not lines:
            continue
        time_idx = None
        match = None
        for i, line in enumerate(lines):
            match = _TIME_LINE.search(line)
            if match:
                time_idx = i
                break
        if match is None:
            continue
        start_ms = _parse_timestamp(*match.group(1, 2, 3, 4)) + offset_ms
        end_ms = _parse_timestamp(*match.group(5, 6, 7, 8)) + offset_ms
        text_lines = lines[time_idx + 1:]
        if not text_lines:
            continue
        cues.append((start_ms, end_ms, text_lines))
    return cues


def merge_srts(items: list[tuple], log) -> str | None:
    """Merge per clip SRT files into one, shifting each clip's cues by the
    cumulative duration of all prior clips.

    items is a list of (normalized_path, srt_path_or_None) in concat order.
    Clips whose srt is None contribute no cues but still advance the running
    offset by their own duration. Writes util.WORK / "merged.srt".
    Returns the merged path, or None when there are zero cues in total.
    """
    start = time.time()
    log.info("merge_srts: start, %d clip(s)", len(items))

    all_cues: list[tuple] = []
    offset_ms = 0

    for normalized_path, srt_path in items:
        duration_s = _probe_duration(normalized_path, log)
        if srt_path:
            cues = _read_cues(srt_path, offset_ms, log)
            log.info("merge_srts: %s contributed %d cue(s)", srt_path, len(cues))
            all_cues.extend(cues)
        else:
            log.info("merge_srts: no srt for %s, offset only", normalized_path)
        offset_ms += int(round(duration_s * 1000))

    if not all_cues:
        log.warning("merge_srts: zero cues total, returning None")
        log.info("merge_srts: done in %.2fs", time.time() - start)
        return None

    all_cues.sort(key=lambda c: c[0])

    out_path = util.WORK / "merged.srt"
    lines: list[str] = []
    for index, (start_ms, end_ms, text_lines) in enumerate(all_cues, start=1):
        lines.append(str(index))
        lines.append(f"{_ms_to_timestamp(start_ms)} --> {_ms_to_timestamp(end_ms)}")
        lines.extend(text_lines)
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    log.info(
        "merge_srts: wrote %d cue(s) to %s in %.2fs",
        len(all_cues), out_path, time.time() - start,
    )
    return str(out_path)


def _escape_subtitles_path(path: str) -> str:
    """Escape a path for use inside the ffmpeg subtitles filter.

    The filter parses its argument, so backslashes, colons, single quotes,
    and brackets must be escaped, and the whole value is single quoted.
    """
    escaped = path.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    return escaped


def burn_subtitles(video: str, srt: str, log) -> str:
    """Burn an SRT into a video with ffmpeg and return the output path.

    Output is util.WORK / "subbed.mp4". Audio is copied unchanged.
    """
    start = time.time()
    log.info("burn_subtitles: start, video=%s srt=%s", video, srt)

    out_path = util.WORK / "subbed.mp4"
    escaped_srt = _escape_subtitles_path(srt)
    vf = f"subtitles='{escaped_srt}':force_style='Fontsize=18,Outline=1'"

    args = [
        "ffmpeg",
        "-i", video,
        "-vf", vf,
        "-c:a", "copy",
        str(out_path),
        "-y",
    ]
    util.run_cmd(args, log)

    log.info("burn_subtitles: wrote %s in %.2fs", out_path, time.time() - start)
    return str(out_path)
