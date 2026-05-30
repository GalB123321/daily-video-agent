#!/usr/bin/env python3
"""Daily video agent orchestrator.

Config driven short form pipeline. Discovers new clips in the watch folder,
then for each clip: trims silence, normalizes to the target portrait format,
transcribes words, and applies motion. The processed clips are assembled,
color graded, captioned, loudness normalized, mixed with music, and given an
optional intro, producing one dated portrait video in the output folder.

Every editing choice is a setting with a default (see pipeline/presets.py), so
behaviour is changed by editing config.yaml only. Every optional or AI step
degrades gracefully: on failure it logs a warning and returns its input
unchanged, so a daily run never breaks. One bad clip never aborts the run.
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import date
from pathlib import Path

from pipeline import (
    assemble,
    broll,
    captions,
    color,
    creative,
    discover,
    emphasis,  # noqa: F401  imported for completeness, used inside motion
    motion,
    music,
    normalize,
    titles,
    transcribe,
    trim,
    util,
)


def main() -> int:
    settings = util.load_config()
    util.ensure_dirs()
    log = util.setup_logging()
    run_start = time.monotonic()

    preset = util.get(settings, "preset", "punchy")
    watch_folder = util.get(settings, "watch_folder", "")
    log.info("Daily video agent starting. Preset: %s. Watch folder: %s", preset, watch_folder)

    manifest = util.load_manifest()
    clips = discover.discover(watch_folder, manifest, log)

    if not clips:
        log.info("no new clips, nothing to do")
        return 0

    log.info("discovered %d new clip(s)", len(clips))

    # Words are only needed if captions or emphasis driven zoom is on.
    need_words = bool(
        util.get(settings, "captions.enabled", True)
        or util.get(settings, "motion.emphasis_zoom", True)
    )
    log.info("word level transcription needed: %s", need_words)

    motion_enabled = bool(util.get(settings, "motion.enabled", True))

    # Each entry: (motion_clip_path, words_path_or_None, source_path)
    processed: list[tuple[str, str | None, str]] = []
    skipped = 0

    for index, src in enumerate(clips, start=1):
        try:
            log.info("processing clip %02d: %s", index, src)
            trimmed = trim.trim(src, index, settings, log)
            normalized = normalize.normalize(trimmed, index, settings, log)

            if need_words:
                tr = transcribe.transcribe(normalized, index, settings, log)
            else:
                tr = {"srt": None, "words": None}
            words_path = tr.get("words")

            if motion_enabled:
                final_clip = motion.apply_motion(
                    normalized, index, words_path, settings, log
                )
            else:
                final_clip = normalized

            processed.append((final_clip, words_path, src))
        except Exception as exc:  # noqa: BLE001  one bad clip never aborts the run
            skipped += 1
            log.error("clip %02d failed, skipping: %s", index, exc, exc_info=True)
            continue

    if not processed:
        log.error("nothing processed, all clips failed")
        return 1

    log.info("%d clip(s) processed, %d skipped", len(processed), skipped)

    # Optional creative pass. Best effort, never aborts the run.
    if util.get(settings, "creative_llm.enabled", False):
        try:
            decisions = creative.creative_decisions(
                [p[0] for p in processed], settings, log
            )
            log.info("creative decisions: %s", decisions)
        except Exception as exc:  # noqa: BLE001
            log.warning("creative step failed, ignoring: %s", exc)

    # Optional b roll generation. Best effort, never aborts the run.
    if util.get(settings, "broll.enabled", False):
        try:
            broll.generate_broll([p[0] for p in processed], settings, log)
        except Exception as exc:  # noqa: BLE001
            log.warning("b roll step failed, ignoring: %s", exc)

    # Assemble the processed clips into one timeline.
    combined = assemble.assemble([p[0] for p in processed], settings, log)

    # Color grade. Degrades to unchanged video on failure.
    if util.get(settings, "color.enabled", True):
        combined = color.grade(combined, settings, log)

    # Captions. Merge per clip words into one timeline then burn them in.
    if util.get(settings, "captions.enabled", True):
        merged = captions.merge_words(
            [(p[0], p[1]) for p in processed], settings, log
        )
        combined = captions.build_and_burn(combined, merged, settings, log)

    # Loudness normalize the speech bed. Degrades to unchanged video.
    combined = music.normalize_loudness(combined, settings, log)

    # Background music with ducking. Degrades if the track is missing.
    if util.get(settings, "audio.music.enabled", True):
        combined = music.mix_music(combined, settings, log)

    # Optional intro card. Degrades to unchanged video on failure.
    if util.get(settings, "intro.enabled", False):
        combined = titles.add_intro(combined, settings, log)

    # Write the final dated output.
    final = util.output_path()
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        final.unlink()
    shutil.copy2(combined, final)
    log.info("final video written to %s", final)

    # Mark sources processed only after a successful render, then persist once.
    for _, _, src in processed:
        try:
            util.mark_processed(src, manifest)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not mark processed %s: %s", src, exc)
    util.save_manifest(manifest)

    # Archive sources if configured. Best effort per file.
    if util.get(settings, "archive_processed", False):
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
        f"Daily video ready: {len(processed)} clip(s), {skipped} skipped. "
        f"{final.name} in {elapsed:.0f}s"
    )
    log.info(msg)
    util.notify(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
