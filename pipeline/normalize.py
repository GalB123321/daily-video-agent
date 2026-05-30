"""Normalize a trimmed clip to the target portrait canvas.

Scales each source clip into a uniform 1080x1920 (portrait) frame at a fixed
fps, letterboxing with padding so the original aspect ratio is preserved, and
guarantees a stereo audio track exists. This uniformity is what lets the later
concat step stitch clips together without glitches.
"""

import logging
import subprocess
import time

from pipeline import util


def _has_audio(src: str, log: logging.Logger) -> bool:
    """Return True when the source file carries at least one audio stream.

    Uses ffprobe. On any failure we assume there is no audio so the caller
    synthesizes a silent track, which is the safe, concat friendly default.
    """
    try:
        result = util.run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                src,
            ],
            log,
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        log.warning("ffprobe audio check failed for %s, assuming silent: %s", src, exc)
        return False


def normalize(src: str, index: int, resolution: str, fps: int, log: logging.Logger) -> str:
    """Normalize one clip to the target resolution and fps.

    resolution is "WxH" (default 1080x1920 portrait). The video is scaled to
    fit inside the canvas, padded to fill it, set to the target fps, and encoded
    as H.264 with AAC stereo audio. When the source has no audio, a silent
    stereo track is synthesized so every output clip is uniform.

    Returns the absolute path to work/normalized/clipNN.mp4.
    """
    start = time.monotonic()
    log.info("normalize: start clip%02d from %s", index, src)

    util.ensure_dirs()

    width, height = resolution.lower().split("x")
    width = width.strip()
    height = height.strip()

    out = util.NORMALIZED / f"clip{index:02d}.mp4"

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )

    has_audio = _has_audio(src, log)

    args = ["ffmpeg", "-y"]
    if has_audio:
        args += ["-i", src]
    else:
        # Synthesize a silent stereo source so the concat stays uniform.
        log.info("normalize: clip%02d has no audio, adding silent stereo track", index)
        args += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-i",
            src,
        ]

    args += [
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
    ]

    if has_audio:
        # Single input: map nothing explicitly, ffmpeg picks video plus audio.
        args += ["-shortest"]
    else:
        # Two inputs: input 0 is silent audio, input 1 is the source video.
        args += [
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            "-shortest",
        ]

    args += [str(out)]

    util.run_cmd(args, log)

    elapsed = time.monotonic() - start
    log.info("normalize: done clip%02d in %.2fs -> %s", index, elapsed, out)
    return str(out)
