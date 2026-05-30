# Daily Auto Editing Video Agent

A local macOS agent that runs once a day, collects new videos from a shared folder, and edits them into one finished short form video. Dead air removed, punchy zoom on key words, animated captions burned in, color graded, background music ducked under speech, optional intro card.

The whole engine is config driven. Every editing choice is a setting with a sensible default, so a non developer can change the look by editing one file. The daily job is a deterministic pipeline of command line tools. It succeeds with no language model and no paid service. Every optional or AI step degrades gracefully: if a dependency is missing it logs a warning, returns its input unchanged, and the run still finishes.

Final format: portrait 1080x1920 at 30fps.

## Editing settings (for Rony)

You change how videos look by editing `config.yaml`. You do not need to touch any code. The full menu of every setting, with a comment on each line, lives in `config.full.yaml`. Treat that file as a catalogue: find the setting you want, copy that one line and its section into `config.yaml`, and edit the value there.

### The one word preset switch

The fastest way to change the whole look is the `preset` line at the top of `config.yaml`. Set it to one word and everything else follows:

```yaml
preset: "punchy"
```

| Preset | Look |
| --- | --- |
| `punchy` | Fast cuts, big bold center captions, strong zoom, bright vivid color. The default. Made for high energy short form. |
| `cinematic` | Gentle crossfades, soft zoom, calm lower third captions, filmic graded color, a vignette, quieter music. A slower, classier feel. |
| `balanced` | A calm middle ground: soft crossfades, medium zoom, warm color, medium captions. |

How the layers stack: built in defaults sit at the bottom, the preset you pick sits on top of them, and anything you write in `config.yaml` sits on top of everything. So a value you set in `config.yaml` always wins over the preset, and the preset wins over the defaults. You can pick a preset and still override one or two single settings under it.

### Every tunable setting, in plain language

Allowed values are listed for each. Times can be written as `"0.2sec"`, `"250ms"`, or a plain number of seconds like `0.2`.

#### Cutting (how dead air is removed)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `cutting.remove_silence` | Cut out silent gaps so the video stays tight. | `true`, `false` |
| `cutting.margin` | Padding kept around speech so cuts do not clip words. | a time, e.g. `"0.2sec"` |
| `cutting.edit_mode` | How to decide what to cut. `audio` cuts by sound. `none` cuts nothing. | `audio`, `none` |
| `cutting.silence_threshold` | How quiet counts as silence. Higher cuts more aggressively. | a percent, e.g. `"4%"` |
| `cutting.keep_pauses` | Leave natural breathing room between sentences. | `true`, `false` |
| `cutting.min_clip_seconds` | Drop kept pieces shorter than this. `0` keeps all. | a number of seconds |

#### Zoom and motion (movement on the shots)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `motion.enabled` | Master switch for all movement. | `true`, `false` |
| `motion.ken_burns` | A slow continuous zoom or pan so static shots feel alive. | `true`, `false` |
| `motion.ken_burns_amount` | How strong the slow zoom is. | `0` none up to about `0.2` strong |
| `motion.ken_burns_direction` | `in` zooms in, `out` zooms out, `alternate` switches each clip. | `in`, `out`, `alternate` |
| `motion.emphasis_zoom` | A quick zoom punch on important spoken words. | `true`, `false` |
| `motion.emphasis_strength` | How hard each punch zooms. | `0` none up to about `0.3` strong |
| `motion.emphasis_hold` | How long a punch is held before easing back. | a time, e.g. `"0.6sec"` |
| `motion.emphasis_triggers.numbers` | Punch on spoken numbers like 3, 2025, 100. | `true`, `false` |
| `motion.emphasis_triggers.proper_nouns` | Punch on names of people, places, brands. | `true`, `false` |
| `motion.emphasis_triggers.keywords` | Punch whenever one of these words is spoken. | a list of words |
| `motion.emphasis_triggers.every_sentence_start` | Punch at the start of every sentence. | `true`, `false` |

Emphasis zoom needs the transcript to know which word is which, so it needs faster-whisper (see the note at the end). Without it the slow ken burns zoom still works and the punches are simply skipped.

#### Transitions (how one clip becomes the next)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `transitions.type` | The cut style between clips. `hard` is an instant cut. `crossfade` dissolves. `dip_to_black` fades through black. `whip` is a fast blur swipe. | `hard`, `crossfade`, `dip_to_black`, `whip` |
| `transitions.duration` | How long a non hard transition lasts. Ignored when type is `hard`. | a time, e.g. `"0.25sec"` |

#### Captions (the on screen words)

There are three caption styles. Pick one with `captions.style`:

| Style | The look |
| --- | --- |
| `word_reveal` | Big bold words appear one chunk at a time as they are spoken, centered. The default punchy social look. |
| `karaoke_pop` | A line sits on screen and the current word lights up in the highlight color as it is spoken, with a little pop. |
| `lower_third` | A calmer subtitle band near the bottom of the frame, like a normal film subtitle. |

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `captions.enabled` | Show captions at all. | `true`, `false` |
| `captions.language` | Spoken language code used for transcription. | e.g. `"en"` |
| `captions.style` | The caption style, see the table above. | `word_reveal`, `karaoke_pop`, `lower_third` |
| `captions.font` | Font family name installed on the machine. | e.g. `"Arial Black"` |
| `captions.font_size` | Caption text size. | a number, e.g. `92` |
| `captions.bold` | Bold the text. | `true`, `false` |
| `captions.primary_color` | Color of normal caption text. | a hex color, e.g. `"#FFFFFF"` |
| `captions.highlight_color` | Color of the currently spoken word. | a hex color, e.g. `"#FFD400"` |
| `captions.outline` | Thickness of the dark outline around letters. | a number, `0` none |
| `captions.shadow` | Drop shadow distance behind letters. | a number, `0` none |
| `captions.position` | Where captions sit. | `center`, `lower_third`, `top` |
| `captions.max_words` | Most words shown on screen at once. | a number, e.g. `3` |
| `captions.uppercase` | Force ALL CAPS captions. | `true`, `false` |
| `captions.animation` | How each caption enters. `pop` scales in, `fade` fades in, `slide` slides in, `none` is instant. | `pop`, `fade`, `slide`, `none` |

