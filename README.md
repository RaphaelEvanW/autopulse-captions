# ✨ AutoPulse Captions — AI Micro-Chunk Auto-Captioner + Mini-ML (EN & ID)

Automated video captioning engine & **Antigravity Agent Skill** (`autopulse-captions`) that generates **Single-Line Micro-Chunk (`2–3 words`)** subtitles with **Active Word Highlight** (default `GoldPulse` preset `#FFFFFF` ➔ `#F5B82E`, or any custom `--highlight #RRGGBB` color & style), powered by `faster-whisper`, a self-learning **Contextual N-Gram & Phonetic Mini-ML** corrector, an interactive browser Word Inspector, and **FFmpeg** burn-in.

---

## 🔥 Key Features

1. **GoldPulse Visual Style (`.ass` + FFmpeg Burn-in)**
   - **Single-Line Micro-Chunking:** Groups speech into `2–3` words per line (`max_chars: 22` on 9:16 vertical video) with natural clause & pause detection (`> 0.35s`).
   - **Active Word Highlight:** Words stay crisp white (`#FFFFFF`) and light up in Warm Gold (`#F5B82E`) the exact millisecond they are spoken, with zero flicker between words.
   - **Auto-Orientation:** Automatically detects **Portrait (`9:16`)**, **Landscape (`16:9`)**, or **Square (`1:1`)** via `ffprobe` and scales font size & vertical margins accordingly.

2. **Smart Hardware Auto-Detection (`gpu_check.py`)**
   - Automatically checks your NVIDIA GPU free VRAM, Compute Capability, and system RAM before loading Whisper, picking the strongest model that safely fits your hardware with an automatic OOM fallback chain:

   | Hardware Tier | Free VRAM | Transcribe Model (`auto`) | Translate Model (`auto`) |
   | :--- | :--- | :--- | :--- |
   | **High-End GPU** (e.g. RTX 3060 12GB+) | `>= 5.1 GB` | `large-v3` (`float16`) | `large-v3` (`float16`) |
   | **Mid GPU** (6GB – 8GB VRAM) | `>= 3.6 GB` | `large-v3` (`int8_float16`) | `large-v3` (`int8_float16`) |
   | **Entry GPU 4GB** (e.g. **GTX 1650 Ti 4GB**) | `>= 2.2 GB` | **`large-v3-turbo` (`int8_float16`)** | `medium` (`int8_float16`) |
   | **CPU Fallback** (No NVIDIA GPU / Low VRAM) | `< 2.2 GB` | `large-v3-turbo` (`int8` on CPU) | `medium` (`int8` on CPU) |

3. **Dual-Language (`EN` & `ID`) + Interactive Language Prompt**
   - Before transcribing in terminal mode, the script asks whether you want **English (`en`)**, **Bahasa Indonesia (`id`)**, **Indonesian Audio ➔ English Captions (`--translate`)**, or **Auto-Detect**.
   - Separate Mini-ML memory compartments (`profiles.en` and `profiles.id`) ensure Indonesian slang rules never collide with English words.

4. **Self-Learning Mini-ML (`learned_memory.json`) & Browser Word Inspector (`preview_player.html`)**
   - Flags low-confidence Whisper words, phonetic slang mishearings, contractions, and stutters.
   - Edit words, adjust start/end timestamps (`0.05s` step), or insert missed words (`➕ Kata`) directly in `preview_player.html`.
   - Every time you re-burn from your revised `words_revised.json`, `train_mini_ml()` compares Whisper's raw output with your edits and updates `learned_memory.json` (Unigram, Bigram/Trigram context, Phonetic Skeleton, and Whisper `hotwords` injection).

---

## ⚙️ Requirements & Installation

### Prerequisites
- **Python 3.10 – 3.12** *(CTranslate2 / `faster-whisper` requires Python <= 3.13)*
- **FFmpeg** (with `libass` support for subtitle burn-in):
  ```powershell
  # Windows (Winget)
  winget install Gyan.FFmpeg
  ```

