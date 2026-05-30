"""Audio mastering for the daily video pipeline.

Two steps, both config driven and both degrade gracefully so a daily run
never breaks:

normalize_loudness: brings the speech track to a consistent social loudness
target (I=-14, LRA=11, TP=-1.5) via the ffmpeg loudnorm filter. The video
stream is copied untouched.

mix_music: lays a background music track under the video. The music can loop
to cover long videos and can duck under speech via sidechaincompress so the
voice stays clear. If the track file is missing the step is skipped and the
input video is returned unchanged.

Settings read (from the merged config):
  audio.loudness_normalize: bool
  audio.music.enabled: bool
  audio.music.track: path string
  audio.music.volume: float (music level, e.g. 0.22)
  audio.music.loop: bool
  audio.music.duck_under_speech: bool
  audio.music.duck_amount: float (0..1, how hard to duck, e.g. 0.6)
"""

from __future__ import annotations

import time
from pathlib import Path

from pipeline import util


def normalize_loudness(video: str, settings: dict, log) -> str:
    """Normalize speech loudness to a social media target.

    Runs the ffmpeg loudnorm filter at I=-14 LRA=11 TP=-1.5, copies the
    video stream, and writes work/loudnorm.mp4. If audio.loudness_normalize
    is false the input is returned unchanged. Any failure degrades by
    returning the input video so the run continues.

    Args:
        video: path to the input video (with a speech audio track).
        settings: the merged settings dict.
        log: logger from util.setup_logging.

    Returns:
        Path to work/loudnorm.mp4, or the unchanged input on skip or failure.
    """
    start = time.time()
    enabled = bool(util.get(settings, "audio.loudness_normalize", True))
    log.info("music.normalize_loudness start: video=%s enabled=%s", video, enabled)

    if not enabled:
        log.info("music.normalize_loudness: disabled in config, returning input unchanged")
        return video

    src = Path(video)
    if not src.exists():
        log.warning(
            "music.normalize_loudness: input video not found at %s, returning input unchanged",
            video,
        )
        return video

    out = util.WORK / "loudnorm.mp4"

    # I is integrated loudness in LUFS, LRA is loudness range, TP is the true
    # peak ceiling in dBTP. These values are a solid target for phone speakers.
    args = [
        "ffmpeg",
        "-i",
        str(src),
        "-af",
        "loudnorm=I=-14:LRA=11:TP=-1.5",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(out),
        "-y",
    ]

    try:
        util.run_cmd(args, log)
    except Exception as exc:
        log.warning(
            "music.normalize_loudness: loudnorm failed (%s), returning input unchanged",
            exc,
        )
        return video

    elapsed = time.time() - start
    log.info("music.normalize_loudness done in %.1fs: %s", elapsed, out)
    return str(out)


def mix_music(video: str, settings: dict, log) -> str:
    """Mix a background music track under the video.

    Reads audio.music from the merged settings. When music is disabled or the
    track file is missing the step is skipped and the input video is returned
    unchanged. The music loops when audio.music.loop is true. When
    audio.music.duck_under_speech is true the music is ducked under the speech
    via sidechaincompress, with duck_amount controlling how hard it ducks;
    otherwise the music is mixed at a constant level. The video stream is
    copied untouched. Output is work/with_music.mp4. Any failure degrades by
    returning the input video.

    Args:
        video: path to the input video (with its own speech audio).
        settings: the merged settings dict.
        log: logger from util.setup_logging.

    Returns:
        Path to work/with_music.mp4, or the unchanged input on skip or failure.
    """
    start = time.time()

    enabled = bool(util.get(settings, "audio.music.enabled", True))
    track = util.get(settings, "audio.music.track", "")
    volume = float(util.get(settings, "audio.music.volume", 0.22))
    loop = bool(util.get(settings, "audio.music.loop", True))
    duck = bool(util.get(settings, "audio.music.duck_under_speech", True))
    duck_amount = float(util.get(settings, "audio.music.duck_amount", 0.6))

    log.info(
        "music.mix_music start: video=%s track=%s volume=%s loop=%s duck=%s duck_amount=%s",
        video, track, volume, loop, duck, duck_amount,
    )

    if not enabled:
        log.info("music.mix_music: disabled in config, returning input unchanged")
        return video

    if not track:
        log.warning("music.mix_music: no track configured, returning input unchanged")
        return video

    track_path = Path(track)
    if not track_path.is_absolute():
        # Resolve a relative track (for example "./assets/music.mp3") against
        # the repo root so the path works regardless of the working directory.
        track_path = (util.ROOT / track_path).resolve()

    if not track_path.exists():
        log.warning(
            "music.mix_music: track file not found at %s, returning input unchanged",
            track_path,
        )
        return video

    src = Path(video)
    if not src.exists():
        log.warning(
            "music.mix_music: input video not found at %s, returning input unchanged",
            video,
        )
        return video

    out = util.WORK / "with_music.mp4"

    if duck:
        # Scale the music by the requested volume, then duck it against the
        # speech with sidechaincompress. duck_amount maps to the compressor
        # ratio: 0 leaves the music flat, 1 ducks it hard. We clamp into a
        # sensible 1..20 ratio range. amix folds the speech and the ducked
        # music back into one stereo track.
        amount = max(0.0, min(1.0, duck_amount))
        ratio = 1.0 + amount * 19.0  # 0 -> 1 (no duck), 1 -> 20 (hard duck)
        filter_complex = (
            f"[1:a]volume={volume}[bg];"
            f"[bg][0:a]sidechaincompress="
            f"threshold=0.05:ratio={ratio:.2f}:attack=20:release=300[ducked];"
            f"[0:a][ducked]amix=inputs=2:duration=first[a]"
        )
    else:
        # Constant music level mixed under the speech track.
        filter_complex = (
            f"[1:a]volume={volume}[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first[a]"
        )

    args = ["ffmpeg", "-i", str(src)]
    if loop:
        # Loop the music input so it covers videos longer than the track.
        # The loop flags must precede the input they apply to.
        args += ["-stream_loop", "-1"]
    args += [
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
        # duration=first already trims, but this guards the container length.
        "-shortest",
        str(out),
        "-y",
    ]

    try:
        util.run_cmd(args, log)
    except Exception as exc:
        log.warning(
            "music.mix_music: mixing failed (%s), returning input unchanged", exc
        )
        return video

    elapsed = time.time() - start
    log.info("music.mix_music done in %.1fs: %s", elapsed, out)
    return str(out)
