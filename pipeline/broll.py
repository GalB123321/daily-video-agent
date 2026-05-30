"""Phase 4 B-roll generation via the Higgsfield MCP server.

This module produces short portrait (1080x1920) B-roll clips for the daily
video by shelling out to the Claude CLI in headless mode (claude -p "<prompt>").
The Claude instance is expected to have the Higgsfield MCP server connected so
it can generate the clips, save them to a target directory, and print the
resulting absolute file paths (one per line).

Requirements:
  · The `claude` binary must be on PATH.
  · A Higgsfield MCP server must be configured in Claude's MCP settings.

This step is optional and gracefully degrades: broll.enabled defaults to false
in config.yaml. On ANY failure (missing binary, non-zero exit, timeout, or no
usable paths in the output) this returns an empty list and never raises, so the
rest of the pipeline keeps running.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from pipeline import util

# Video extensions we accept back from the Claude CLI output.
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

# How long we allow the headless Claude run before giving up.
_TIMEOUT_SECONDS = 600


def _build_prompt(out_dir: Path, count: int, resolution: str) -> str:
    """Compose the instruction sent to the headless Claude CLI."""
    return (
        "You have the Higgsfield MCP server connected. "
        f"Generate {count} short cinematic portrait B-roll video clips at "
        f"{resolution} resolution (vertical, 30fps), each roughly 3 to 5 "
        "seconds long, suitable as filler footage between talking segments. "
        f"Save every generated clip into this exact directory: {out_dir} . "
        "After all clips are saved, print ONLY the absolute file path of each "
        "saved clip, one path per line, with no extra commentary, no markdown, "
        "and no numbering. If you cannot generate any clips, print nothing."
    )


def _parse_paths(stdout: str) -> list[str]:
    """Extract existing video file paths from raw CLI stdout."""
    found: list[str] = []
    for raw in stdout.splitlines():
        line = raw.strip().strip("'\"")
        if not line:
            continue
        candidate = Path(line)
        if candidate.suffix.lower() not in _VIDEO_EXTS:
            continue
        if not candidate.is_absolute():
            continue
        if candidate.is_file():
            resolved = str(candidate.resolve())
            if resolved not in found:
                found.append(resolved)
    return found


def generate_broll(clips: list[str], cfg: dict, log) -> list[str]:
    """Generate B-roll clips via the Higgsfield MCP server through Claude CLI.

    Returns a list of absolute paths to generated clips, or [] on any failure.
    Never raises.
    """
    start = time.time()
    log.info("broll.generate_broll: start")

    broll_cfg = (cfg or {}).get("broll", {}) or {}
    if not broll_cfg.get("enabled", False):
        log.info("broll.generate_broll: disabled in config, skipping")
        return []

    count = int(broll_cfg.get("count", 2))
    target_cfg = (cfg or {}).get("target", {}) or {}
    resolution = target_cfg.get("resolution", "1080x1920")

    claude_bin = shutil.which("claude")
    if not claude_bin:
        log.warning(
            "broll.generate_broll: claude binary not found on PATH, "
            "returning no B-roll"
        )
        return []

    out_dir = util.WORK / "broll"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("broll.generate_broll: could not create output dir: %s", exc)
        return []

    prompt = _build_prompt(out_dir, count, resolution)

    try:
        proc = util.run_cmd([claude_bin, "-p", prompt], log)
    except subprocess.TimeoutExpired:
        log.warning(
            "broll.generate_broll: claude timed out after %ss, returning no B-roll",
            _TIMEOUT_SECONDS,
        )
        return []
    except subprocess.CalledProcessError as exc:
        log.warning(
            "broll.generate_broll: claude exited non zero (%s), returning no B-roll",
            exc.returncode,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "broll.generate_broll: claude invocation failed: %s, returning no B-roll",
            exc,
        )
        return []

    stdout = getattr(proc, "stdout", "") or ""
    paths = _parse_paths(stdout)

    if not paths:
        log.warning(
            "broll.generate_broll: no usable clip paths in claude output, "
            "returning no B-roll"
        )
        return []

    elapsed = time.time() - start
    log.info(
        "broll.generate_broll: done, %d clip(s) in %.1fs",
        len(paths),
        elapsed,
    )
    return paths