Captions need faster-whisper to turn speech into timed words (see the note at the end). Without it captions are skipped and the rest of the video is produced normally.

#### Color (the grade over the whole video)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `color.enabled` | Apply a color grade at all. | `true`, `false` |
| `color.look` | The overall look. `punch` is bright and vivid, `warm` is golden, `cool` is blue, `cinematic` is filmic, `none` applies no preset look. | `punch`, `warm`, `cool`, `cinematic`, `none` |
| `color.contrast` | `1.0` unchanged, above adds contrast, below flattens. | a number near `1.0` |
| `color.saturation` | `1.0` unchanged, above more colorful, below muted. | a number near `1.0` |
| `color.brightness` | `0.0` unchanged, positive brighter, negative darker. | a number near `0.0` |
| `color.vignette` | Darken the corners to focus the center. | `true`, `false` |

#### Audio (speech loudness and music)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `audio.loudness_normalize` | Even out speech to a consistent broadcast level. | `true`, `false` |
| `audio.music.enabled` | Mix in a background music track. | `true`, `false` |
| `audio.music.track` | Path to the music file. | a file path |
| `audio.music.volume` | Music level. Speech sits on top of it. | `0` silent to `1` full |
| `audio.music.loop` | Repeat the track to cover the whole video. | `true`, `false` |
| `audio.music.duck_under_speech` | Lower the music automatically while someone speaks. | `true`, `false` |
| `audio.music.duck_amount` | How much to lower the music when ducking. | `0` none to `1` full |

If the music file is missing the music step logs a warning and returns the video unchanged, so the run still finishes.

#### Intro (an optional title card before the video)

| Setting | What it does | Allowed values |
| --- | --- | --- |
| `intro.enabled` | Render an animated intro card before the main video. Off by default. | `true`, `false` |
| `intro.text` | The headline shown on the intro card. | any text |

The intro uses Remotion. If Remotion is not set up the intro step logs a warning and returns the video unchanged.

## Daily pipeline order

```
discover, trim, normalize, transcribe, motion zoom, assemble with transitions,
color grade, animated captions, loudness, music, intro, export
```

Per stage:

| Stage | Tool | Required | Notes |
| --- | --- | --- | --- |
| Discover | manifest scan | yes | finds new clips, skips anything already processed |
| Trim | auto-editor | yes | removes silence per the Cutting settings |
| Normalize | ffmpeg | yes | unifies resolution, fps, and audio. Mandatory before concat |
| Transcribe | faster-whisper | no | writes per clip `srt` plus a timed `words.json`. Skipped if unavailable |
| Motion zoom | ffmpeg | no | slow ken burns plus emphasis punches on key words. Degrades to the input clip |
| Assemble | ffmpeg | yes | joins clips applying the chosen Transitions |
| Color grade | ffmpeg | no | applies the Color look. Degrades to the input video |
| Captions | ffmpeg | no | burns animated captions from the merged words. Degrades to the input video |
| Loudness | ffmpeg | no | loudness normalize speech. Degrades to the input video |
| Music | ffmpeg | no | loops and ducks a background track. Degrades if the track is missing |
| Intro | Remotion | no | prepends an animated title card. Degrades to the input video |
| Export | ffmpeg | yes | writes the output as a dated file, then archives sources and notifies |

The words data is the contract between the speech aware stages. Transcribe writes `work/srt/clipNN.words.json`, a list of `{"word", "start", "end"}` in clip local seconds. Motion zoom, captions, and emphasis all read it. If transcription is unavailable that file is not written, the words path is `None` everywhere, and those stages degrade cleanly.

## One time setup

```bash
brew install ffmpeg
cd /Users/galbaumel/daily-video-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

faster-whisper downloads its speech model on the first run that needs transcription. That first run needs network access and takes a little longer. Captions and emphasis zoom both depend on it. If it is not installed the run still completes: those two features are simply skipped with a logged warning.

## Run it manually

```bash
cd /Users/galbaumel/daily-video-agent
source .venv/bin/activate
python3 run.py
```

Running twice on the same day is safe. A clip is only added to the manifest after it is processed, so a second run reprocesses nothing and simply rewrites the dated output.

## Scheduling (launchd)

```bash
bash scheduling/install.sh
```

This copies `scheduling/com.user.dailyvideo.plist` to `~/Library/LaunchAgents/` and loads it. The job fires every day at 19:00. The Mac must be awake at that time. If it is asleep the run is skipped and catches up on the next wake.

To change the hour, edit the `Hour` integer in the plist and run `bash scheduling/install.sh` again.

The watch folder may need Full Disk Access for the runner if it lives in a synced or shared location. Grant it in System Settings, Privacy and Security, Full Disk Access.

## Gotchas

Normalize is mandatory before concat because phone clips differ in size, orientation, and fps. The words timeline is recomputed into one continuous timeline when clips are merged, so captions stay in sync across the join. Provide a music file at `assets/music.mp3` or set `audio.music.enabled` to false. The launchd minimal environment needs PATH set, which the plist handles. The AI and optional steps stay off the critical path, so a missing model, a missing music track, or an unconfigured Remotion never blocks a run. Every optional stage logs a warning and returns its input unchanged.
