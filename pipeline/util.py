"""Shared helpers for the daily video pipeline.

Provides ROOT relative paths, logging, manifest tracking, a subprocess
wrapper, cheap fingerprinting, idempotency checks, and macOS notifications.
Every other pipeline module imports from here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

# Repo root, derived from this file location so the project is portable.
ROOT = Path(__file__).resolve().parent.parent

# Standard working directories, all under ROOT.
WORK = ROOT / "work"
TRIMMED = WORK / "trimmed"
NORMALIZED = WORK / "normalized"
SRT_DIR = WORK / "srt"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
ASSETS = ROOT / "assets"
STATE = ROOT / "state"

MANIFEST_PATH = STATE / "manifest.json"


def ensure_dirs() -> None:
    """Create every working directory if it does not already exist."""
    for d in (WORK, TRIMMED, NORMALIZED, SRT_DIR, OUTPUT, LOGS, ASSETS, STATE):
        d.mkdir(parents=True, exist_ok=True)


def setup_logging() -> logging.Logger:
    """Configure a logger that writes to a timestamped file and to stdout."""
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS / f"run_{stamp}.log"

    logger = logging.getLogger("daily_video")
    logger.setLevel(logging.INFO)
    # Clear any handlers from a previous call in the same process.
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    logger.info("Logging to %s", log_file)
    return logger


def run_cmd(args: list[str], log: logging.Logger) -> subprocess.CompletedProcess:
    """Run a command, capture output, log it with elapsed time, raise on failure."""
    log.info("run: %s", " ".join(str(a) for a in args))
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - start
        log.error("command failed in %.2fs: %s", elapsed, " ".join(str(a) for a in args))
        if exc.stdout:
            log.error("stdout: %s", exc.stdout.strip())
        if exc.stderr:
            log.error("stderr: %s", exc.stderr.strip())
        raise
    elapsed = time.monotonic() - start
    log.info("done in %.2fs", elapsed)
    return proc


def load_manifest() -> dict:
    """Load the manifest dict from state, returning {} if missing or invalid."""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(m: dict) -> None:
    """Write the manifest dict to state as pretty JSON."""
    STATE.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=2, sort_keys=True)


def fingerprint(path) -> str:
    """Cheap content fingerprint: sha1 of size and integer mtime, no file read."""
    p = Path(path)
    stat = p.stat()
    raw = f"{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_processed(path, manifest: dict) -> bool:
    """True only if the path is in the manifest and its fingerprint is unchanged."""
    key = str(Path(path))
    entry = manifest.get(key)
    if not entry:
        return False
    try:
        return entry.get("hash") == fingerprint(path)
    except OSError:
        return False


def mark_processed(path, manifest: dict) -> None:
    """Record this path in the manifest with its current size, mtime, and hash."""
    p = Path(path)
    stat = p.stat()
    manifest[str(p)] = {
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "hash": fingerprint(p),
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }


def notify(message: str) -> None:
    """Show a macOS notification. Never raises, failures are swallowed."""
    try:
        safe = message.replace('"', "'")
        script = f'display notification "{safe}" with title "Daily Video Agent"'
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        # Notification is best effort only.
        pass


def output_path() -> Path:
    """Return the dated output file path under OUTPUT, e.g. 2026-05-30.mp4."""
    return OUTPUT / f"{date.today().isoformat()}.mp4"
