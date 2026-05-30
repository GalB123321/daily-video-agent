"""Trim silence/dead air from a source clip using auto-editor.

Produces work/trimmed/clipNN.mp4. If auto-editor yields nothing because the
whole clip is silent, falls back to copying the source so the pipeline keeps
flowing.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from pipeline import util


def trim(src: str, index: int, margin: str, edit_mode: str, log) -> str:
    """Trim a source clip with auto-editor.

    Args:
        src: absolute path to the source video.
        index: 1 based clip index, used for the zero padded output name.
        margin: padding kept around kept sections, e.g. "0.2sec".
        edit_mode: "audio" or "motion" (anything else degrades to "audio").
        log: logger that receives start and elapsed messages.

    Returns:
        Absolute path string to work/trimmed/clipNN.mp4.
    """
    started = time.time()
    util.ensure_dirs()

    out = util.TRIMMED / f"clip{index:02d}.mp4"
    edit = edit_mode if edit_mode in ("audio", "motion") else "audio"

    log.info(
        "trim start: src=%s index=%02d margin=%s edit=%s out=%s",
        src,
        index,
        margin,
        edit,
        out,
    )

    if edit != edit_mode:
        log.warning(
            "trim: unknown edit_mode %r, falling back to 'audio'", edit_mode
        )

    args = [
        "auto-editor",
        str(src),
        "--margin",
        str(margin),
        "--edit",
        edit,
        "-o",
        str(out),
    ]

    try:
        util.run_cmd(args, log)
    except Exception as exc:  # auto-editor missing, error, or empty result
        log.warning(
            "trim: auto-editor failed for %s (%s), copying source unchanged",
            src,
            exc,
        )
        _copy_source(src, out, log)
        log.info("trim done (copied): %s in %.2fs", out, time.time() - started)
        return str(out)

    # auto-editor can succeed yet produce nothing when the whole clip is silent.
    if not out.exists() or out.stat().st_size == 0:
        log.warning(
            "trim: auto-editor produced no output for %s (clip likely all "
            "silent), copying source unchanged",
            src,
        )
        _copy_source(src, out, log)

    log.info("trim done: %s in %.2fs", out, time.time() - started)
    return str(out)


def _copy_source(src: str, out: Path, log) -> None:
    """Copy the source to the output path as a graceful fallback."""
    try:
        if out.exists():
            out.unlink()
        shutil.copyfile(src, out)
    except Exception as exc:
        log.warning("trim: fallback copy failed for %s: %s", src, exc)
