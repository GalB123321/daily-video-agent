"""Phase 4 creative step: ask Claude for advisory creative decisions.

This module shells out to the `claude -p` headless CLI and asks it to return a
small JSON object describing creative choices for the day's video, for example
clip ordering, a music mood, and free form caption notes.

Important notes:
  · This step is OPTIONAL. The config key creative_llm.enabled defaults to false,
    so this function is skipped entirely unless the operator turns it on.
  · The result is ADVISORY only. The orchestrator may use the returned hints,
    but the pipeline must run fine when this returns an empty dict.
  · Graceful degradation is mandatory: on ANY failure (CLI missing, non zero
    exit, timeout, unparseable output) this logs a warning and returns {}.
    It never raises.

Expected JSON shape from the model (all keys optional, best effort):
  {
    "order": [2, 0, 1],          # clip indices in suggested play order
    "music_mood": "upbeat",      # short mood label for music selection
    "caption_notes": "..."       # free text guidance for captions
  }
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from pipeline import util  # noqa: F401  (kept for parity with sibling modules)

# Hard ceiling so a hung CLI never stalls the whole run.
_CLAUDE_TIMEOUT_SECONDS = 120


def _build_prompt(clips: list[str]) -> str:
    """Compose the headless prompt sent to claude -p.

    We pass the clip filenames (not full paths) and their zero based indices so
    the model can reason about ordering, then ask for a strict JSON reply.
    """
    listing_lines = []
    for idx, clip in enumerate(clips):
        name = Path(clip).name
        listing_lines.append(f"  index {idx}: {name}")
    listing = "\n".join(listing_lines) if listing_lines else "  (no clips)"

    return (
        "You are a video editing assistant for a short portrait social video. "
        "Below are the normalized clips that will be combined, in their current "
        "order.\n\n"
        f"{listing}\n\n"
        "Suggest creative decisions and reply with ONLY a single JSON object, "
        "no prose, no code fences. Use these keys:\n"
        '  "order": a list of the clip indices in your suggested play order.\n'
        '  "music_mood": a short mood label, for example upbeat, calm, cinematic.\n'
        '  "caption_notes": brief free text guidance for on screen captions.\n'
        "Keep every value concise. Return valid JSON only."
    )


def _extract_json(text: str) -> dict:
    """Find and parse the first balanced {...} block in text.

    Returns the parsed dict, or {} if nothing parseable is found. Scans for the
    first opening brace, then tracks brace depth to locate its matching close,
    which is more robust than a greedy regex when the model adds stray text.
    """
    if not text:
        return {}

    start = text.find("{")
    while start != -1:
        depth = 0
        for pos in range(start, len(text)):
            ch = text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : pos + 1]
                    try:
                        parsed = json.loads(candidate)
                    except (ValueError, TypeError):
                        break  # try the next opening brace, if any
                    if isinstance(parsed, dict):
                        return parsed
                    return {}
        # No balanced block from this start; look for the next opening brace.
        start = text.find("{", start + 1)

    return {}


def creative_decisions(clips: list[str], cfg: dict, log) -> dict:
    """Ask Claude for advisory creative decisions about the day's clips.

    Args:
        clips: absolute paths of the normalized clips, in current order.
        cfg: the full loaded config dict (reads cfg["creative_llm"]).
        log: pipeline logger.

    Returns:
        A dict of creative hints, for example {"order": [...], "music_mood": ...,
        "caption_notes": ...}. Returns {} when disabled or on any failure.
    """
    start = time.monotonic()
    log.info("creative.creative_decisions: start")

    creative_cfg = (cfg or {}).get("creative_llm", {}) or {}
    if not creative_cfg.get("enabled", False):
        log.info(
            "creative.creative_decisions: disabled in config, skipping. "
            "elapsed %.2fs",
            time.monotonic() - start,
        )
        return {}

    if not clips:
        log.warning(
            "creative.creative_decisions: no clips provided, returning {}. "
            "elapsed %.2fs",
            time.monotonic() - start,
        )
        return {}

    prompt = _build_prompt(clips)
    args = ["claude", "-p", prompt]

    try:
        log.info("creative.creative_decisions: running %s", " ".join(args[:2]))
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        log.warning(
            "creative.creative_decisions: claude CLI not found, returning {}. "
            "elapsed %.2fs",
            time.monotonic() - start,
        )
        return {}
    except subprocess.TimeoutExpired:
        log.warning(
            "creative.creative_decisions: claude CLI timed out after %ss, "
            "returning {}. elapsed %.2fs",
            _CLAUDE_TIMEOUT_SECONDS,
            time.monotonic() - start,
        )
        return {}
    except Exception as exc:  # noqa: BLE001  never let this step break the run
        log.warning(
            "creative.creative_decisions: unexpected error %r, returning {}. "
            "elapsed %.2fs",
            exc,
            time.monotonic() - start,
        )
        return {}

    if proc.returncode != 0:
        log.warning(
            "creative.creative_decisions: claude exited %s, returning {}. "
            "stderr: %s. elapsed %.2fs",
            proc.returncode,
            (proc.stderr or "").strip()[:500],
            time.monotonic() - start,
        )
        return {}

    decisions = _extract_json(proc.stdout or "")
    if not decisions:
        log.warning(
            "creative.creative_decisions: no parseable JSON in output, "
            "returning {}. elapsed %.2fs",
            time.monotonic() - start,
        )
        return {}

    log.info(
        "creative.creative_decisions: done, keys %s. elapsed %.2fs",
        sorted(decisions.keys()),
        time.monotonic() - start,
    )
    return decisions
