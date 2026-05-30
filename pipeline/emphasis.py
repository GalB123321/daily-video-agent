"""Detect emphasis spans from word timings.

Pure python, no external dependencies, never raises. Given the flattened word
list produced by transcribe (each item {"word", "start", "end"} in clip local
seconds), it flags words that deserve a punchy emphasis zoom and merges
adjacent flagged words into spans.

A word is flagged when any enabled trigger fires:
  · numbers: the word contains a digit.
  · proper_nouns: the word is capitalized and is not the first word of a
                  sentence (so a normal sentence opener is not mistaken for a
                  name).
  · keywords: the word, lowercased and stripped of punctuation, is in the
              configured keyword list.
  · every_sentence_start: the word begins a sentence.

The returned list is [{"start", "end", "strength"}] sorted by start. strength
comes from settings.motion.emphasis_strength and is scaled up slightly for
spans that contain a number, since digits read as the punchiest beat.

Triggers are read from settings.motion.emphasis_triggers. Any bad input (None,
wrong types, missing keys) degrades to an empty list.
"""

from __future__ import annotations

import re

from pipeline import util

# A run of sentence ending punctuation marks the next real word as a start.
_SENTENCE_END = re.compile(r"[.!?]+[\"')\]]*\s*$")
# Strip surrounding punctuation so keyword matching is forgiving.
_STRIP = re.compile(r"^[^0-9a-zA-Z]+|[^0-9a-zA-Z]+$")

# Numbers carry the most weight, so their emphasis is scaled up a touch.
_NUMBER_SCALE = 1.25


def _clean(word: str) -> str:
    """Lowercase a token with leading and trailing punctuation removed."""
    return _STRIP.sub("", word).lower()


def _has_digit(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def _is_capitalized(word: str) -> bool:
    """True if the first letter of the token is an uppercase letter."""
    core = _STRIP.sub("", word)
    return bool(core) and core[0].isalpha() and core[0].isupper()


def detect_emphasis(words: list, settings, log) -> list:
    """Return emphasis spans for ``words``, sorted by start time.

    Never raises. Returns [] on empty or malformed input.
    """
    try:
        if not words or not isinstance(words, (list, tuple)):
            return []

        triggers = util.get(settings, "motion.emphasis_triggers", {}) or {}
        use_numbers = bool(triggers.get("numbers", True))
        use_proper = bool(triggers.get("proper_nouns", True))
        use_starts = bool(triggers.get("every_sentence_start", False))
        raw_keywords = triggers.get("keywords", []) or []
        keywords = {str(k).lower() for k in raw_keywords}

        base_strength = float(util.get(settings, "motion.emphasis_strength", 0.18))

        # First pass: decide which words are flagged and why.
        flagged: list[dict] = []   # parallel list of {start, end, is_number, hit}
        sentence_start = True      # the very first word opens a sentence

        for item in words:
            if not isinstance(item, dict):
                sentence_start = False
                continue
            token = item.get("word")
            w_start = item.get("start")
            w_end = item.get("end")
            if not isinstance(token, str) or token.strip() == "":
                sentence_start = False
                continue
            if not isinstance(w_start, (int, float)) or not isinstance(w_end, (int, float)):
                sentence_start = False
                continue

            cleaned = _clean(token)
            is_number = use_numbers and _has_digit(token)

            hit = False
            if is_number:
                hit = True
            if use_proper and not sentence_start and _is_capitalized(token):
                hit = True
            if cleaned and cleaned in keywords:
                hit = True
            if use_starts and sentence_start:
                hit = True

            flagged.append({
                "start": float(w_start),
                "end": float(w_end),
                "is_number": is_number,
                "hit": hit,
            })

            # Update sentence boundary for the next word.
            sentence_start = bool(_SENTENCE_END.search(token))

        # Second pass: merge adjacent flagged words into spans.
        spans: list[dict] = []
        current: dict | None = None
        for f in flagged:
            if not f["hit"]:
                if current is not None:
                    spans.append(current)
                    current = None
                continue
            if current is None:
                current = {
                    "start": f["start"],
                    "end": f["end"],
                    "is_number": f["is_number"],
                }
            else:
                current["end"] = max(current["end"], f["end"])
                current["is_number"] = current["is_number"] or f["is_number"]
        if current is not None:
            spans.append(current)

        # Assign strength and shape the final dicts.
        result: list[dict] = []
        for s in spans:
            strength = base_strength * (_NUMBER_SCALE if s["is_number"] else 1.0)
            result.append({
                "start": float(s["start"]),
                "end": float(s["end"]),
                "strength": float(round(strength, 4)),
            })

        result.sort(key=lambda s: s["start"])

        if log is not None:
            log.info("emphasis: %d span(s) from %d word(s)", len(result), len(words))
        return result

    except Exception as exc:  # noqa: BLE001 never break the daily run
        if log is not None:
            log.warning("emphasis: failed, returning no spans. reason: %s", exc)
        return []
