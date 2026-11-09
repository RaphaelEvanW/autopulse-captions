# CLI Reference — `generate_captions.py`

```
generate_captions.py INPUT [options]
```

`INPUT` = video/audio file (`.mp4 .mov .mkv .mp3 .wav ...`) **or** a words JSON exported
from the preview editor (revision mode).

## Output

| Flag | Default | Meaning |
| --- | --- | --- |
| `-o, --output-ass` | `<input>.ass` | Subtitle file |
| `--burn` | off | Burn subtitles into the video with FFmpeg |
| `--output-video` | `<input>_captioned.mp4` | Burned video path |
| `--video` | from `last_run.json` | Source video when INPUT is JSON |
| `--save-words` | – | Also save the final word list to this JSON |
| `--open-preview` | off | Open `preview_player.html` when done |

## Language

| Flag | Meaning |
| --- | --- |
| `--lang en` / `--lang id` | Force language |
| `--lang auto` | Whisper auto-detect |
| *(omitted)* | Interactive question in a terminal; auto-detect when non-interactive |
| `--translate` | Any spoken language → English captions |
| `--no-ask` | Never prompt |

## Whisper / hardware

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model` | `auto` | `large-v3`, `large-v3-turbo`, `medium`, `small`, `base` |
| `--device` | `auto` | `cuda` / `cpu` |
| `--compute-type` | `auto` | `float16`, `int8_float16`, `int8` |
| `--prompt` | from Mini-ML | Context sentence that steers spelling/style |
| `--hotwords` | from Mini-ML | Space-separated names/terms |
| `--glossary` | – | JSON `{"wrong": "right"}` applied after transcription |

`auto` selection (see `gpu_check.py`), based on **free** VRAM minus a safety margin:

| Usable VRAM | Transcribe | Translate |
| --- | --- | --- |
| ≥ ~5.1 GB | `large-v3` float16 | `large-v3` float16 |
| ≥ ~3.6 GB | `large-v3` int8_float16 | `large-v3` int8_float16 |
| ≥ ~2.2 GB (e.g. GTX 1650 Ti 4 GB) | `large-v3-turbo` int8_float16 | `medium` int8_float16 |
| smaller / no NVIDIA GPU | CPU `large-v3-turbo` int8 (RAM ≥ 8 GB) | CPU `medium` int8 |

Pascal GPUs (compute capability 6.x) use pure `int8`. If loading or decoding fails
(OOM, missing cuDNN), the script steps down the fallback chain automatically.

## Mini-ML

| Flag | Default | Meaning |
| --- | --- | --- |
| `--auto-fix-ml` | `0.80` | Auto-apply corrections with confidence ≥ value (`0` = suggest only) |
| `--learn-from` | `last_run.json` → raw words | Raw Whisper JSON to diff against in revision mode |

## Style

| Flag | Default | Meaning |
| --- | --- | --- |
| `--max-words` | `3` | Words per line |
| `--max-chars` | by orientation (22 / 32 / 24) | Characters per line |
| `--font` | `Montserrat` | Font family (files in `fonts/`) |
| `--font-size` | by orientation (63 / 58 / 56) | ASS font size |
| `--margin-v` | by orientation (460 / 110 / 150) | Distance from bottom |
| `--highlight` | `#F5B82E` | Active-word color |

Orientation presets: portrait 1080×1920, landscape 1920×1080, square 1080×1080.
