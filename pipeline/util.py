"""Shared helpers for the daily video pipeline.

Provides ROOT relative paths, logging, manifest tracking, a subprocess
wrapper, cheap fingerprinting, idempotency checks, and macOS notifications.
Every other pipeline module imports from here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

from . import presets

# Repo root, derived from this file location so the project is portable.
ROOT = Path(__file__).resolve().parent.parent

# Standard working directories, all under ROOT.
WORK = ROOT / "work"
TRIMMED = WORK / "trimmed"
NORMALIZED = WORK / "normalized"
SRT_DIR = WORK / "srt"
MOTION = WORK / "motion"
CAPTIONS = WORK / "captions"
OUTPUT = ROOT / "output"
LOGS = ROOT / "logs"
ASSETS = ROOT / "assets"
STATE = ROOT / "state"

CONFIG_PATH = ROOT / "config.yaml"
MANIFEST_PATH = STATE / "manifest.json"


def ensure_dirs() -> None:
    """Create every working directory if it does not already exist."""
    for d in (
        WORK, TRIMMED, NORMALIZED, SRT_DIR, MOTION, CAPTIONS,
        OUTPUT, LOGS, ASSETS, STATE,
    ):
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


def load_config(config_path=None) -> dict:
    """Build the merged settings dict the whole pipeline reads.

    Merge order, later layer wins: presets.DEFAULTS, then the preset bundle
    named by the user (or by DEFAULTS), then the user config.yaml. Missing or
    invalid config.yaml degrades to an empty user layer so the run still works.
    """
    path = Path(config_path) if config_path else CONFIG_PATH

    user: dict = {}
    if path.exists():
        try:
            import yaml  # lazy, keeps util importable without PyYAML

            with path.open("r", encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh)
            if isinstance(loaded, dict):
                user = loaded
        except Exception:
            # A broken config never breaks the daily run, fall back to defaults.
            user = {}

    # The preset comes from the user config if set, otherwise from DEFAULTS.
    preset_name = user.get("preset", presets.DEFAULTS.get("preset"))
    bundle = presets.PRESET_BUNDLES.get(preset_name, {})

    return presets.deep_merge(presets.DEFAULTS, bundle, user)


def get(settings: dict, dotted: str, default=None):
    """Safe nested getter. get(settings, "audio.music.volume", 0.2)."""
    node = settings
    for part in dotted.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default
    return node


def parse_seconds(v) -> float:
    """Parse a duration into float seconds.

    Accepts plain numbers (0.2), numeric strings ("0.2"), seconds strings
    ("0.2sec" or "0.2s"), and millisecond strings ("250ms"). Unparseable
    input returns 0.0.
    """
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return 0.0

    s = v.strip().lower()
    if not s:
        return 0.0

    match = re.match(r"^([0-9]*\.?[0-9]+)\s*([a-z%]*)$", s)
    if not match:
        return 0.0

    number = float(match.group(1))
    unit = match.group(2)

    if unit == "ms":
        return number / 1000.0
    # "", "s", "sec", "secs", "seconds" all mean seconds.
    return number


def ffprobe_duration(path, log: logging.Logger | None = None) -> float:
    """Return media duration in seconds via ffprobe. Returns 0.0 on failure."""
    args = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(args, check=True, capture_output=True, text=True)
        return float(proc.stdout.strip())
    except Exception:
        if log is not None:
            log.warning("ffprobe could not read duration for %s", path)
        return 0.0


def hex_to_ass(hex_color: str, alpha: int = 0) -> str:
    """Convert a #RRGGBB hex color to an ASS color string.

    ASS uses the form &HAABBGGRR where AA is alpha (00 opaque, FF transparent),
    and the channels are ordered blue, green, red. alpha is an int 0 to 255.
    """
    h = str(hex_color).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        # Fall back to opaque white on bad input.
        h = "FFFFFF"

    rr = h[0:2]
    gg = h[2:4]
    bb = h[4:6]
    aa = max(0, min(255, int(alpha)))
    return f"&H{aa:02X}{bb.upper()}{gg.upper()}{rr.upper()}"
