"""Background music mixing for the daily video pipeline.

Mixes a background music track under the assembled video. Two modes:
simple amix (constant music volume) or sidechain ducking so the music
drops under speech. If the track file is missing the step degrades
gracefully and returns the input video unchanged.
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


def mix_music(video: str, track: str, volume: float, duck: bool, log) -> str:
    """Mix a background music track under the video.

    Args:
        video: path to the input video (with its own audio, e.g. speech).
        track: path to the music file. If it does not exist the step is
            skipped and the input video is returned unchanged.
        volume: music volume multiplier (for example 0.25).
        duck: when True, duck the music under speech via sidechaincompress.
            When False, mix at a constant music volume.
        log: logger from util.setup_logging.

    Returns:
        Path to work/with_music.mp4, or the unchanged input video on skip.
    """
    start = time.time()
    log.info("music.mix_music start: video=%s track=%s volume=%s duck=%s", video, track, volume, duck)

    track_path = Path(track)
    if not track_path.exists():
        log.warning(
            "music.mix_music: track file not found at %s, skipping music step", track
        )
        return video

    out = util.WORK / "with_music.mp4"

    if duck:
        # The music is compressed (ducked) whenever the speech track is loud.
        # First scale the music by the requested volume, then sidechain it
        # against the original speech audio. amix folds them back together.
        filter_complex = (
            f"[1:a]volume={volume}[bg];"
            f"[bg][0:a]sidechaincompress="
            f"threshold=0.05:ratio=8:attack=20:release=300[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first[a]"
        )
    else:
        # Constant music volume mixed under the speech track.
        filter_complex = (
            f"[1:a]volume={volume}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first[a]"
        )

    args = [
        "ffmpeg",
        "-i",
        video,
        # Loop the music input so it covers videos longer than the track.
        "-stream_loop",
        "-1",
        "-i",
        str(track_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(out),
        "-y",
    ]

    util.run_cmd(args, log)

    elapsed = time.time() - start
    log.info("music.mix_music done in %.1fs: %s", elapsed, out)
    return str(out)
