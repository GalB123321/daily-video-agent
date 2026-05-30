"""Animated caption engine for the daily video pipeline.

Two public functions form the contract with the rest of the pipeline:

  merge_words(items, settings, log) -> list
    Build one global word list with absolute timestamps from the per clip
    words.json files, offsetting each clip by the cumulative duration of all
    prior clips. When the timeline uses a non hard transition, every join
    overlaps by the transition duration, so the cumulative offset is reduced
    by that overlap to keep captions in sync with the crossfaded timeline.

  build_and_burn(video, merged_words, settings, log) -> str
    Generate an ASS subtitle file in one of three styles and burn it into the
    video with ffmpeg or libass. The styles are word_reveal, karaoke_pop, and
    lower_third, each fully driven by the captions section of settings.

Everything is config driven. Every optional step degrades gracefully: a
missing words list, a failed render, or any unexpected error logs a warning
and returns the input video unchanged so a daily run never breaks.

No dash characters are used as prose punctuation in any human readable text.
ASS override tags such as \\fad and \\t are markup, not prose, and stay as is.
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


# Fixed render resolution for the ASS canvas. The pipeline targets portrait
# 1080x1920, and PlayResX / PlayResY anchor all positions and font sizes.
PLAY_RES_X = 1080
PLAY_RES_Y = 1920

# Vertical placement (MarginV) per named position, in script pixels.
# center keeps the active word near the optical middle of a 9:16 frame.
_MARGIN_V = {
    "center": 760,
    "lower_third": 240,
    "top": 1400,
}

# ASS alignment (numpad style). 2 = bottom center, 5 = middle center,
# 8 = top center. Combined with MarginV this fixes where text sits.
_ALIGNMENT = {
    "center": 5,
    "lower_third": 2,
    "top": 8,
}


# =====================================================================
# merge_words
# =====================================================================

def merge_words(items: list, settings: dict, log) -> list:
    """Merge per clip word lists into one global, absolutely timed list.

    items is an ordered list of (clip_path, words_path_or_None). Each clip is
    offset by the cumulative duration of all prior clips. Clips with a None
    words path contribute only their duration to the running offset.

    When settings.transitions.type is not "hard", consecutive clips overlap on
    the final timeline by the transition duration. Each join after the first
    therefore pulls the running offset back by that overlap so the words stay
    aligned with the crossfaded output.

    Returns a time sorted list of {"word", "start", "end"} in seconds.
    """
    start = time.monotonic()
    log.info("merge_words: start, %d clip(s)", len(items) if items else 0)

    if not items:
        log.warning("merge_words: no items provided, returning empty list")
        return []

    transition_type = util.get(settings, "transitions.type", "hard")
    overlap = 0.0
    if transition_type and transition_type != "hard":
        overlap = util.parse_seconds(util.get(settings, "transitions.duration", 0.0))
        if overlap > 0:
            log.info(
                "merge_words: transition '%s' overlaps joins by %.3fs",
                transition_type, overlap,
            )

    merged: list[dict] = []
    offset = 0.0

    for idx, item in enumerate(items):
        try:
            clip_path, words_path = item
        except (TypeError, ValueError):
            log.warning("merge_words: malformed item at index %d, skipping", idx)
            continue

        # Every join after the first reduces the running offset by the overlap.
        if idx > 0 and overlap > 0:
            offset = max(0.0, offset - overlap)

        duration = util.ffprobe_duration(clip_path, log) if clip_path else 0.0

        if words_path:
            words = _load_words(words_path, log)
            for w in words:
                ws = float(w.get("start", 0.0)) + offset
                we = float(w.get("end", ws)) + offset
                if we < ws:
                    we = ws
                merged.append({
                    "word": str(w.get("word", "")),
                    "start": ws,
                    "end": we,
                })
            log.info(
                "merge_words: clip %02d contributed %d word(s) at offset %.3fs",
                idx + 1, len(words), offset,
            )
        else:
            log.info(
                "merge_words: clip %02d has no words, offset only (%.3fs)",
                idx + 1, offset,
            )

        offset += duration

    merged.sort(key=lambda w: w["start"])
    log.info(
        "merge_words: done in %.2fs, %d total word(s)",
        time.monotonic() - start, len(merged),
    )
    return merged


def _load_words(words_path, log) -> list[dict]:
    """Read a words.json file, returning a list of word dicts or [] on failure."""
    import json

    try:
        raw = Path(words_path).read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 a bad file must not break the run
        log.warning("merge_words: could not read words %s: %s", words_path, exc)
        return []

    if not isinstance(data, list):
        log.warning("merge_words: words file %s is not a list", words_path)
        return []

    out: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if "start" not in entry or "end" not in entry:
            continue
        out.append(entry)
    return out


# =====================================================================
# build_and_burn
# =====================================================================

def build_and_burn(video: str, merged_words: list, settings: dict, log) -> str:
    """Generate an animated ASS file and burn it into the video.

    Output is work/captioned.mp4. Audio is copied unchanged. If captions are
    disabled, there are no words, or any step fails, a warning is logged and
    the input video is returned unchanged so the pipeline keeps moving.
    """
    start = time.monotonic()
    style = util.get(settings, "captions.style", "word_reveal")
    log.info("build_and_burn: start, style=%s", style)

    if not util.get(settings, "captions.enabled", True):
        log.warning("build_and_burn: captions disabled, returning input video")
        return video

    if not merged_words:
        log.warning("build_and_burn: no words to render, returning input video")
        return video

    try:
        util.ensure_dirs()
        ass_path = util.CAPTIONS / "captions.ass"
        ass_text = _build_ass(merged_words, settings, log)
        ass_path.write_text(ass_text, encoding="utf-8")
        log.info("build_and_burn: wrote ASS to %s", ass_path)

        out_path = util.WORK / "captioned.mp4"
        escaped = _escape_filter_path(str(ass_path))
        # Prefer the dedicated ass filter (libass). Some ffmpeg builds expose
        # libass only through the subtitles filter, so fall back to that.
        filters = [f"ass='{escaped}'", f"subtitles='{escaped}'"]

        last_exc: Exception | None = None
        burned = False
        for vf in filters:
            args = [
                "ffmpeg",
                "-i", str(video),
                "-vf", vf,
                "-c:a", "copy",
                str(out_path),
                "-y",
            ]
            try:
                util.run_cmd(args, log)
                burned = True
                break
            except Exception as exc:  # noqa: BLE001 try the next filter form
                last_exc = exc
                log.warning(
                    "build_and_burn: filter '%s' did not run, trying next",
                    vf.split("=", 1)[0],
                )

        if not burned:
            raise last_exc if last_exc else RuntimeError("no caption filter ran")
    except Exception as exc:  # noqa: BLE001 burning is optional, degrade cleanly
        log.warning(
            "build_and_burn: caption burn failed (%s), returning input video",
            exc,
        )
        return video

    log.info(
        "build_and_burn: wrote %s in %.2fs",
        out_path, time.monotonic() - start,
    )
    return str(out_path)


# =====================================================================
# ASS document construction
# =====================================================================

def _build_ass(merged_words: list, settings: dict, log) -> str:
    """Assemble a complete ASS document for the configured caption style."""
    style = util.get(settings, "captions.style", "word_reveal")

    header = _build_header(settings)

    if style == "karaoke_pop":
        events = _events_karaoke_pop(merged_words, settings, log)
    elif style == "lower_third":
        events = _events_lower_third(merged_words, settings, log)
    else:
        # word_reveal is the default and the fallback for any unknown style.
        if style != "word_reveal":
            log.warning(
                "_build_ass: unknown style '%s', using word_reveal", style,
            )
        events = _events_word_reveal(merged_words, settings, log)

    return header + "\n".join(events) + "\n"


def _build_header(settings: dict) -> str:
    """Build the [Script Info], [V4+ Styles], and [Events] header lines.

    Two styles are declared: Base (primary color fill) and Hi (highlight color
    fill). Karaoke uses both. The other styles use Base and override colors
    inline where needed.
    """
    font = util.get(settings, "captions.font", "Arial Black")
    font_size = int(util.get(settings, "captions.font_size", 92))
    bold = -1 if util.get(settings, "captions.bold", True) else 0
    outline = int(util.get(settings, "captions.outline", 6))
    shadow = int(util.get(settings, "captions.shadow", 2))
    position = util.get(settings, "captions.position", "center")

    primary = util.hex_to_ass(util.get(settings, "captions.primary_color", "#FFFFFF"))
    highlight = util.hex_to_ass(util.get(settings, "captions.highlight_color", "#FFD400"))
    outline_color = util.hex_to_ass("#000000")
    back_color = util.hex_to_ass("#000000")

    align = _ALIGNMENT.get(position, 5)
    margin_v = _MARGIN_V.get(position, 760)

    # BorderStyle 1 = outline plus drop shadow (the punchy short form look).
    style_fmt = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )

    def style_line(name: str, fill: str) -> str:
        return (
            f"Style: {name},{font},{font_size},{fill},{primary},"
            f"{outline_color},{back_color},{bold},0,0,0,"
            f"100,100,0,0,1,{outline},{shadow},"
            f"{align},40,40,{margin_v},1"
        )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {PLAY_RES_X}",
        f"PlayResY: {PLAY_RES_Y}",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        style_fmt,
        style_line("Base", primary),
        style_line("Hi", highlight),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
    ]
    return "\n".join(lines) + "\n"


# =====================================================================
# Event builders, one per style
# =====================================================================

def _events_word_reveal(merged_words: list, settings: dict, log) -> list[str]:
    """One group of up to max_words on screen at a time, each animated in.

    The animation per group is taken from captions.animation: pop scales the
    group from 80 to 100 percent, fade uses a soft fade, slide eases up from
    below, and none shows the text plainly.
    """
    max_words = max(1, int(util.get(settings, "captions.max_words", 3)))
    animation = util.get(settings, "captions.animation", "pop")
    uppercase = bool(util.get(settings, "captions.uppercase", False))

    groups = _chunk_words(merged_words, max_words)
    events: list[str] = []

    for group in groups:
        g_start = group[0]["start"]
        g_end = group[-1]["end"]
        if g_end <= g_start:
            g_end = g_start + 0.2
        text = " ".join(_clean_word(w["word"], uppercase) for w in group)
        text = text.strip()
        if not text:
            continue
        anim_tag = _anim_prefix(animation, g_start, g_end)
        events.append(_event_line(g_start, g_end, "Base", anim_tag + text))

    return events


def _events_karaoke_pop(merged_words: list, settings: dict, log) -> list[str]:
    """A rolling window of up to max_words words. The spoken word pops and is

    recolored to the highlight color while its neighbours stay in the primary
    color. Each word in the window becomes the active word for its own time
    span, so the window emits one event per word with that word emphasized.
    """
    max_words = max(1, int(util.get(settings, "captions.max_words", 3)))
    uppercase = bool(util.get(settings, "captions.uppercase", False))
    strength = 1.0 + max(0.0, float(util.get(settings, "motion.emphasis_strength", 0.18)))
    # Cap the pop so karaoke stays readable even at high emphasis.
    pop_scale = int(min(140, max(100, round(strength * 100))))

    highlight = util.hex_to_ass(util.get(settings, "captions.highlight_color", "#FFD400"))

    groups = _chunk_words(merged_words, max_words)
    events: list[str] = []

    for group in groups:
        for active_idx, active in enumerate(group):
            seg_start = active["start"]
            seg_end = active["end"]
            if seg_end <= seg_start:
                seg_end = seg_start + 0.15

            parts: list[str] = []
            for i, w in enumerate(group):
                token = _clean_word(w["word"], uppercase)
                if not token:
                    continue
                if i == active_idx:
                    # Active word: recolor to highlight and pop up in scale.
                    parts.append(
                        f"{{\\c{highlight}"
                        f"\\fscx{pop_scale}\\fscy{pop_scale}}}"
                        f"{token}"
                        f"{{\\r}}"
                    )
                else:
                    parts.append(token)
            text = " ".join(parts).strip()
            if not text:
                continue
            events.append(_event_line(seg_start, seg_end, "Base", text))

    return events


def _events_lower_third(merged_words: list, settings: dict, log) -> list[str]:
    """Group words into short readable phrases that fade in near the bottom.

    Phrases are sized a little longer than the punchy max_words so the lower
    third reads as a caption rather than single popping words. Position is
    forced to the lower third regardless of the configured position.
    """
    # A few more words per line than the punchy default, but still tight.
    base_max = int(util.get(settings, "captions.max_words", 3))
    phrase_len = max(3, base_max + 2)
    uppercase = bool(util.get(settings, "captions.uppercase", False))

    groups = _chunk_words(merged_words, phrase_len)
    events: list[str] = []

    # Force lower third placement with an inline alignment and margin override.
    margin_v = _MARGIN_V["lower_third"]
    pos_tag = f"{{\\an2\\fad(180,120)}}"

    for group in groups:
        g_start = group[0]["start"]
        g_end = group[-1]["end"]
        if g_end <= g_start:
            g_end = g_start + 0.3
        text = " ".join(_clean_word(w["word"], uppercase) for w in group).strip()
        if not text:
            continue
        events.append(
            _event_line(g_start, g_end, "Base", pos_tag + text, margin_v=margin_v)
        )

    return events


# =====================================================================
# Shared helpers
# =====================================================================

def _chunk_words(words: list, size: int) -> list[list]:
    """Split the word list into consecutive chunks of at most size words."""
    chunks: list[list] = []
    current: list = []
    for w in words:
        token = str(w.get("word", "")).strip()
        if not token:
            continue
        current.append(w)
        if len(current) >= size:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _anim_prefix(animation: str, start: float, end: float) -> str:
    """Return the ASS override tag block that opens an animated event.

    pop:   scale from 80 to 100 percent over the first 120ms via \\t.
    fade:  soft fade in and out via \\fad.
    slide: ease the line up from 60px below its anchor via \\move plus \\fad.
    none:  no animation, plain text.
    """
    duration = max(0.0, end - start)
    # Keep the intro snappy but never longer than the event itself.
    intro_ms = int(min(150, max(60, duration * 1000 * 0.4)))

    if animation == "pop":
        return (
            "{\\fscx80\\fscy80"
            f"\\t(0,{intro_ms},\\fscx100\\fscy100)}}"
        )
    if animation == "fade":
        return "{\\fad(120,80)}"
    if animation == "slide":
        # Slide relies on \move which needs absolute coordinates, so it is
        # anchored to the script center. Combined with the style alignment
        # this reads as a short upward ease into place.
        cx = PLAY_RES_X // 2
        y_from = (PLAY_RES_Y // 2) + 60
        y_to = PLAY_RES_Y // 2
        end_ms = int(start * 1000)
        move_dur = intro_ms
        return (
            f"{{\\an5\\move({cx},{y_from},{cx},{y_to},0,{move_dur})"
            "\\fad(120,80)}"
        )
    # none or anything unexpected: no animation tag.
    return ""


def _clean_word(word: str, uppercase: bool) -> str:
    """Trim a word and apply uppercasing and ASS text escaping."""
    token = str(word).strip()
    if uppercase:
        token = token.upper()
    return _escape_ass_text(token)


def _escape_ass_text(text: str) -> str:
    """Escape text for the ASS event field.

    Curly braces open and close override blocks, so literal braces are
    neutralised. Backslashes are escaped, and hard newlines map to the ASS
    line break token.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\r\n", "\\N").replace("\n", "\\N")
    return text


def _event_line(start: float, end: float, style: str, text: str,
                margin_v: int = 0) -> str:
    """Format one Dialogue event line with second precision timestamps."""
    return (
        f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,"
        f"0,0,{int(margin_v)},,{text}"
    )


def _ass_time(seconds: float) -> str:
    """Convert seconds to the ASS time format H:MM:SS.cc (centisecond)."""
    if seconds < 0:
        seconds = 0.0
    total_cs = int(round(seconds * 100))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_filter_path(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument.

    The filtergraph parser treats colons as option separators and single
    quotes as string delimiters, so both are escaped along with backslashes
    and the bracket characters.
    """
    escaped = path.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("[", "\\[").replace("]", "\\]")
    return escaped
