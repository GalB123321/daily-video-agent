"""Assemble normalized clips into one combined video.

The join style is config driven via settings.transitions.type:

  hard        : fast concat demuxer (stream copy, re encode fallback). No blend.
  crossfade   : ffmpeg xfade transition=fade between clips, audio acrossfade.
  dip_to_black: xfade transition=fadeblack, audio acrossfade.
  whip        : xfade transition=wiperight for a whip pan feel, audio acrossfade.

For any non hard type the clips are re encoded through a programmatically built
filter_complex chain. Offsets are cumulative clip duration minus the cumulative
transition overlap, measured with util.ffprobe_duration. If the filter chain
fails for any reason the function warns and falls back to a plain hard concat so
a daily run never breaks. Output is always work/combined.mp4.
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


# Map a transition type to its ffmpeg xfade transition name.
_XFADE_NAME = {
    "crossfade": "fade",
    "dip_to_black": "fadeblack",
    "whip": "wiperight",
}


def _write_concat_list(clips: list[str], list_path: Path) -> None:
    """Write the ffmpeg concat demuxer list with single quoted abs paths."""
    lines = []
    for clip in clips:
        abs_path = str(Path(clip).resolve())
        # ffmpeg concat list escapes single quotes inside a quoted path.
        escaped = abs_path.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hard_concat(clips: list[str], combined: Path, log) -> str:
    """Fast concat demuxer with a re encode fallback. Returns combined path."""
    list_path = util.WORK / "concat_list.txt"
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

    return str(combined)


def _target_dims_fps(settings: dict) -> tuple[int, int, int]:
    """Read target width, height, fps from settings with safe fallbacks."""
    resolution = util.get(settings, "target.resolution", "1080x1920")
    fps = util.get(settings, "target.fps", 30)
    try:
        w_str, h_str = str(resolution).lower().split("x")
        width, height = int(w_str), int(h_str)
    except Exception:
        width, height = 1080, 1920
    try:
        fps = int(fps)
    except Exception:
        fps = 30
    return width, height, fps


def _build_xfade_filter(
    durations: list[float],
    transition: str,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    """Build a filter_complex string chaining xfade and acrossfade for N clips.

    Each input is first scaled, padded, sar reset and fps locked so xfade has
    matching frames to blend. Offsets are the running sum of clip durations
    minus the running sum of transition overlaps already consumed.
    """
    n = len(durations)
    parts: list[str] = []

    # Per input normalization so every clip shares geometry and frame rate.
    for i in range(n):
        parts.append(
            f"[{i}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p[v{i}]"
        )

    # Video xfade chain. Each step overlaps the previous result with the next.
    prev_label = "v0"
    # cumulative timeline length of the chain built so far (post overlaps).
    timeline = durations[0]
    for i in range(1, n):
        out_label = f"vx{i}" if i < n - 1 else "vout"
        offset = timeline - duration
        if offset < 0:
            offset = 0.0
        parts.append(
            f"[{prev_label}][v{i}]"
            f"xfade=transition={transition}:duration={duration:.3f}:"
            f"offset={offset:.3f}[{out_label}]"
        )
        # The merged segment length grows by the next clip minus the overlap.
        timeline = timeline + durations[i] - duration
        prev_label = out_label

    # Audio acrossfade chain mirrors the video chain.
    prev_a = "0:a"
    for i in range(1, n):
        out_a = f"ax{i}" if i < n - 1 else "aout"
        parts.append(
            f"[{prev_a}][{i}:a]"
            f"acrossfade=d={duration:.3f}:c1=tri:c2=tri[{out_a}]"
        )
        prev_a = out_a

    return ";".join(parts)


def _xfade_concat(
    clips: list[str],
    combined: Path,
    settings: dict,
    transition: str,
    duration: float,
    log,
) -> str:
    """Re encode the clips into combined using an xfade filter chain."""
    durations = []
    for clip in clips:
        d = util.ffprobe_duration(clip, log)
        if d <= 0:
            raise ValueError(f"assemble: could not measure duration of {clip}")
        durations.append(d)

    # An overlap longer than the shorter neighbour breaks xfade, clamp it.
    safe_duration = min([duration] + [d * 0.5 for d in durations])
    if safe_duration <= 0:
        raise ValueError("assemble: non positive transition duration")
    if safe_duration < duration:
        log.warning(
            "assemble: transition shortened to %.3fs to fit the shortest clip",
            safe_duration,
        )

    width, height, fps = _target_dims_fps(settings)
    filter_complex = _build_xfade_filter(
        durations, transition, safe_duration, width, height, fps
    )

    args = ["ffmpeg"]
    for clip in clips:
        args += ["-i", str(Path(clip).resolve())]
    args += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        str(combined),
        "-y",
    ]
    util.run_cmd(args, log)
    return str(combined)


def assemble(clips: list[str], settings: dict, log) -> str:
    """Concatenate clips into work/combined.mp4 and return that path.

    Reads settings.transitions to choose the join style. Hard does a fast
    concat. crossfade, dip_to_black and whip re encode through an xfade chain.
    Any filter failure falls back to a plain hard concat with a warning.
    """
    start = time.monotonic()
    log.info("assemble: start, %d clip(s)", len(clips))

    util.ensure_dirs()

    if not clips:
        raise ValueError("assemble: no clips provided to concatenate")

    combined = util.WORK / "combined.mp4"

    t_type = str(util.get(settings, "transitions.type", "hard")).lower()
    t_duration = util.parse_seconds(util.get(settings, "transitions.duration", "0.25sec"))

    # A single clip or a hard cut never needs a blend, take the fast path.
    if t_type == "hard" or len(clips) < 2:
        if t_type != "hard":
            log.info(
                "assemble: only one clip, using a hard concat for the %s setting",
                t_type,
            )
        result = _hard_concat(clips, combined, log)
        elapsed = time.monotonic() - start
        log.info("assemble: done in %.1fs, output %s", elapsed, result)
        return result

    transition = _XFADE_NAME.get(t_type)
    if transition is None:
        log.warning(
            "assemble: unknown transition type %r, falling back to a hard concat",
            t_type,
        )
        result = _hard_concat(clips, combined, log)
        elapsed = time.monotonic() - start
        log.info("assemble: done in %.1fs, output %s", elapsed, result)
        return result

    log.info(
        "assemble: %s transition (xfade=%s), duration %.3fs",
        t_type, transition, t_duration,
    )
    try:
        result = _xfade_concat(
            clips, combined, settings, transition, t_duration, log
        )
    except Exception as exc:  # noqa: BLE001 any filter issue degrades gracefully
        log.warning(
            "assemble: %s transition failed (%s). Falling back to a hard concat.",
            t_type, exc,
        )
        result = _hard_concat(clips, combined, log)

    elapsed = time.monotonic() - start
    log.info("assemble: done in %.1fs, output %s", elapsed, result)
    return result