### One-Command Hardware-Aware Setup
Clone this repository and run `install.py` inside a virtual environment. It automatically checks your GPU via `gpu_check.py`, installs `faster-whisper`, and—if an NVIDIA GPU is detected—installs the CUDA 12 `cuBLAS` & `cuDNN 9` wheel DLLs (no manual 4 GB CUDA Toolkit installation required):

```powershell
# 1. Create and activate a Python 3.11 virtual environment
py -3.11 -m venv .venv
.\.venv\Scripts\activate

# 2. Run the smart installer (detects GPU, installs matching deps, smoke-tests CUDA)
python install.py --download-model
```

To inspect your hardware profile at any time without installing anything:
```powershell
python gpu_check.py
```

---

## 🚀 Usage

### 1. Generate & Burn Captions (Interactive Language Prompt)
```powershell
python generate_captions.py my_video.mp4 --burn
```
*(If `--lang` is omitted, the CLI will ask you to choose `[1] English`, `[2] Bahasa Indonesia`, `[3] Translate ID -> EN`, or `[4] Auto-detect` before running.)*

### 2. Explicit Language Commands (Scripted / Agent Mode)
```powershell
# English video
python generate_captions.py my_video.mp4 --lang en --burn

# Indonesian casual/slang video
python generate_captions.py my_video.mp4 --lang id --burn

# Indonesian audio -> translated into English captions
python generate_captions.py my_video.mp4 --lang id --translate --burn
```

### 3. Revise in Browser & Train Mini-ML
1. Open [`preview_player.html`](./preview_player.html) in your browser (or pass `--open-preview`).
2. Click any word's timestamp to jump the video, edit text/timings, or click **➕ Kata** to insert missed words.
3. Click **💾 Simpan `words.json`** to download `words_revised.json`.
4. Run:
   ```powershell
   python generate_captions.py words_revised.json --burn --output-video final_revised.mp4
   ```
   The script automatically diffs `words_revised.json` against the raw Whisper snapshot, updates [`learned_memory.json`](./learned_memory.json), regenerates the `.ass` file, and burns the final video.

---

## 🤖 Using as an Antigravity Agent Skill

This repository is structured as a ready-to-use **Antigravity Skill** (`autopulse-captions`):
- **Workspace Skill:** Included at `.agents/skills/autopulse-captions/SKILL.md` (automatically discovered when working inside this repository).
- **Global Skill (All Workspaces):** Automatically installed to `~/.gemini/config/skills/autopulse-captions/` when you run `python install.py`.

When triggered in Antigravity, the agent will:
1. Ask you whether the caption should be **English (`en`)**, **Indonesian (`id`)**, or **Translated (`id -> en`)** before running.
2. Automatically select the right Whisper model for your GPU via `gpu_check.py`.
3. Chain seamlessly after video-generation tasks (`draft.mp4` ➔ `final_captioned.mp4`).

---

## 📁 Project Structure

```text
autopulse-captions/
├── SKILL.md                  # Antigravity Skill definition & agent runbook
├── README.md                 # Human documentation & setup guide
├── generate_captions.py      # Main CLI (Whisper + Mini-ML + ASS generator + FFmpeg burn)
├── gpu_check.py              # Zero-dependency GPU/VRAM profiler & model selector
├── install.py                # Hardware-aware dependency installer & CUDA smoke tester
├── requirements.txt          # Base dependencies (CPU & GPU)
├── requirements-gpu.txt      # NVIDIA CUDA 12 runtime wheels (cuBLAS + cuDNN 9)
├── preset_style.ass          # Standalone ASS style template
├── preview_player.html       # Interactive Live Preview & Word Inspector Web UI
├── learned_memory.json       # Self-learning Mini-ML database (EN & ID profiles)
├── glossary.example.json     # Example static glossary override
├── fonts/                    # Bundled Montserrat Bold & ExtraBold fonts
├── references/               # Detailed CLI & Mini-ML reference docs for the agent
│   ├── cli.md
│   └── mini-ml.md
└── .agents/
    └── skills/
        └── autopulse-captions/
            └── SKILL.md      # Workspace skill discovery entrypoint
```
