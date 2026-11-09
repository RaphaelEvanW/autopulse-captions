#!/usr/bin/env python3
"""
GoldPulse Installer
-------------------
1. Cek versi Python (faster-whisper/CTranslate2 butuh 3.9 - 3.13)
2. Cek hardware (gpu_check.py) -> tentukan model + device + compute_type
3. Install dependency yang SESUAI hardware:
     - semua mesin : faster-whisper
     - GPU NVIDIA  : + cuBLAS & cuDNN 9 untuk CUDA 12 (tanpa perlu install CUDA Toolkit)
4. Cek FFmpeg (opsional install via winget di Windows)
5. Smoke test GPU (load model 'tiny' di CUDA) -> kalau gagal, otomatis tandai pakai CPU
6. (Opsional) Download model terpilih di depan supaya run pertama tidak lama

Pemakaian (disarankan di dalam virtualenv):
    python install.py               # install lengkap sesuai hardware
    python install.py --dry-run     # lihat rencana tanpa install apa-apa
    python install.py --download-model --install-ffmpeg
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import gpu_check  # noqa: E402

REQ_BASE = ROOT / "requirements.txt"
REQ_GPU = ROOT / "requirements-gpu.txt"


def step(msg: str) -> None:
    print(f"\n==> {msg}")


def pip_install(args, dry: bool) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", *args]
    print("    $ " + " ".join(cmd))
    if dry:
        return True
    return subprocess.run(cmd).returncode == 0


def find_ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    if platform.system() == "Windows":
        import os
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        for cand in base.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
            return str(cand)
    return None


def smoke_test_cuda(compute_type: str) -> bool:
    """Load model tiny di GPU + transcribe 1 detik hening. Membuktikan cuBLAS/cuDNN benar-benar ke-load."""
    code = (
        "import sys; sys.path.insert(0, r'%s');"
        "import generate_captions as g; g.register_nvidia_dlls();"
        "import numpy as np; from faster_whisper import WhisperModel;"
        "m = WhisperModel('tiny', device='cuda', compute_type='%s');"
        "segs, _ = m.transcribe(np.zeros(16000, dtype=np.float32)); list(segs); print('CUDA_OK')"
    ) % (str(ROOT), compute_type)
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if "CUDA_OK" in res.stdout:
        return True
    tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
    print("    [!] Smoke test GPU gagal: " + " | ".join(tail))
    return False


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Installer GoldPulse Captions (menyesuaikan GPU/CPU)")
    ap.add_argument("--dry-run", action="store_true", help="Tampilkan rencana saja")
    ap.add_argument("--cpu-only", action="store_true", help="Paksa mode CPU (lewati paket CUDA)")
    ap.add_argument("--install-ffmpeg", action="store_true", help="Install FFmpeg via winget jika belum ada (Windows)")
    ap.add_argument("--download-model", action="store_true", help="Download model Whisper terpilih sekarang")
    ap.add_argument("--skip-smoke", action="store_true", help="Lewati smoke test GPU")
    args = ap.parse_args()

    # 1. Python version
    step("Cek versi Python")
    v = sys.version_info
    print(f"    Python {v.major}.{v.minor}.{v.micro} @ {sys.executable}")
    if not ((3, 9) <= (v.major, v.minor) <= (3, 13)):
        print("    [X] faster-whisper/CTranslate2 belum punya wheel untuk versi ini.")
        print("        Pakai Python 3.10 - 3.12, contoh:  py -3.11 -m venv .venv  lalu  .venv\\Scripts\\python install.py")
        sys.exit(1)
    if sys.prefix == sys.base_prefix:
        print("    [i] Tidak di dalam virtualenv. Disarankan: python -m venv .venv && .venv\\Scripts\\activate")

    # 2. Hardware
    step("Cek hardware")
    profile = gpu_check.select_profile("transcribe")
    if args.cpu_only and profile["device"] == "cuda":
        profile = gpu_check.select_profile("transcribe", gpus=[])
        profile["reason"] = "Dipaksa CPU (--cpu-only). " + profile["reason"]
    gpu_check.print_human(profile)
    use_gpu = profile["device"] == "cuda"

    # 3. Dependencies
    step("Install dependency dasar (faster-whisper)")
    if not pip_install(["-r", str(REQ_BASE)], args.dry_run):
        sys.exit("[X] Gagal install requirements.txt")

    if use_gpu:
        step("Install runtime CUDA 12 (cuBLAS + cuDNN 9) untuk GPU NVIDIA")
        if not pip_install(["-r", str(REQ_GPU)], args.dry_run):
            print("    [!] Gagal install paket CUDA -> fallback ke CPU")
            use_gpu = False
    else:
        step("Mode CPU -> paket CUDA dilewati")

    # 4. FFmpeg
    step("Cek FFmpeg (untuk burn-in subtitle)")
    ff = find_ffmpeg()
    if ff:
        print(f"    OK: {ff}")
    elif args.install_ffmpeg and platform.system() == "Windows" and not args.dry_run:
        subprocess.run(["winget", "install", "-e", "--id", "Gyan.FFmpeg",
                        "--accept-source-agreements", "--accept-package-agreements"])
        print("    [i] Selesai. generate_captions.py otomatis menemukan FFmpeg winget tanpa restart terminal.")
    else:
        print("    [!] FFmpeg belum ada. Windows: winget install Gyan.FFmpeg | macOS: brew install ffmpeg | Linux: apt install ffmpeg")

    # 5. Smoke test GPU
    if use_gpu and not args.dry_run and not args.skip_smoke:
        step("Smoke test GPU (model tiny)")
        if smoke_test_cuda(profile["compute_type"]):
            print("    OK: GPU siap dipakai Whisper")
        else:
            print("    -> Profil diturunkan ke CPU. (Cek driver NVIDIA terbaru, lalu jalankan ulang installer.)")
            profile = gpu_check.select_profile("transcribe", gpus=[])
            profile["reason"] = "Smoke test GPU gagal. " + profile["reason"]
            use_gpu = False

    # 6. Simpan profil
    if not args.dry_run:
        gpu_check.PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        print(f"\n[+] Profil hardware disimpan: {gpu_check.PROFILE_PATH.name}")

    # 7. Daftarkan sebagai Global Skill Antigravity (~/.gemini/config/skills/autopulse-captions)
    if not args.dry_run:
        try:
            global_skill_dir = Path.home() / ".gemini" / "config" / "skills" / "autopulse-captions"
            (global_skill_dir / "references").mkdir(parents=True, exist_ok=True)
            skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
            skill_text = skill_text.replace(
                r"C:\Users\Evan\.gemini\antigravity\scratch\autopulse-captions",
                str(ROOT),
            )
            (global_skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
            for ref in (ROOT / "references").glob("*.md"):
                shutil.copy2(ref, global_skill_dir / "references" / ref.name)
            print(f"[+] Global Antigravity Skill terdaftar di: {global_skill_dir}")
        except Exception as e:
            print(f"[i] Lewati pendaftaran global skill ({e}); workspace skill di .agents/skills/ tetap aktif.")

    # 8. Download model
    if args.download_model and not args.dry_run:
        step(f"Download model {profile['model']}")
        code = ("from faster_whisper.utils import download_model; "
                f"print(download_model('{profile['model']}'))")
        subprocess.run([sys.executable, "-c", code])

    print("\n" + "=" * 64)
    print(f" SELESAI  ->  Whisper: {profile['model']} / {profile['device']} / {profile['compute_type']}")
    print(" Coba     :  python generate_captions.py video.mp4 --burn")
    print("=" * 64)


if __name__ == "__main__":
    main()
