# Daily Auto Editing Video Agent

A local macOS agent that runs once a day, collects new videos from a shared folder, and edits them into one finished video. Dead air removed, subtitles burned in, background music mixed, optional animated titles and AI generated extras.

The daily job is a deterministic pipeline of command line tools. It succeeds with no language model and no Higgsfield. The AI features are optional add ons layered on last, behind config toggles, and they degrade gracefully so the pipeline always finishes.

## What it does

On each run the agent walks the watch folder, finds clips it has not processed before, and turns them into one dated video in the output folder. The flow:

1. Discover new clips, skip anything already in the manifest.
2. Trim dead parts from each clip with auto-editor.
3. Normalize every clip to a common resolution, frame rate, and audio rate.
4. Transcribe each clip to subtitles with faster-whisper (optional).
5. Assemble the normalized clips into one video.
6. Add an animated title card (optional, Remotion).
7. Burn merged subtitles into the video.
8. Mix a background music track (optional).
9. Export the final video to output as a dated file.
10. Update the manifest, archive the sources, send a notification.

AI insertion points, all optional and all off by default: generate b roll, and let a model make creative decisions (clip order, caption polish, music mood) before assembly.

## One time setup

```bash
brew install ffmpeg
cd /Users/galbaumel/daily-video-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

faster-whisper downloads its speech model on the first run that needs transcription. That first run needs network access and takes a little longer.

## Run it manually

```bash
cd /Users/galbaumel/daily-video-agent
source .venv/bin/activate
python3 run.py
```

Running twice on the same day is safe. A clip is only added to the manifest after it is processed, so a second run reprocesses nothing and simply rewrites the dated output.

## Daily pipeline order

```
discover, trim, normalize, transcribe, assemble, titles, subtitles, music, export, finalize
```

Per stage:

| Stage | Tool | Required | Notes |
| --- | --- | --- | --- |
| Discover | manifest scan | yes | skips clips already processed |
| Trim | auto-editor | yes | removes silence and low motion |
| Normalize | ffmpeg | yes | unifies resolution, fps, audio. Mandatory before concat |
| Transcribe | faster-whisper | no | writes per clip srt, offsets recomputed on merge |
| Assemble | ffmpeg concat | yes | safe because every clip is normalized first |
| Titles | Remotion | no | animated title card, off by default |
| Subtitles | ffmpeg | no | burns merged srt, timestamps shifted per clip |
| Music | ffmpeg | no | mixes a background track, optional ducking |
| Export | ffmpeg | yes | writes output as a dated file |
| Finalize | manifest, notify | yes | records success, archives sources, notifies |

## Configuration

All settings live in `config.yaml`.

```yaml
watch_folder: "/Users/galbaumel/Shared/photos"
output_folder: "./output"
archive_processed: true
target:
  resolution: "1080x1920"
  fps: 30
trim:
  margin: "0.2sec"
  edit_mode: "audio"
subtitles:
  enabled: true
  language: "en"
music:
  enabled: true
  track: "./assets/music.mp3"
  volume: 0.25
  duck_under_speech: false
titles:
  enabled: false
broll:
  enabled: false
creative_llm:
  enabled: false
notify:
  method: "macos_notification"
```

Toggle reference:

| Key | Default | Effect |
| --- | --- | --- |
| `subtitles.enabled` | true | transcribe clips and burn merged subtitles |
| `titles.enabled` | false | render and prepend a Remotion title card |
| `music.enabled` | true | mix the track at `music.track` under the video |
| `broll.enabled` | false | generate AI b roll and insert before assembly |
| `creative_llm.enabled` | false | let a model suggest order, captions, music mood |
| `archive_processed` | true | move sources into an archive folder after success |

## Scheduling (launchd)

```bash
bash scheduling/install.sh
```

This copies `scheduling/com.user.dailyvideo.plist` to `~/Library/LaunchAgents/` and loads it. The job fires every day at 19:00. The Mac must be awake at that time. If it is asleep the run is skipped and catches up on the next wake.

To change the hour, edit the `Hour` integer in the plist and run `bash scheduling/install.sh` again.

The watch folder may need Full Disk Access for the runner if it lives in a synced or shared location. Grant it in System Settings, Privacy and Security, Full Disk Access.

## Phase map

Phase 1, the deterministic edit, is always on. Phase 2 adds subtitles, on by default. Phase 3 adds Remotion titles, off by default. Phase 4 adds b roll plus the creative model step, both off by default, and both need the Claude CLI on PATH (the b roll step also needs the Higgsfield MCP server configured in Claude). Every AI step degrades to a no op if its dependency is missing, so a run never breaks.

## Gotchas

Normalize is mandatory before concat because phone clips differ in size, orientation, and fps. Subtitle offsets are recomputed when clips are concatenated. Provide a music file at `assets/music.mp3` or set `music.enabled` to false. The launchd minimal environment needs PATH set, which the plist handles. The AI steps cost credits and stay off the critical path, so the generated b roll and creative calls never block a run.
