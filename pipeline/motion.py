"""Cinematic camera movement for a single clip.

apply_motion adds a continuous Ken Burns zoom across the whole clip and, where
transcript words are available, quick punch in zooms over emphasized spans. The
movement is driven entirely by an ffmpeg zoompan z expression evaluated per
output frame, so the gesture stays smooth and the duration is preserved.

Everything degrades gracefully. If motion is disabled, the clip has no words,
the duration cannot be read, or zoompan fails for any reason, the function logs
a warning and returns the input clip path unchanged so the daily run never
breaks.

No dash characters are used as prose punctuation in this file. Dashes that
appear are required by ffmpeg flag syntax (for example -c:v) or by filter
expression math (for example iw/2).
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

from pipeline import util


def _load_words(words_path) -> list:
    """Read the words.json list. Returns [] on any problem."""
    try:
        with Path(words_path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _detect_emphasis(words: list, settings: dict, log: logging.Logger) -> list:
    """Call emphasis.detect_emphasis defensively.

    emphasis is an optional sibling module. If it is missing or raises, we log a
    warning and return an empty list, which simply means no punch in zooms.
    """
    try:
        from pipeline import emphasis  # lazy, optional sibling module
    except Exception as exc:
        log.warning("motion: emphasis module unavailable, skipping punch zooms: %s", exc)
        return []
    try:
        spans = emphasis.detect_emphasis(words, settings, log)
        return spans if isinstance(spans, list) else []
    except Exception as exc:
        log.warning("motion: emphasis detection failed, skipping punch zooms: %s", exc)
        return []


def _base_z_term(direction: str, amount: float, duration: float, index: int) -> str:
    """Build the linear Ken Burns part of the zoom as a function of time T.

    T is seconds since clip start. The base zoom moves linearly from one end of
    the amount range to the other across the whole clip. direction in zooms in,
    out zooms out, alternate flips per clip index so neighbours feel varied.
    """
    eff = direction
    if direction == "alternate":
        eff = "in" if index % 2 == 0 else "out"

    span = max(0.0, float(amount))
    dur = max(0.001, float(duration))

    # progress p goes 0 to 1 across the clip: p = min(T/dur, 1)
    progress = f"min(T/{dur:.6f},1)"

    if eff == "out":
        # Start zoomed in by span, ease back to 1.0.
        return f"(1+{span:.6f}*(1-{progress}))"
    # Default in: start at 1.0, ease to 1+span.
    return f"(1+{span:.6f}*{progress})"


def _emphasis_terms(spans: list, strength: float, hold: float) -> str:
    """Build the additive punch in bumps as a function of time T.

    Each emphasis span gets a gaussian bump centered on the middle of the span,
    rising to +strength and easing back out. hold sets how long the punch is
    held, which we map to the gaussian width so the zoom feels deliberate.
    """
    s = max(0.0, float(strength))
    if s <= 0.0 or not spans:
        return ""

    # Sigma controls the bell width. Tie it to the hold so a longer hold gives a
    # wider, slower punch. Clamp to a sane minimum for snappy short holds.
    sigma = max(0.08, float(hold) / 2.0)
    two_sigma_sq = 2.0 * sigma * sigma

    terms = []
    for span in spans:
        try:
            start = float(span.get("start", 0.0))
            end = float(span.get("end", start))
        except Exception:
            continue
        if end < start:
            start, end = end, start
        center = (start + end) / 2.0
        # Per span strength may scale the global strength when provided.
        try:
            local = float(span.get("strength", 1.0))
        except Exception:
            local = 1.0
        peak = s * (local if local > 0 else 1.0)
        # Gaussian bump: peak * exp(-((T-center)^2)/(2*sigma^2))
        terms.append(
            f"{peak:.6f}*exp(-(pow(T-{center:.6f},2))/{two_sigma_sq:.6f})"
        )

    if not terms:
        return ""
    return "+" + "+".join(terms)


def apply_motion(clip: str, index: int, words_path, settings: dict, log: logging.Logger) -> str:
    """Apply Ken Burns plus emphasis punch zooms to one clip.

    Returns the absolute path to work/motion/clipNN.mp4 on success. On any
    failure, or when motion is disabled, logs a warning and returns the input
    clip path unchanged so the pipeline continues.
    """
    start_t = time.monotonic()
    log.info("motion: start clip%02d from %s", index, clip)

    if not util.get(settings, "motion.enabled", True):
        log.warning("motion: disabled in settings, returning clip%02d unchanged", index)
        return clip

    util.ensure_dirs()

    # Target canvas and fps drive the z expression timing and output size.
    resolution = str(util.get(settings, "target.resolution", "1080x1920"))
    try:
        width, height = (p.strip() for p in resolution.lower().split("x"))
        int(width)
        int(height)
    except Exception:
        log.warning("motion: bad resolution %r, returning clip%02d unchanged", resolution, index)
        return clip

    fps = util.get(settings, "target.fps", 30)
    try:
        fps = int(fps)
        if fps <= 0:
            raise ValueError
    except Exception:
        log.warning("motion: bad fps %r, defaulting to 30", fps)
        fps = 30

    duration = util.ffprobe_duration(clip, log)
    if duration <= 0:
        log.warning("motion: could not read duration for clip%02d, returning unchanged", index)
        return clip

    ken_burns = bool(util.get(settings, "motion.ken_burns", True))
    amount = util.get(settings, "motion.ken_burns_amount", 0.08)
    try:
        amount = max(0.0, float(amount))
    except Exception:
        amount = 0.08
    direction = str(util.get(settings, "motion.ken_burns_direction", "in")).lower()

    # Base linear zoom term. When Ken Burns is off, the base stays at 1.0 and
    # only emphasis bumps move the camera.
    if ken_burns and amount > 0:
        base_term = _base_z_term(direction, amount, duration, index)
    else:
        base_term = "1"

    # Emphasis punch zooms, only when enabled and words are available.
    emphasis_term = ""
    want_emphasis = bool(util.get(settings, "motion.emphasis_zoom", True))
    if want_emphasis and words_path and Path(str(words_path)).exists():
        words = _load_words(words_path)
        if words:
            spans = _detect_emphasis(words, settings, log)
            strength = util.get(settings, "motion.emphasis_strength", 0.18)
            try:
                strength = max(0.0, float(strength))
            except Exception:
                strength = 0.18
            hold = util.parse_seconds(util.get(settings, "motion.emphasis_hold", "0.6sec"))
            emphasis_term = _emphasis_terms(spans, strength, hold)
            if emphasis_term:
                log.info("motion: clip%02d adding %d emphasis punch zooms", index, len(spans))
    elif want_emphasis:
        log.info("motion: clip%02d has no words, base Ken Burns only", index)

    if base_term == "1" and not emphasis_term:
        log.warning(
            "motion: clip%02d has no movement to apply, returning unchanged", index
        )
        return clip

    # Full per frame zoom expression. zoompan exposes the output frame counter as
    # on, so we recover seconds with on/fps and clamp the zoom to a safe range so
    # the crop never asks for more than the source can give.
    z_core = base_term + emphasis_term
    z_expr = f"max(1.001,min(2.0,{z_core}))".replace("T", f"(on/{fps})")

    # Center the zoom crop. zoompan default x and y are top left, so we offset by
    # half the leftover to keep the focal point centered through the move.
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"

    out = util.MOTION / f"clip{index:02d}.mp4"

    # zoompan total output frames must cover the whole clip. We render one output
    # frame per requested fps frame and let -frames bound the length, but the
    # cleaner route is to give zoompan a generous d via the input fps and then
    # rely on the source running out. We set d=1 and s to the canvas size, and
    # set fps inside zoompan so timing matches the z expression.
    total_frames = max(1, int(math.ceil(duration * fps)) + 2)

    zoompan = (
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d=1:s={width}x{height}:fps={fps}"
    )
    # Ensure even dimensions and a concat friendly pixel format after the move.
    vf = f"{zoompan},format=yuv420p"

    args = [
        "ffmpeg", "-y",
        "-i", clip,
        "-vf", vf,
        "-frames:v", str(total_frames),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "19",
        "-c:a", "copy",
        str(out),
    ]

    try:
        util.run_cmd(args, log)
    except Exception as exc:
        log.warning("motion: zoompan failed for clip%02d, returning unchanged: %s", index, exc)
        return clip

    if not out.exists() or out.stat().st_size < 1024:
        log.warning("motion: output for clip%02d looks empty, returning unchanged", index)
        return clip

    elapsed = time.monotonic() - start_t
    log.info("motion: done clip%02d in %.2fs -> %s", index, elapsed, out)
    return str(out)
