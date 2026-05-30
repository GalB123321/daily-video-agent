"""Phase 3 titles step (optional, Remotion based).

Renders a short animated title card with Remotion and concatenates it in front
of the supplied video. This step is best effort: if Node, npx, Remotion, or
ffmpeg are missing, or any command fails, it logs a warning and returns the
input video path unchanged. It never raises.

The Remotion subproject lives at <repo root>/remotion and exposes a single
composition named "Title" at portrait 1080x1920, 30fps.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from pipeline import util


# Location of the Remotion subproject, relative to the repo root.
REMOTION_DIR = util.ROOT / "remotion"
# Where Remotion writes its rendered master (matches package.json render script).
TITLE_RAW = REMOTION_DIR / "work" / "title.mov"
# Normalized title clip, conformed to the main video codec and parameters.
TITLE_NORM = util.WORK / "title_norm.mp4"
# Final concatenated result.
TITLED_OUT = util.WORK / "titled.mp4"


def add_titles(video: str, cfg: dict, log) -> str:
    """Prepend an animated Remotion title card to ``video``.

    Returns the path to the new video on success, or the original ``video``
    path unchanged on any failure or if titles are disabled.
    """
    start = time.time()
    log.info("titles.add_titles start for %s", video)

    titles_cfg = (cfg or {}).get("titles", {}) or {}
    if not titles_cfg.get("enabled", False):
        log.info("titles disabled in config, returning video unchanged")
        return video

    src = Path(video)
    if not src.exists():
        log.warning("titles: input video missing, returning unchanged: %s", video)
        return video

    # Verify the toolchain is present before doing any work.
    if shutil.which("npx") is None:
        log.warning("titles: npx not found on PATH, skipping title card")
        return video
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        log.warning("titles: ffmpeg or ffprobe not found, skipping title card")
        return video
    if not REMOTION_DIR.exists():
        log.warning("titles: remotion subproject missing at %s, skipping", REMOTION_DIR)
        return video

    try:
        # 1) Render the title card via Remotion (best effort).
        rendered = _render_title_card(log)
        if rendered is None:
            log.warning("titles: render produced no output, returning video unchanged")
            return video

        # 2) Conform the title to the main video codec, resolution, and fps.
        normalized_title = _normalize_title(rendered, src, log)
        if normalized_title is None:
            log.warning("titles: could not normalize title, returning video unchanged")
            return video

        # 3) Concatenate title in FRONT of the video.
        result = _concat_front(normalized_title, src, log)
        if result is None:
            log.warning("titles: concat failed, returning video unchanged")
            return video

        log.info(
            "titles.add_titles done in %.2fs -> %s",
            time.time() - start,
            result,
        )
        return result

    except Exception as exc:  # noqa: BLE001 (graceful degradation by contract)
        log.warning("titles: unexpected failure, returning video unchanged: %s", exc)
        return video


def _render_title_card(log):
    """Run Remotion render. Returns the rendered Path or None on failure."""
    util.ensure_dirs()
    TITLE_RAW.parent.mkdir(parents=True, exist_ok=True)

    # Remotion render command. The output path is relative to the cwd, which we
    # set to the remotion subproject, so it lands at remotion/work/title.mov.
    cmd = [
        "npx",
        "remotion",
        "render",
        "src/index.ts",
        "Title",
        "work/title.mov",
    ]
    try:
        log.info("titles: rendering Remotion title card (cwd=%s)", REMOTION_DIR)
        t = time.time()
        # We do not use util.run_cmd here because we must set cwd to the
        # remotion subproject. We still log the command and elapsed time.
        log.info("RUN: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            cwd=str(REMOTION_DIR),
            check=True,
            capture_output=True,
            text=True,
        )
        log.info("titles: remotion render took %.2fs", time.time() - t)
        if proc.stderr:
            log.debug("remotion stderr: %s", proc.stderr.strip())
    except FileNotFoundError as exc:
        log.warning("titles: npx executable not found: %s", exc)
        return None
    except subprocess.CalledProcessError as exc:
        log.warning(
            "titles: remotion render failed (code %s): %s",
            exc.returncode,
            (exc.stderr or "").strip()[:500],
        )
        return None

    if not TITLE_RAW.exists() or TITLE_RAW.stat().st_size < 1024:
        log.warning("titles: rendered title missing or empty at %s", TITLE_RAW)
        return None
    return TITLE_RAW


def _probe_video_params(path: Path, log):
    """Return (width, height, fps_str) for a video using ffprobe, or None."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate",
        "-of",
        "csv=p=0:s=,",
        str(path),
    ]
    try:
        proc = util.run_cmd(cmd, log)
    except Exception as exc:  # noqa: BLE001
        log.warning("titles: ffprobe failed on %s: %s", path, exc)
        return None
    out = (proc.stdout or "").strip()
    parts = out.split(",")
    if len(parts) < 3:
        log.warning("titles: unexpected ffprobe output: %r", out)
        return None
    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        log.warning("titles: could not parse ffprobe dimensions: %r", out)
        return None
    fps = parts[2] or "30/1"
    return width, height, fps


def _normalize_title(title_raw: Path, reference: Path, log):
    """Re encode the title to match the reference video. Returns Path or None."""
    params = _probe_video_params(reference, log)
    if params is None:
        # Fall back to portrait defaults from the contract.
        width, height, fps = 1080, 1920, "30/1"
        log.info("titles: using default 1080x1920 30fps for title normalization")
    else:
        width, height, fps = params

    TITLE_NORM.parent.mkdir(parents=True, exist_ok=True)
    # Scale and pad to the reference frame, set fps, encode H.264 + AAC silent
    # audio so the concat demuxer can join it with the speech video cleanly.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},format=yuv420p"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(title_raw),
        "-f",
        "lavfi",
        "-t",
        "3",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf",
        vf,
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(TITLE_NORM),
    ]
    try:
        util.run_cmd(cmd, log)
    except Exception as exc:  # noqa: BLE001
        log.warning("titles: title normalization failed: %s", exc)
        return None
    if not TITLE_NORM.exists() or TITLE_NORM.stat().st_size < 1024:
        log.warning("titles: normalized title missing or empty")
        return None
    return TITLE_NORM


def _concat_front(title_norm: Path, video: Path, log):
    """Concatenate ``title_norm`` in front of ``video``. Returns Path or None.

    Uses re encoding via the concat filter to tolerate any residual codec or
    parameter mismatch between the title and the main video.
    """
    TITLED_OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(title_norm),
        "-i",
        str(video),
        "-filter_complex",
        "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(TITLED_OUT),
    ]
    try:
        util.run_cmd(cmd, log)
    except Exception as exc:  # noqa: BLE001
        log.warning("titles: concat in front failed: %s", exc)
        return None
    if not TITLED_OUT.exists() or TITLED_OUT.stat().st_size < 1024:
        log.warning("titles: concat output missing or empty")
        return None
    return str(TITLED_OUT)
