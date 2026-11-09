#!/usr/bin/env python3
"""
GoldPulse Hardware Check
------------------------
Mendeteksi GPU/CPU/RAM lalu memilih kombinasi Whisper (model + device + compute_type)
paling kuat yang MUAT di mesin ini, beserta rantai fallback kalau ternyata kehabisan VRAM.

Tanpa dependency berat (tidak butuh torch / faster-whisper), cukup `nvidia-smi` kalau ada.

Pemakaian:
    python gpu_check.py               # ringkasan yang mudah dibaca
    python gpu_check.py --json        # output JSON (dipakai installer & agent)
    python gpu_check.py --task translate
    python gpu_check.py --save        # simpan ke hardware_profile.json
"""

import argparse
import ctypes
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROFILE_PATH = Path(__file__).resolve().parent / "hardware_profile.json"

# Perkiraan kebutuhan VRAM (GB) faster-whisper / CTranslate2 saat inference (beam 5, termasuk aktivasi)
VRAM_NEED_GB = {
    ("large-v3", "float16"): 4.7,
    ("large-v3", "int8_float16"): 3.2,
    ("large-v3-turbo", "float16"): 2.6,
    ("large-v3-turbo", "int8_float16"): 1.8,
    ("medium", "int8_float16"): 1.6,
    ("small", "int8_float16"): 0.9,
    ("base", "int8_float16"): 0.5,
}

# Margin aman untuk display/Windows/browser yang ikut memakai VRAM
VRAM_SAFETY_GB = 0.4


def _run(cmd: List[str]) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def detect_nvidia_gpus() -> List[Dict[str, Any]]:
    """Baca GPU NVIDIA via nvidia-smi (nama, VRAM total/bebas dalam GB, compute capability)."""
    smi = shutil.which("nvidia-smi")
    if not smi and platform.system() == "Windows":
        cand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        if cand.exists():
            smi = str(cand)
    if not smi:
        return []

    raw = _run([smi, "--query-gpu=index,name,memory.total,memory.free,compute_cap,driver_version",
                "--format=csv,noheader,nounits"])
    if raw is None:
        # Driver lama tidak mendukung compute_cap
        raw = _run([smi, "--query-gpu=index,name,memory.total,memory.free,driver_version",
                    "--format=csv,noheader,nounits"])
        has_cc = False
    else:
        has_cc = True
    if not raw:
        return []

    gpus = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            if has_cc:
                idx, name, total, free, cc, driver = parts[:6]
            else:
                idx, name, total, free, driver = parts[:5]
                cc = "0"
            gpus.append({
                "index": int(idx),
                "name": name,
                "vram_total_gb": round(float(total) / 1024, 2),
                "vram_free_gb": round(float(free) / 1024, 2),
                "compute_capability": float(cc) if cc not in ("", "[N/A]") else 0.0,
                "driver": driver,
            })
        except (ValueError, IndexError):
            continue
    return gpus


def detect_ram_gb() -> float:
    """Total RAM sistem (GB) tanpa psutil."""
    try:
        if platform.system() == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
        if hasattr(os, "sysconf"):
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3), 1)
    except Exception:
        pass
    return 0.0


def _gpu_candidates(task: str, cc: float) -> List[tuple]:
    """Urutan kandidat dari yang paling akurat ke paling ringan, sesuai kemampuan GPU."""
    # GPU Pascal (cc 6.x) tidak efisien di float16 -> pakai int8 murni
    fp16_ok = cc >= 7.0 or cc == 0.0
    mixed = "int8_float16" if fp16_ok else "int8"
    full = "float16" if fp16_ok else "int8"

    if task == "translate":
        # large-v3-turbo TIDAK dilatih untuk translate -> jangan dipilih untuk mode terjemah
        return [("large-v3", full), ("large-v3", mixed), ("medium", mixed), ("small", mixed)]
    return [
        ("large-v3", full),
        ("large-v3", mixed),
        ("large-v3-turbo", mixed),
        ("medium", mixed),
        ("small", mixed),
        ("base", mixed),
    ]


def _need(model: str, compute: str) -> float:
    key = (model, "int8_float16" if compute == "int8" else compute)
    return VRAM_NEED_GB.get(key, 1.0)


