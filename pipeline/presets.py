"""Default settings and preset bundles for the daily video pipeline.

DEFAULTS holds every tunable setting with punchy short form values. The full
schema lives here so a non developer can see every knob in one place. The merge
order applied by util.load_config is: DEFAULTS first, then the chosen preset
bundle, then the user config.yaml. Later layers win.

PRESET_BUNDLES are partial overrides: each bundle only lists the keys that
differ from DEFAULTS. The "punchy" bundle is empty because DEFAULTS already
target the punchy look.

No dash characters are used as prose punctuation in comments here.
"""

from __future__ import annotations

from copy import deepcopy


# Every setting the pipeline reads, with punchy short form defaults.
DEFAULTS: dict = {
    "preset": "punchy",  # punchy | cinematic | balanced

    "watch_folder": "./input",
    "output_folder": "./output",
    "archive_processed": True,

    "target": {
        "resolution": "1080x1920",
        "fps": 30,
    },

    "cutting": {
        "remove_silence": True,
        "margin": "0.2sec",
        "edit_mode": "audio",       # audio | none
        "silence_threshold": "4%",
        "keep_pauses": False,
        "min_clip_seconds": 0.0,
    },

    "motion": {
        "enabled": True,
        "ken_burns": True,
        "ken_burns_amount": 0.08,
        "ken_burns_direction": "in",   # in | out | alternate
        "emphasis_zoom": True,
        "emphasis_strength": 0.18,
        "emphasis_hold": "0.6sec",
        "emphasis_triggers": {
            "numbers": True,
            "proper_nouns": True,
            "keywords": [
                "best", "never", "always", "free", "secret", "new",
                "most", "huge", "need", "must", "why", "how",
            ],
            "every_sentence_start": False,
        },
    },

    "transitions": {
        "type": "hard",          # hard | crossfade | dip_to_black | whip
        "duration": "0.25sec",
    },

    "captions": {
        "enabled": True,
        "language": "en",
        "style": "word_reveal",      # word_reveal | karaoke_pop | lower_third
        "font": "Arial Black",
        "font_size": 92,
        "bold": True,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFD400",
        "outline": 6,
        "shadow": 2,
        "position": "center",        # center | lower_third | top
        "max_words": 3,
        "uppercase": False,
        "animation": "pop",          # pop | fade | slide | none
    },

    "color": {
        "enabled": True,
        "look": "punch",             # punch | warm | cool | cinematic | none
        "contrast": 1.06,
        "saturation": 1.12,
        "brightness": 0.0,
        "vignette": False,
    },

    "audio": {
        "loudness_normalize": True,
        "music": {
            "enabled": True,
            "track": "./assets/music.mp3",
            "volume": 0.22,
            "loop": True,
            "duck_under_speech": True,
            "duck_amount": 0.6,
        },
    },

    "broll": {
        "enabled": False,
    },

    "creative_llm": {
        "enabled": False,
    },

    "intro": {
        "enabled": False,
        "text": "",
    },

    "notify": {
        "method": "macos_notification",
    },
}


# Partial overrides per preset. Only keys that differ from DEFAULTS appear.
# punchy is empty because DEFAULTS already encode the punchy look.
PRESET_BUNDLES: dict = {
    "punchy": {},

    "cinematic": {
        "transitions": {"type": "crossfade"},
        "motion": {
            "ken_burns_amount": 0.05,
            "ken_burns_direction": "alternate",
            "emphasis_strength": 0.10,
        },
        "cutting": {"keep_pauses": True},
        "color": {
            "look": "cinematic",
            "contrast": 1.04,
            "saturation": 1.05,
            "vignette": True,
        },
        "captions": {
            "style": "lower_third",
            "font_size": 72,
            "animation": "fade",
        },
        "audio": {"music": {"volume": 0.18}},
    },

    "balanced": {
        "transitions": {"type": "crossfade", "duration": "0.18sec"},
        "motion": {"emphasis_strength": 0.14},
        "color": {"look": "warm"},
        "captions": {"font_size": 84},
    },
}


def deep_merge(*dicts: dict) -> dict:
    """Merge dicts left to right, recursing into nested dicts. Later wins.

    Lists and scalars are replaced wholesale, not concatenated. Inputs are
    never mutated, a fresh dict is returned.
    """
    result: dict = {}
    for d in dicts:
        if not d:
            continue
        for key, value in d.items():
            existing = result.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                result[key] = deep_merge(existing, value)
            else:
                result[key] = deepcopy(value)
    return result
