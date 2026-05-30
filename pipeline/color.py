"""Color grading for the daily video pipeline.

Applies a config driven color look to the assembled, captioned video.
Every choice lives under settings.color: the named look (punch, warm,
cool, cinematic, or none), explicit eq adjustments (contrast, saturation,
brightness), and an optional vignette. The step is best effort: if it is
disabled, set to look "none", or fails for any reason, the input video is
logged and returned unchanged so a daily run never breaks.
"""

from __future__ import annotations

import time

from pipeline import util


def _build_look_filters(look: str) -> list[str]:
    """Return the ffmpeg filter fragments that define a named look.

    These run before the explicit eq pass so the user contrast, saturation,
    and brightness values fine tune the look on top. Unknown looks fall back
    to no extra fragments.
    """
    look = (look or "").strip().lower()

    if look == "punch":
        # Crisp, vivid short form look: a touch of local contrast via unsharp
        # plus a small saturation lift. The heavier eq comes from settings.
        return [
            "unsharp=5:5:0.8:5:5:0.0",
            "eq=saturation=1.05",
        ]

    if look == "warm":
        # Push the white point toward orange and warm the midtones.
        return [
            "colortemperature=temperature=5200",
            "colorbalance=rs=0.04:gm=0.01:bs=-0.05:rh=0.03:bh=-0.04",
        ]

    if look == "cool":
        # Push the white point toward blue for a cold, clean feel.
        return [
            "colortemperature=temperature=8200",
            "colorbalance=rs=-0.05:bs=0.06:rh=-0.03:bh=0.05",
        ]

    if look == "cinematic":
        # Gentle teal in the shadows, warm orange in the highlights, with a
        # softened contrast and a slight desaturation for a filmic feel.
        return [
            "curves=r='0/0.02 0.5/0.5 1/0.97':b='0/0.06 0.5/0.5 1/0.94'",
            "colorbalance=rs=-0.03:gs=0.01:bs=0.06:rh=0.05:gh=0.02:bh=-0.05",
            "eq=saturation=0.95",
        ]

    # "none" or anything unrecognised contributes no look fragments.
    return []


def grade(video, settings, log) -> str:
    """Apply the configured color look to the video and return the output path.

    Reads settings.color. If color is disabled or the look is "none", the
    input video is returned unchanged. Otherwise an ffmpeg filter chain is
    built from the named look plus the explicit eq adjustments (contrast,
    saturation, brightness) and an optional vignette, then encoded to
    work/graded.mp4. On any failure a warning is logged and the input video
    is returned unchanged.
    """
    start = time.time()

    enabled = util.get(settings, "color.enabled", True)
    look = util.get(settings, "color.look", "punch")

    log.info("color.grade start: enabled=%s look=%s video=%s", enabled, look, video)

    if not enabled or str(look).strip().lower() == "none":
        log.info("color.grade skipped: color grading is off")
        return str(video)

    try:
        contrast = float(util.get(settings, "color.contrast", 1.0))
        saturation = float(util.get(settings, "color.saturation", 1.0))
        brightness = float(util.get(settings, "color.brightness", 0.0))
        vignette = bool(util.get(settings, "color.vignette", False))

        filters: list[str] = []

        # 1. The named look establishes the base grade.
        filters.extend(_build_look_filters(look))

        # 2. The explicit eq pass applies the user contrast, saturation, and
        #    brightness on top of the look so config.yaml stays in control.
        filters.append(
            f"eq=contrast={contrast:.4f}:"
            f"saturation={saturation:.4f}:"
            f"brightness={brightness:.4f}"
        )

        # 3. An optional vignette darkens the frame edges for focus.
        if vignette:
            filters.append("vignette=PI/5")

        vf = ",".join(filters)
        out = util.WORK / "graded.mp4"

        args = [
            "ffmpeg",
            "-i",
            str(video),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            # Copy the audio untouched: grading is a picture only step.
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out),
            "-y",
        ]

        util.run_cmd(args, log)

        elapsed = time.time() - start
        log.info("color.grade done in %.1fs: %s", elapsed, out)
        return str(out)

    except Exception as exc:
        # Grading is best effort: never break the daily run over a color step.
        log.warning(
            "color.grade failed, returning input unchanged: %s", exc
        )
        return str(video)
