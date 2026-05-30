#!/usr/bin/env python3
"""Daily video agent orchestrator.

Discovers new clips in the watch folder, trims, normalizes, transcribes,
assembles, optionally adds titles, subtitles, and music, then writes one
dated portrait video into the output folder. Optional steps degrade
gracefully: a failure in any optional step logs a warning and the pipeline
continues with the input unchanged.
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import date
from pathlib import Path

import yaml

from pipeline import (
    assemble,
    broll,
    creative,
    discover,
    music,
    normalize,
    subtitles,
    titles,
    transcribe,
    trim,
)
from pipeline.util import (
    ROOT,
    ensure_dirs,
    is_processed,
    load_manifest,
    mark_processed,
    notify,
    output_path,
    save_manifest,
    setup_logging,
)


def load_config() -> dict:
    """Read config.yaml from the repo root."""
    cfg_path = ROOT / "config.yaml"
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> int:
    cfg = load_config()
    ensure_dirs()
    log = setup_logging()
    run_start = time.monotonic()

    watch_folder = cfg.get("watch_folder", "")
    target = cfg.get("target", {})
    resolution = target.get("resolution", "1080x1920")
    fps = int(target.get("fps", 30))
    trim_cfg = cfg.get("trim", {})
    margin = trim_cfg.get("margin", "0.2sec")
    edit_mode = trim_cfg.get("edit_mode", "audio")
    subtitles_cfg = cfg.get("subtitles", {})
    subtitles_enabled = bool(subtitles_cfg.get("enabled", False))
    language = subtitles_cfg.get("language", "en")
    music_cfg = cfg.get("music", {})
    titles_cfg = cfg.get("titles", {})
    broll_cfg = cfg.get("broll", {})
    creative_cfg = cfg.get("creative_llm", {})

    log.info("Daily video agent starting. Watch folder: %s", watch_folder)

    manifest = load_manifest()
    clips = discover.discover(watch_folder, manifest, log)

    if not clips:
        log.info("no new clips")
        return 0

    log.info("discovered %d new clip(s)", len(clips))

    # processed entries: (normalized_path, srt_or_None, source_path)
    processed: list[tuple[str, str | None, str]] = []
    skipped = 0

    for index, src in enumerate(clips, start=1):
        try:
            log.info("processing clip %02d: %s", index, src)
            trimmed_path = trim.trim(src, index, margin, edit_mode, log)
            normalized_path = normalize.normalize(
                trimmed_path, index, resolution, fps, log
            )
            srt_path: str | None = None
            if subtitles_enabled:
                srt_path = transcribe.transcribe(normalized_path, index, language, log)
            processed.append((normalized_path, srt_path, src))
        except Exception as exc:  # noqa: BLE001 one bad clip never aborts the run
            skipped += 1
            log.error("clip %02d failed, skipping: %s", index, exc, exc_info=True)
            continue

    if not processed:
        log.error("nothing processed")
        return 1

    # Optional creative step. Best effort, result unused for ordering for now.
    if bool(creative_cfg.get("enabled", False)):
        try:
            decisions = creative.creative_decisions(
                [p[0] for p in processed], cfg, log
            )
            log.info("creative decisions: %s", decisions)
        except Exception as exc:  # noqa: BLE001
            log.warning("creative step failed, ignoring: %s", exc)

    normalized_clips = [p[0] for p in processed]

    # Optional b roll. Best effort, prepend any generated clips.
    if bool(broll_cfg.get("enabled", False)):
        try:
            extra = broll.generate_broll(normalized_clips, cfg, log)
            if extra:
                normalized_clips = list(extra) + normalized_clips
                log.info("inserted %d b roll clip(s)", len(extra))
        except Exception as exc:  # noqa: BLE001
            log.warning("b roll step failed, ignoring: %s", exc)

    combined = assemble.assemble(normalized_clips, log)

    # Optional titles. Degrades to unchanged video on failure.
    if bool(titles_cfg.get("enabled", False)):
        combined = titles.add_titles(combined, cfg, log)

    # Optional subtitles. Merge per clip SRTs then burn them in.
    if subtitles_enabled:
        items = [(p[0], p[1]) for p in processed]
        merged = subtitles.merge_srts(items, log)
        if merged:
            combined = subtitles.burn_subtitles(combined, merged, log)
        else:
            log.warning("no merged subtitles produced, skipping burn in")

    # Optional music. Degrades to unchanged video if track missing.
    if bool(music_cfg.get("enabled", False)):
        track = music_cfg.get("track", "")
        volume = float(music_cfg.get("volume", 0.25))
        duck = bool(music_cfg.get("duck_under_speech", False))
        combined = music.mix_music(combined, track, volume, duck, log)

    final = output_path()
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        final.unlink()
    shutil.copy2(combined, final)
    log.info("final video written to %s", final)

    # Mark sources processed only after success, then persist once.
    for _, _, src in processed:
        mark_processed(src, manifest)
    save_manifest(manifest)

    # Archive sources if configured.
    if bool(cfg.get("archive_processed", False)):
        archive_dir = Path(watch_folder) / "_archived" / date.today().isoformat()
        archive_dir.mkdir(parents=True, exist_ok=True)
        for _, _, src in processed:
            try:
                dest = archive_dir / Path(src).name
                shutil.move(src, dest)
                log.info("archived %s", dest)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not archive %s: %s", src, exc)

    elapsed = time.monotonic() - run_start
    msg = (
        f"Daily video ready: {len(processed)} clips, {skipped} skipped. "
        f"{final.name} in {elapsed:.0f}s"
    )
    log.info(msg)
    notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
