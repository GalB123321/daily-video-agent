"""Concatenate normalized clips into one combined video.

Stream copies the clips together when their codecs match. If the copy concat
fails because of a codec mismatch, it retries once by re encoding to
libx264 plus aac so mixed sources still join cleanly.
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


def _write_concat_list(clips: list[str], list_path: Path) -> None:
    """Write the ffmpeg concat demuxer list with single quoted abs paths."""
    lines = []
    for clip in clips:
        abs_path = str(Path(clip).resolve())
        # ffmpeg concat list escapes single quotes inside a quoted path.
        escaped = abs_path.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def assemble(clips: list[str], log) -> str:
    """Concatenate clips into work/combined.mp4 and return that path.

    Tries a fast stream copy concat first. On failure (typically a codec
    mismatch across clips) it retries once with a libx264 plus aac re encode.
    """
    start = time.monotonic()
    log.info("assemble: start, %d clip(s)", len(clips))

    util.ensure_dirs()

    if not clips:
        raise ValueError("assemble: no clips provided to concatenate")

    list_path = util.WORK / "concat_list.txt"
    combined = util.WORK / "combined.mp4"

    _write_concat_list(clips, list_path)
    log.info("assemble: wrote concat list %s", list_path)

    copy_args = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        str(combined),
        "-y",
    ]

    try:
        util.run_cmd(copy_args, log)
    except Exception as exc:  # noqa: BLE001 stream copy can fail many ways
        log.warning(
            "assemble: stream copy concat failed (%s). Retrying with re encode.",
            exc,
        )
        reencode_args = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            str(combined),
            "-y",
        ]
        util.run_cmd(reencode_args, log)

    elapsed = time.monotonic() - start
    log.info("assemble: done in %.1fs, output %s", elapsed, combined)
    return str(combined)