def select_profile(task: str = "transcribe", gpus: Optional[List[Dict[str, Any]]] = None,
                   ram_gb: Optional[float] = None) -> Dict[str, Any]:
    """
    Pilih setup Whisper terbaik yang muat.
    Return: {device, model, compute_type, cpu_threads, fallback_chain, reason, gpu, ram_gb, tier}
    """
    gpus = detect_nvidia_gpus() if gpus is None else gpus
    ram_gb = detect_ram_gb() if ram_gb is None else ram_gb
    cpu_threads = max(1, (os.cpu_count() or 4) - 1)

    # CPU fallback umum (selalu jadi ujung rantai)
    if task == "translate":
        cpu_model = "medium" if ram_gb >= 8 else "small"
    else:
        cpu_model = "large-v3-turbo" if ram_gb >= 8 else ("small" if ram_gb >= 4 else "base")
    cpu_option = {"device": "cpu", "model": cpu_model, "compute_type": "int8"}

    if gpus:
        gpu = max(gpus, key=lambda g: g["vram_free_gb"])
        usable = min(gpu["vram_free_gb"], gpu["vram_total_gb"] - VRAM_SAFETY_GB) - VRAM_SAFETY_GB
        cands = _gpu_candidates(task, gpu["compute_capability"])
        fitting = [c for c in cands if _need(*c) <= usable]

        if fitting:
            best_model, best_ct = fitting[0]
            chain = [{"device": "cuda", "model": m, "compute_type": c} for (m, c) in fitting[1:3]]
            chain.append(cpu_option)
            tier = {
                "large-v3": "high",
                "large-v3-turbo": "high-lite",
                "medium": "medium",
            }.get(best_model, "low")
            return {
                "device": "cuda",
                "device_index": gpu["index"],
                "model": best_model,
                "compute_type": best_ct,
                "cpu_threads": cpu_threads,
                "fallback_chain": chain,
                "tier": tier,
                "reason": (f"{gpu['name']} ({gpu['vram_total_gb']} GB, bebas {gpu['vram_free_gb']} GB, "
                           f"cc {gpu['compute_capability']}) -> {best_model} {best_ct} "
                           f"butuh ~{_need(best_model, best_ct)} GB, muat di ~{round(usable, 1)} GB yang aman dipakai"),
                "gpu": gpu,
                "ram_gb": ram_gb,
                "task": task,
            }
        reason_prefix = f"{gpu['name']} VRAM bebas hanya ~{round(usable, 1)} GB (terlalu kecil) -> pakai CPU. "
    else:
        reason_prefix = "Tidak ada GPU NVIDIA terdeteksi -> pakai CPU. "

    return {
        "device": "cpu",
        "device_index": 0,
        "model": cpu_model,
        "compute_type": "int8",
        "cpu_threads": cpu_threads,
        "fallback_chain": [{"device": "cpu", "model": "small", "compute_type": "int8"}] if cpu_model != "small" else [],
        "tier": "cpu",
        "reason": reason_prefix + f"RAM {ram_gb} GB -> {cpu_model} int8 ({cpu_threads} thread)",
        "gpu": gpus[0] if gpus else None,
        "ram_gb": ram_gb,
        "task": task,
    }


def print_human(profile: Dict[str, Any]) -> None:
    print("=" * 64)
    print(" GoldPulse Hardware Check")
    print("=" * 64)
    gpu = profile.get("gpu")
    if gpu:
        print(f" GPU     : {gpu['name']}  |  VRAM {gpu['vram_total_gb']} GB (bebas {gpu['vram_free_gb']} GB)"
              f"  |  CC {gpu['compute_capability']}  |  Driver {gpu['driver']}")
    else:
        print(" GPU     : (tidak ada GPU NVIDIA)")
    print(f" RAM     : {profile['ram_gb']} GB   |   CPU threads dipakai: {profile['cpu_threads']}")
    print(f" Task    : {profile['task']}")
    print("-" * 64)
    print(f" PILIHAN : model={profile['model']}  device={profile['device']}  compute={profile['compute_type']}"
          f"  (tier: {profile['tier']})")
    print(f" Alasan  : {profile['reason']}")
    if profile["fallback_chain"]:
        chain = "  ->  ".join(f"{c['model']}/{c['device']}/{c['compute_type']}" for c in profile["fallback_chain"])
        print(f" Fallback: {chain}")
    print("=" * 64)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Cek GPU/CPU dan pilih model Whisper yang paling pas")
    ap.add_argument("--task", choices=["transcribe", "translate"], default="transcribe")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    ap.add_argument("--save", action="store_true", help=f"Simpan ke {PROFILE_PATH.name}")
    args = ap.parse_args()

    profile = select_profile(task=args.task)
    if args.save:
        PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(profile, indent=2))
    else:
        print_human(profile)
        if args.save:
            print(f"[+] Disimpan ke {PROFILE_PATH}")


if __name__ == "__main__":
    main()
