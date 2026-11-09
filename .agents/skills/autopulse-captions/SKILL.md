---
name: autopulse-captions
description: >-
  Generate and burn viral-style single-line micro-chunk captions (2-3 words per
  line, active word highlighted with customizable colors/styles) onto videos in
  English or Indonesian, using faster-whisper with automatic GPU/CPU model
  selection, a self-learning Mini-ML word corrector, a browser word editor, and
  FFmpeg burn-in. Use this skill when the user asks to caption/subtitle a video,
  add TikTok/Reels/Shorts style captions, transcribe a clip with word
  timestamps, fix or revise captions, or chain captioning after a
  video-generation step.
---

# AutoPulse Captions

Pipeline: `video` → Whisper (word timestamps) → Mini-ML correction (EN/ID) →
micro-chunk `.ass` (active word highlight) → optional preview editor → FFmpeg burn-in.

Locate `PROJECT_DIR` (the folder containing `generate_captions.py` and `gpu_check.py`):
1. If `generate_captions.py` is in the same directory as this `SKILL.md`, `PROJECT_DIR` is this directory.
2. If this `SKILL.md` is inside `.agents/skills/autopulse-captions/`, `PROJECT_DIR` is the repository root (`../../..`).
3. Otherwise, default to `C:\Users\Evan\.gemini\antigravity\scratch\autopulse-captions`.

All commands below run from `PROJECT_DIR`.
Python executable (`PY`): `PROJECT_DIR/.venv/Scripts/python.exe` (Windows) or `PROJECT_DIR/.venv/bin/python` (macOS/Linux).

## 0. First-time setup (only if `.venv/` or `hardware_profile.json` is missing)

1. Check hardware first (no install needed, no network):
   `python gpu_check.py` — shows which Whisper model fits (e.g. RTX 3060 → `large-v3`,
   GTX 1650 Ti 4 GB → `large-v3-turbo`, no GPU → CPU `large-v3-turbo`/`small`).
2. Create the venv with **Python 3.10–3.12** (3.13 max; 3.14 has no CTranslate2 wheels):
   `py -3.11 -m venv .venv` (Windows) / `python3.11 -m venv .venv`.
3. Install dependencies matched to the hardware (needs network):
   `PY install.py --download-model` (add `--install-ffmpeg` on Windows if FFmpeg is missing).
   It installs CUDA 12 runtime libs only when an NVIDIA GPU is found, smoke-tests the GPU,
   and writes `hardware_profile.json`.
4. Verify: the installer ends with `SELESAI -> Whisper: <model> / <device> / <compute>`.

## 1. Ask before running (REQUIRED)

Before transcribing a new video, ALWAYS ask the user first (using `ask_question` or plain text if not specified in the prompt) — do not guess:

- **Caption language**:
  1. **English (`--lang en`)**
  2. **Bahasa Indonesia (`--lang id`)**
  3. **Indonesian audio → English captions (`--lang id --translate`)**
  4. **Auto-detect (`--lang auto`)**
- Only if unclear: whether to burn into the video (`--burn`) or generate `.ass` only.

Always pass `--lang` explicitly when the agent runs the script (the script's own
interactive prompt only appears in a real interactive terminal).

## 2. Generate captions

```bash
PY generate_captions.py "<video>" --lang en --burn --output-video "<out>.mp4"
```

- Model/device/compute default to `auto` (from `gpu_check.py`) with automatic fallback
  (e.g. `large-v3 → large-v3-turbo → CPU`) on out-of-memory or CUDA errors. Do not
  override `--model` unless the user asks.
- Orientation (portrait / landscape / square) is detected via ffprobe; font size,
  margins and line length adapt automatically.
- The first run of a model downloads it from Hugging Face (needs network).
- Outputs next to the input: `<name>.ass`, `<name>_raw_words.json`,
  `<name>_captioned.mp4`; plus `last_run.json` and an updated `preview_player.html`.

Validate: exit code 0, the log line `model dipakai: ...`, and the output video exists.
Report to the user the model used and the list of suspect words the script prints.

## 3. Review & revise (when there are suspect words or the user wants changes)

1. Open `PROJECT_DIR/preview_player.html` in the browser (`--open-preview` does it).
   The user edits words/timestamps, inserts missed words (`➕ Kata`), then clicks
   **💾 Simpan words.json** (downloads `words_revised.json`).
2. Re-render from the revised JSON — this also **trains Mini-ML** (`learned_memory.json`,
   separate `en`/`id` profiles) by diffing against the last raw Whisper output:
   ```bash
   PY generate_captions.py "<path>/words_revised.json" --burn --output-video "<out>.mp4"
   ```
   The source video is taken from `last_run.json` (override with `--video`).

## 4. Chaining after video generation

When another step produced a video (e.g. `draft.mp4`), ask the language question, then run
step 2 on that file with `--output-video final.mp4`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `No module named faster_whisper` | Use `PY` from `.venv`, or run step 0 |
| Every GPU option fails, falls to CPU | Update NVIDIA driver, rerun `PY install.py` |
| Slow on CPU | Expected (~1× realtime). Suggest `--model small` for drafts |
| `ffmpeg tidak ditemukan` | `winget install Gyan.FFmpeg` / `brew install ffmpeg` / `apt install ffmpeg` |
| Wrong words / slang / names | Use the editor (step 3) so Mini-ML learns; or `--hotwords "Name1 Name2"` |
| Translation quality poor | `--translate` never uses turbo (not trained for it); needs ≥ ~3.6 GB free VRAM for `large-v3` |

Full flag reference: [references/cli.md](references/cli.md) ·
How Mini-ML works: [references/mini-ml.md](references/mini-ml.md)
