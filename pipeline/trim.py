"""Config driven silence trimming for a single source clip.

Reads settings.cutting.* and uses auto-editor to cut dead air, producing
work/trimmed/clipNN.mp4. Every failure mode degrades gracefully: if
auto-editor is missing, errors, or would cut the entire clip, the source is
copied through unchanged so the daily run never breaks.

Cutting settings consumed (see the merged schema):
  cutting.remove_silence   bool   when false, copy source through untouched.
  cutting.margin           dur    padding kept around speech, e.g. "0.2sec".
  cutting.edit_mode        str    "audio" keeps speech, "none" keeps everything.
  cutting.silence_threshold str   audio level below which a section is cut,
                                  e.g. "4%", passed as audio:threshold=4%.
  cutting.keep_pauses      bool   widen the margin so natural pauses survive.
  cutting.min_clip_seconds float  if the trimmed clip is shorter than this,
                                  fall back to the source (over aggressive cut).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from pipeline import util


def trim(src: str, index: int, settings: dict, log) -> str:
    """Trim silence from a source clip per settings.cutting.*.

    Args:
        src: absolute path to the source video.
        index: 1 based clip index, used for the zero padded output name.
        settings: merged settings dict (presets plus preset bundle plus user).
        log: logger that receives start and elapsed messages.

    Returns:
        Absolute path string to work/trimmed/clipNN.mp4. Always a real file:
        either the auto-editor result or a copy of the source on any degrade.
    """
    started = time.time()
    util.ensure_dirs()

    out = util.TRIMMED / f"clip{index:02d}.mp4"

    remove_silence = bool(util.get(settings, "cutting.remove_silence", True))
    margin = util.get(settings, "cutting.margin", "0.2sec")
    edit_mode = util.get(settings, "cutting.edit_mode", "audio")
    silence_threshold = util.get(settings, "cutting.silence_threshold", "4%")
    keep_pauses = bool(util.get(settings, "cutting.keep_pauses", False))
    min_clip_seconds = util.parse_seconds(
        util.get(settings, "cutting.min_clip_seconds", 0.0)
    )

    log.info(
        "trim start: src=%s index=%02d remove_silence=%s edit=%s "
        "margin=%s threshold=%s keep_pauses=%s out=%s",
        src,
        index,
        remove_silence,
        edit_mode,
        margin,
        silence_threshold,
        keep_pauses,
        out,
    )

    # When silence removal is off (or edit_mode is "none") keep every frame.
    if not remove_silence or str(edit_mode).lower() == "none":
        log.info("trim: silence removal disabled, copying source unchanged")
        _copy_source(src, out, log)
        log.info("trim done (copied): %s in %.2fs", out, time.time() - started)
        return str(out)

    edit = str(edit_mode).lower() if str(edit_mode).lower() in ("audio", "motion") else "audio"
    if edit != str(edit_mode).lower():
        log.warning("trim: unknown edit_mode %r, falling back to 'audio'", edit_mode)

    # Compose the edit expression. For audio editing, pass the silence
    # threshold through to auto-editor as audio:threshold=<value>.
    edit_expr = edit
    if edit == "audio" and silence_threshold:
        edit_expr = f"audio:threshold={silence_threshold}"

    # Keeping pauses means leaving more breathing room around speech: widen
    # the kept margin so natural beats are not clipped into a jump cut.
    effective_margin = margin
    if keep_pauses:
        effective_margin = _widen_margin(margin, log)

    args = [
        "auto-editor",
        str(src),
        "--margin",
        str(effective_margin),
        "--edit",
        edit_expr,
        "--no-open",
        "-o",
        str(out),
    ]

    try:
        util.run_cmd(args, log)
    except Exception as exc:  # auto-editor missing, errored, or refused.
        log.warning(
            "trim: auto-editor failed for %s (%s), copying source unchanged",
            src,
            exc,
        )
        _copy_source(src, out, log)
        log.info("trim done (copied): %s in %.2fs", out, time.time() - started)
        return str(out)

    # auto-editor can succeed yet emit nothing when the whole clip is silent.
    if not out.exists() or out.stat().st_size == 0:
        log.warning(
            "trim: auto-editor produced no output for %s (clip likely all "
            "silent), copying source unchanged",
            src,
        )
        _copy_source(src, out, log)
        log.info("trim done (copied): %s in %.2fs", out, time.time() - started)
        return str(out)

    # Guard against an over aggressive cut leaving almost nothing behind.
    if min_clip_seconds > 0:
        trimmed_dur = util.ffprobe_duration(out, log)
        if 0 < trimmed_dur < min_clip_seconds:
            log.warning(
                "trim: trimmed clip %.2fs below min_clip_seconds %.2fs for %s, "
                "copying source unchanged",
                trimmed_dur,
                min_clip_seconds,
                src,
            )
            _copy_source(src, out, log)
            log.info("trim done (copied): %s in %.2fs", out, time.time() - started)
            return str(out)

    log.info("trim done: %s in %.2fs", out, time.time() - started)
    return str(out)


def _widen_margin(margin, log) -> str:
    """Return a wider margin (roughly double) so natural pauses survive.

    Falls back to a sensible default when the input margin cannot be parsed.
    """
    seconds = util.parse_seconds(margin)
    if seconds <= 0:
        seconds = 0.2
    widened = round(seconds * 2.0, 3)
    log.info("trim: keep_pauses on, widening margin %s to %ssec", margin, widened)
    return f"{widened}sec"


def _copy_source(src, out: Path, log) -> None:
    """Copy the source to the output path as a graceful fallback."""
    try:
        if out.exists():
            out.unlink()
        shutil.copyfile(src, out)
    except Exception as exc:
        log.warning("trim: fallback copy failed for %s: %s", src, exc)
