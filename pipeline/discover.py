"""Discover new video files in the watch folder.

Walks the watch folder non recursively, collects video files, skips anything
already recorded as processed in the manifest, and returns the remaining
absolute paths sorted by modification time, oldest first.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from pipeline import util

# Video file extensions we accept, compared case insensitively.
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

# Subfolder name that holds already archived clips, never scanned.
ARCHIVE_DIRNAME = "_archived"


def discover(watch_folder: str, manifest: dict, log: logging.Logger) -> list[str]:
    """Return absolute paths of new video files in watch_folder.

    The scan is non recursive. The _archived subfolder and any dotfiles are
    ignored. Files already marked processed in the manifest, by path and
    matching fingerprint, are skipped. Results are sorted by mtime ascending.
    If watch_folder does not exist, a warning is logged and [] is returned.
    """
    start = time.monotonic()
    log.info("discover: scanning %s", watch_folder)

    folder = Path(watch_folder).expanduser()
    if not folder.is_dir():
        log.warning(
            "discover: watch folder does not exist, skipping: %s", watch_folder
        )
        return []

    found = 0
    skipped = 0
    candidates: list[Path] = []

    for entry in folder.iterdir():
        # Skip dotfiles and dot directories.
        if entry.name.startswith("."):
            continue
        # Skip the archive subfolder (non recursive anyway, but be explicit).
        if entry.is_dir():
            if entry.name == ARCHIVE_DIRNAME:
                log.info("discover: ignoring archive folder %s", entry)
            continue
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in VIDEO_EXTS:
            continue

        found += 1
        abs_path = str(entry.resolve())

        if util.is_processed(abs_path, manifest):
            skipped += 1
            log.info("discover: skip already processed %s", entry.name)
            continue

        candidates.append(entry)

    # Sort by modification time, oldest first, so clips keep chronological order.
    candidates.sort(key=lambda p: p.stat().st_mtime)
    result = [str(p.resolve()) for p in candidates]

    elapsed = time.monotonic() - start
    log.info(
        "discover: %d video files found, %d skipped, %d new (%.2fs)",
        found,
        skipped,
        len(result),
        elapsed,
    )
    return result
