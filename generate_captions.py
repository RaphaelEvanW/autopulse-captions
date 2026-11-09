#!/usr/bin/env python3
"""
Auto-Caption Generator - GoldPulse Style (Micro-Chunk Active Word Highlight)
----------------------------------------------------------------------------
Mengubah video/audio menjadi subtitle .ass dengan:
- Pemenggalan pendek 2-3 kata per baris (1 baris saja / Single-Line Micro-Chunk)
- Pembersihan tanda baca otomatis dengan deteksi jeda klausa
- Efek Active Word Highlight (Putih #FFFFFF -> Kuning Emas #F5B82E per kata)
- Opsi burn-in langsung ke video via FFmpeg
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
LAST_RUN_PATH = SCRIPT_DIR / "last_run.json"


def register_nvidia_dlls() -> None:
    """
    Windows: daftarkan folder DLL cuBLAS/cuDNN dari paket pip `nvidia-*-cu12` (dan torch bila ada)
    supaya CTranslate2 bisa memakai GPU tanpa install CUDA Toolkit manual.
    """
    if os.name != "nt":
        return
    import site
    roots = list(site.getsitepackages()) if hasattr(site, "getsitepackages") else []
    user_site = site.getusersitepackages() if hasattr(site, "getusersitepackages") else None
    if user_site:
        roots.append(user_site)
    dll_dirs = []
    for root in roots:
        base = Path(root)
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin", "torch/lib"):
            d = base / sub
            if d.is_dir():
                dll_dirs.append(str(d))
    for d in dll_dirs:
        try:
            os.add_dll_directory(d)
        except (OSError, AttributeError):
            pass
    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs + [os.environ.get("PATH", "")])

# Konfigurasi Preset Visual (Format Warna ASS menggunakan BGR: &HBBGGRR&)
PRESET_CONFIG = {
    "play_res_x": 1080,
    "play_res_y": 1920,
    "font_name": "Montserrat",       # Bisa diganti Rubik, Proxima Nova, atau Arial
    "font_size": 63,                 # Dikecilkan (~3px pada skala preview / -9pt pada skala 1080p)
    "inactive_color": "&HFFFFFF&",   # Putih (#FFFFFF)
    "active_color": "&H2EB8F5&",     # Kuning Emas (#F5B82E -> BGR: 2E B8 F5)
    "outline_color": "&H00000000",   # Hitam solid
    "shadow_color": "&H96000000",    # Hitam semi-transparan untuk drop shadow
    "outline_width": 3.6,
    "shadow_depth": 3.2,
    "margin_v": 460,                 # Dikebawahkan (~24% dari bawah layar)
    "max_words_per_chunk": 3,        # Maksimal 2-3 kata per layar
    "max_chars_per_chunk": 22,       # Jaga agar tetap 1 baris pendek (muat 'scared to disappoint')
    "pause_threshold_sec": 0.35,     # Pisah baris baru jika ada jeda bicara > 0.35 detik
}


def has_clause_break(raw_word: str) -> bool:
    """Cek apakah kata mentah diakhiri tanda baca jeda kalimat (.,!?;:)."""
    return bool(re.search(r'[.,!?;:]\s*$', raw_word.strip()))


def clean_word(word: str) -> str:
    """
    Membersihkan tanda baca seperti titik, koma, tanda tanya, tanda seru di ujung kata
    namun tetap mempertahankan apostrof (I've), tanda bintang sensor (bangs*t), dan spasi internal.
    """
    cleaned = word.strip()
    cleaned = re.sub(r'^[^\w\'*]+|[^\w\'*]+$', '', cleaned)
    return cleaned


def format_ass_time(seconds: float) -> str:
    """Konversi detik (float) ke format timestamp ASS (H:MM:SS.cc)."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    if centiseconds >= 100:
        secs += 1
        centiseconds -= 100
    if secs >= 60:
        minutes += 1
        secs -= 60
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


# Kamus pola salah dengar dasar Bahasa Indonesia (Phonetic Slang & Whisper Self-Censorship)
SLANG_SUSPECT_MAP = {
    "mangg": ("manggil", "Kata terpotong di akhir"),
    "manggel": ("manggil", "Pelesetan fonetik 'manggil'"),
    "manggila": ("manggil", "Halusinasi vokal akhir Whisper"),
    "mangil": ("manggil", "Kurang huruf 'g'"),
    "mangel": ("manggil", "Pelesetan fonetik 'manggil'"),
    "mangglenya": ("manggilnya", "Typo fonetik 'manggilnya'"),
    "panggel": ("panggil", "Pelesetan fonetik 'panggil'"),
    "panggal": ("panggil", "Pelesetan fonetik 'panggil'"),
    "panggila": ("panggilnya", "Salah dengar 'panggilnya'"),
    "umar": ("umur", "Salah dengar 'umur'"),
    "gopiak": ("Gobl*k", "Sensor otomatis Whisper untuk 'Gobl*k'"),
    "bangsa": ("Bangs*t", "Sensor otomatis Whisper untuk 'Bangs*t'"),
    "bangso": ("Bangs*t", "Sensor otomatis Whisper untuk 'Bangs*t'"),
    "brevi": ("Bravy", "Ejaan nama 'Bravy'"),
    "vicong": ("viconk", "Ejaan nama 'viconk'"),
}

# Kamus pola salah dengar / kontraksi kasual Bahasa Inggris (Storytelling / Reels / TikTok)
EN_SUSPECT_MAP = {
    "im": ("I'm", "Kontraksi kurang apostrof"),
    "ive": ("I've", "Kontraksi kurang apostrof"),
    "id": ("I'd", "Kontraksi kurang apostrof"),
    "dont": ("don't", "Kontraksi kurang apostrof"),
    "cant": ("can't", "Kontraksi kurang apostrof"),
    "wont": ("won't", "Kontraksi kurang apostrof"),
    "didnt": ("didn't", "Kontraksi kurang apostrof"),
    "doesnt": ("doesn't", "Kontraksi kurang apostrof"),
    "isnt": ("isn't", "Kontraksi kurang apostrof"),
    "arent": ("aren't", "Kontraksi kurang apostrof"),
    "wasnt": ("wasn't", "Kontraksi kurang apostrof"),
    "werent": ("weren't", "Kontraksi kurang apostrof"),
    "aint": ("ain't", "Kontraksi kurang apostrof"),
    "yall": ("y'all", "Kontraksi kurang apostrof"),
}

EN_COMMON_MARKERS = {
    "the", "and", "to", "of", "in", "is", "that", "it", "for", "you", "was", "with",
    "on", "have", "but", "not", "they", "this", "from", "by", "about", "into", "when",
    "what", "your", "their", "would", "could", "should", "because", "people", "never",
    "always", "really", "actually", "something", "everything", "nothing", "scared",
    "disappoint", "brother", "father", "mother", "family", "money", "world", "life",
    "i'm", "i've", "don't", "can't", "didn't", "it's", "that's", "there's", "we're",
}

ID_COMMON_MARKERS = {
    "yang", "di", "dan", "ini", "itu", "dengan", "untuk", "tidak", "dari", "dalam",
    "akan", "pada", "juga", "saya", "aku", "kamu", "dia", "mereka", "kita", "kami",
    "gue", "gua", "lo", "lu", "kalo", "kalau", "gak", "ga", "nggak", "ngga", "kagak",
    "aja", "sama", "udah", "sudah", "belum", "bisa", "ada", "jadi", "kayak", "banget",
    "manggil", "panggil", "umur", "tahun", "bang", "mas", "om", "anjing", "bangsat",
}

DEFAULT_MEMORY_PATH = Path(__file__).resolve().parent / "learned_memory.json"


def detect_transcript_language(words: List[Dict[str, Any]], explicit_lang: Optional[str] = None) -> str:
    """
    Deteksi otomatis apakah transkrip dominan Bahasa Inggris ('en') atau Bahasa Indonesia ('id').
    Jika explicit_lang ('en' / 'id') diberikan, gunakan langsung.
    """
    if explicit_lang and explicit_lang.lower() in ("en", "id"):
        return explicit_lang.lower()
    en_score = 0
    id_score = 0
    for w in words:
        low = w.get("word", "").strip().lower()
        if low in EN_COMMON_MARKERS:
            en_score += 1
        if low in ID_COMMON_MARKERS:
            id_score += 1
    return "en" if en_score > id_score else "id"


def phonetic_skeleton(word: str) -> str:
    """
    Normalisasi fonetik bahasa Indonesia/gaul agar kata yang terdengar mirip
    memiliki kerangka bunyi yang sama (contoh: 'brevi'/'brefi'/'bravy' -> 'brpi',
    'vicong'/'viconk'/'pikong' -> 'pikng', 'gopiak'/'goblok' -> 'gplk').
    """
    s = word.lower().strip()
    # Ganti tanda bintang sensor dengan vokal generik atau kosongkan untuk pencocokan bunyi
    s = re.sub(r"[^a-z0-9]", "", s)
    s = s.replace("ng", "N").replace("ny", "Y").replace("sy", "s").replace("kh", "k")
    s = s.translate(str.maketrans({
        "v": "p", "f": "p",
        "c": "k", "q": "k",
        "z": "s", "j": "c",
        "y": "i",
        "o": "u", "e": "a",
    }))
    # Hilangkan huruf kembar berurutan
    s = re.sub(r"(.)\1+", r"\1", s)
    return s.lower()


def phonetic_similarity(w1: str, w2: str) -> float:
    """Hitung skor kemiripan gabungan (ejaan + kerangka fonetik Indonesia) 0.0 - 1.0."""
    a, b = w1.lower().strip(), w2.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    raw_ratio = difflib.SequenceMatcher(None, a, b).ratio()
    skel_a, skel_b = phonetic_skeleton(a), phonetic_skeleton(b)
    skel_ratio = difflib.SequenceMatcher(None, skel_a, skel_b).ratio() if (skel_a and skel_b) else raw_ratio
    return round(0.45 * raw_ratio + 0.55 * skel_ratio, 3)


def _empty_lang_profile() -> Dict[str, Any]:
    return {
        "total_revisions_trained": 0,
        "unigram_counts": {},
        "next_context": {},
        "prev_context": {},
        "trigram_context": {},
        "custom_vocab": {},
        "approved_words": [],
    }


def load_learned_memory(memory_path: Path = DEFAULT_MEMORY_PATH) -> Dict[str, Any]:
    """
    Muat memori pembelajaran Mini-ML dari learned_memory.json.
    Mendukung multi-bahasa ('id' dan 'en' terpisah di dalam 'profiles')
    agar aturan slang Indonesia tidak bentrok dengan kata Bahasa Inggris (mis. 'main').
    """
    if memory_path.exists():
        try:
            data = json.loads(memory_path.read_text(encoding="utf-8"))
            if "profiles" not in data:
                # Migrasi otomatis memori lama ke profil 'id'
                id_prof = {
                    "total_revisions_trained": data.get("total_revisions_trained", 0),
                    "unigram_counts": data.get("unigram_counts", {}),
                    "next_context": data.get("next_context", {}),
                    "prev_context": data.get("prev_context", {}),
                    "trigram_context": data.get("trigram_context", {}),
                    "custom_vocab": data.get("custom_vocab", {}),
                    "approved_words": data.get("approved_words", []),
                }
                data["profiles"] = {"id": id_prof, "en": _empty_lang_profile()}
                data["active_lang"] = "id"
            return data
        except Exception:
            pass
    return {
        "version": 2,
        "active_lang": "id",
        "total_revisions_trained": 0,
        "unigram_counts": {},
        "next_context": {},
        "prev_context": {},
        "trigram_context": {},
        "custom_vocab": {},
        "approved_words": [],
        "profiles": {
            "id": _empty_lang_profile(),
            "en": _empty_lang_profile(),
        },
    }


def get_lang_profile(memory: Dict[str, Any], lang: str = "id") -> Dict[str, Any]:
    """Ambil profil Mini-ML spesifik bahasa ('id' atau 'en')."""
    lang_key = "en" if (lang and lang.lower().startswith("en")) else "id"
    profiles = memory.setdefault("profiles", {})
    if lang_key not in profiles:
        profiles[lang_key] = _empty_lang_profile()
    return profiles[lang_key]


def sync_top_level_memory(memory: Dict[str, Any], lang: str = "id") -> None:
    """Sinkronkan profil bahasa aktif ke root JSON agar preview_player.html langsung membacanya."""
    lang_key = "en" if (lang and lang.lower().startswith("en")) else "id"
    prof = get_lang_profile(memory, lang_key)
    memory["active_lang"] = lang_key
    for k in ("total_revisions_trained", "unigram_counts", "next_context", "prev_context", "trigram_context", "custom_vocab", "approved_words"):
        memory[k] = prof.get(k, {} if k != "approved_words" else [])


def save_learned_memory(memory: Dict[str, Any], memory_path: Path = DEFAULT_MEMORY_PATH) -> None:
    """Simpan memori pembelajaran Mini-ML ke learned_memory.json."""
    memory_path.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")


def train_mini_ml(
    raw_words: List[Dict[str, Any]],
    revised_words: List[Dict[str, Any]],
    memory_path: Path = DEFAULT_MEMORY_PATH,
    lang: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Melatih Mini-ML dengan membandingkan hasil mentah Whisper (raw_words) dan
    hasil revisi user (revised_words) berdasarkan penyelarasan timestamp.
    Otomatis mendeteksi bahasa ('id' vs 'en') dan menyimpan ke kompartemen bahasa yang sesuai.
    """
    memory = load_learned_memory(memory_path)
    detected_lang = detect_transcript_language(revised_words, explicit_lang=lang)
    prof = get_lang_profile(memory, detected_lang)

    unigram = prof.setdefault("unigram_counts", {})
    next_ctx = prof.setdefault("next_context", {})
    prev_ctx = prof.setdefault("prev_context", {})
    tri_ctx = prof.setdefault("trigram_context", {})
    vocab = prof.setdefault("custom_vocab", {})
    approved = set(prof.get("approved_words", []))

    learned_logs: List[str] = []

    # 1. Catat seluruh kosakata unik/penting dari revisi user (nama kapital, sensor *, slang) & daftar kata valid
    for rw in revised_words:
        token = rw.get("word", "").strip()
        if not token:
            continue
        low = token.lower()
        approved.add(low)
        if " " in token:
            continue
        is_special = ("*" in token) or (token[0].isupper() and len(token) >= 3) or low in (
            "bravy", "viconk", "conk", "brev", "aloy", "geri", "akong", "kagak", "ngga", "pantesan", "bener", "pake", "kalo"
        )
        if is_special:
            entry = vocab.setdefault(low, {"canonical": token, "count": 0, "phonetic": phonetic_skeleton(token)})
            entry["count"] = int(entry.get("count", 0)) + 1
            if "*" in token or token[0].isupper():
                entry["canonical"] = token

    prof["approved_words"] = sorted(approved)

    # 2. Selaraskan kata mentah Whisper dengan kata hasil revisi berdasarkan titik tengah waktu
    for i, r in enumerate(raw_words):
        raw_w = r.get("word", "").strip()
        if not raw_w:
            continue
        raw_low = raw_w.lower()
        r_mid = (float(r["start"]) + float(r["end"])) / 2.0

        matches = [
            w for w in revised_words
            if w.get("word", "").strip() and abs(((float(w["start"]) + float(w["end"])) / 2.0) - r_mid) < 0.24
        ]
        if not matches:
            continue

        best = min(matches, key=lambda w: abs(((float(w["start"]) + float(w["end"])) / 2.0) - r_mid))
        target_w = best["word"].strip()
        if not target_w:
            continue

        prev_raw = raw_words[i - 1]["word"].strip().lower() if i > 0 else "^"
        next_raw = raw_words[i + 1]["word"].strip().lower() if i + 1 < len(raw_words) else "$"

        changed = (raw_w != target_w)
        if changed or raw_low in unigram or raw_low in ("masa", "akang", "cong", "gak", "bener"):
            u_bucket = unigram.setdefault(raw_low, {})
            u_bucket[target_w] = int(u_bucket.get(target_w, 0)) + 1

            nk = f"{raw_low}|{next_raw}"
            n_bucket = next_ctx.setdefault(nk, {})
            n_bucket[target_w] = int(n_bucket.get(target_w, 0)) + 1

            pk = f"{prev_raw}|{raw_low}"
            p_bucket = prev_ctx.setdefault(pk, {})
            p_bucket[target_w] = int(p_bucket.get(target_w, 0)) + 1

            tk = f"{prev_raw}|{raw_low}|{next_raw}"
            t_bucket = tri_ctx.setdefault(tk, {})
            t_bucket[target_w] = int(t_bucket.get(target_w, 0)) + 1

            if changed:
                learned_logs.append(f"[{detected_lang.upper()}] '{raw_w}' -> '{target_w}' (konteks: {prev_raw} [{raw_w}] {next_raw})")
                t_low = target_w.lower()
                v_entry = vocab.setdefault(t_low, {"canonical": target_w, "count": 0, "phonetic": phonetic_skeleton(target_w)})
                v_entry["count"] = int(v_entry.get("count", 0)) + 1
                v_entry["canonical"] = target_w

    prof["total_revisions_trained"] = int(prof.get("total_revisions_trained", 0)) + 1
    sync_top_level_memory(memory, detected_lang)
    save_learned_memory(memory, memory_path)
    return memory, learned_logs


def build_whisper_hints_from_memory(memory: Dict[str, Any], lang: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Otomatis merakit initial_prompt dan hotwords untuk faster-whisper
    sesuai bahasa ('en' atau 'id'). Jika lang belum diketahui (None), tidak memaksa prompt bahasa tertentu.
    """
    if not lang or lang.lower() not in ("id", "en"):
        return None, None
    lang_key = lang.lower()
    prof = get_lang_profile(memory, lang_key)
    vocab = prof.get("custom_vocab", {})
    if not vocab:
        return None, None
    sorted_items = sorted(vocab.values(), key=lambda x: int(x.get("count", 0)), reverse=True)
    clean_terms = []
    for item in sorted_items:
        canon = item.get("canonical", "")
        if "*" in canon:
            continue
        if canon and canon not in clean_terms:
            clean_terms.append(canon)
        if len(clean_terms) >= 25:
            break
    if not clean_terms:
        return None, None
    hotwords_str = " ".join(clean_terms)
    if lang_key == "en":
        prompt_str = f"Casual English storytelling conversation: {', '.join(clean_terms)}."
    else:
        prompt_str = f"Obrolan santai bahasa Indonesia gaul Jakarta: {', '.join(clean_terms)}."
    return prompt_str, hotwords_str


def predict_from_bucket(bucket: Dict[str, int]) -> Tuple[Optional[str], float, int]:
    """Ambil kandidat terbaik beserta probabilitas (0..1) dan total sampel dari tabel frekuensi."""
    if not bucket:
        return None, 0.0, 0
    total = sum(int(v) for v in bucket.values())
    if total <= 0:
        return None, 0.0, 0
    best_word, best_cnt = max(bucket.items(), key=lambda kv: int(kv[1]))
    return best_word, round(int(best_cnt) / total, 2), total


def analyze_suspect_words(
    words: List[Dict[str, Any]],
    memory: Optional[Dict[str, Any]] = None,
    auto_fix_threshold: float = 0.0,
    lang: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Mendeteksi (dan opsional mengoreksi otomatis) kata yang salah dengar menggunakan:
    1. Mini-ML Contextual Trigram & Bigram Memory (terpisah antara 'en' dan 'id')
    2. Mini-ML Unigram Frequency Memory
    3. Mini-ML Phonetic Skeleton Similarity terhadap Custom Vocab user
    4. Kamus fonetik dasar (SLANG_SUSPECT_MAP untuk 'id', EN_SUSPECT_MAP untuk 'en') & Stutter/Confidence Whisper
    """
    if memory is None:
        memory = load_learned_memory()

    detected_lang = detect_transcript_language(words, explicit_lang=lang)
    sync_top_level_memory(memory, detected_lang)
    prof = get_lang_profile(memory, detected_lang)

    unigram = prof.get("unigram_counts", {})
    next_ctx = prof.get("next_context", {})
    prev_ctx = prof.get("prev_context", {})
    tri_ctx = prof.get("trigram_context", {})
    custom_vocab = prof.get("custom_vocab", {})
    approved_set = set(prof.get("approved_words", []))

    vocab_lower = {w["word"].lower() for w in words}
    has_akong = "akong" in vocab_lower
    auto_fixed_count = 0

    for i, w in enumerate(words):
        raw_word = w["word"]
        low = raw_word.lower()
        prev_w = words[i - 1] if i > 0 else None
        next_w = words[i + 1] if i + 1 < len(words) else None
        prev_low = prev_w["word"].lower() if prev_w else "^"
        next_low = next_w["word"].lower() if next_w else "$"
        duration = w["end"] - w["start"]

        suspect = False
        reason = ""
        suggestion = ""
        ml_conf = 0.0

        # --- LAYER 1: Mini-ML Contextual N-Gram (Trigram -> Next Bigram -> Prev Bigram) ---
        tk = f"{prev_low}|{low}|{next_low}"
        nk = f"{low}|{next_low}"
        pk = f"{prev_low}|{low}"

        cand, prob, total = predict_from_bucket(tri_ctx.get(tk, {}))
        ctx_label = "Trigram"
        if not cand or total == 0:
            cand, prob, total = predict_from_bucket(next_ctx.get(nk, {}))
            ctx_label = f"Konteks '{raw_word} {next_low}'"
        if not cand or total == 0:
            cand, prob, total = predict_from_bucket(prev_ctx.get(pk, {}))
            ctx_label = f"Konteks '{prev_low} {raw_word}'"

        context_approved = bool(cand and cand.lower() == low and prob >= 0.6)

        if cand and cand.lower() != low and prob >= 0.65:
            suspect = True
            suggestion = cand
            ml_conf = prob
            reason = f"🧠 Mini-ML [{detected_lang.upper()}] ({ctx_label}, akurasi {int(prob * 100)}%)"

        # --- LAYER 2: Mini-ML Unigram Memory (jika konteks spesifik tidak meng-approve kata asli) ---
        if not suspect and not context_approved and low in unigram:
            u_cand, u_prob, u_total = predict_from_bucket(unigram[low])
            if u_cand and u_cand.lower() != low and u_prob >= 0.6:
                suspect = True
                suggestion = u_cand
                ml_conf = u_prob
                reason = f"🧠 Mini-ML Memory [{detected_lang.upper()}] ({u_total}x dipelajari -> '{u_cand}', {int(u_prob * 100)}%)"

        # --- LAYER 3: Mini-ML Phonetic Similarity ke Custom Vocab (kata baru yang bunyinya mirip & belum pernah di-approve) ---
        if not suspect and low not in custom_vocab and low not in approved_set and len(low) >= 4:
            best_v = None
            best_sim = 0.0
            for v_low, v_info in custom_vocab.items():
                if v_low == low:
                    continue
                sim = phonetic_similarity(low, v_low)
                if sim > best_sim:
                    best_sim = sim
                    best_v = v_info.get("canonical", v_low)
            if best_v and best_sim >= 0.82:
                suspect = True
                suggestion = best_v
                ml_conf = best_sim
                reason = f"🔊 Mini-ML Fonetik [{detected_lang.upper()}] (kemiripan bunyi {int(best_sim * 100)}% dgn '{best_v}')"

        # --- LAYER 4: Fallback Rule-Based (Spesifik Bahasa 'id' vs 'en') & Whisper Confidence ---
        if not suspect:
            if detected_lang == "id":
                if low in SLANG_SUSPECT_MAP:
                    sug, rsn = SLANG_SUSPECT_MAP[low]
                    if low == "manggila" and prev_w and prev_low in ("lo", "lu"):
                        sug = "lebih tua"
                        rsn = "Salah dengar frasa 'lebih tua' saat suara tumpang tindih"
                    suspect = True
                    suggestion = sug
                    ml_conf = 0.85
                    reason = rsn
                elif low == "masa" and next_w and next_low == "aja":
                    suspect = True
                    suggestion = "Mas"
                    ml_conf = 0.90
                    reason = "Konteks jawaban panggilan: 'Mas aja' terdengar jadi 'Masa aja'"
                elif low == "akang" and has_akong:
                    suspect = True
                    suggestion = "Kan" if (next_w and next_low in ("lu", "lo")) else "Akong"
                    ml_conf = 0.85
                    reason = "Salah dengar 'Kan / Akong' jadi 'Akang'"
            elif detected_lang == "en":
                if low in EN_SUSPECT_MAP:
                    sug, rsn = EN_SUSPECT_MAP[low]
                    suspect = True
                    suggestion = sug
                    ml_conf = 0.85
                    reason = rsn

            if not suspect and prev_w and prev_low == low and duration <= 0.12:
                suspect = True
                suggestion = "[HAPUS]"
                ml_conf = 0.90
                reason = f"Kata duplikat sangat pendek ({duration:.2f}s)"
            elif not suspect and low not in approved_set and low not in custom_vocab and w.get("prob") is not None and float(w["prob"]) < 0.68:
                suspect = True
                reason = f"Confidence Whisper rendah ({int(float(w['prob']) * 100)}%)"

        # Auto-fix jika diaktifkan dan confidence Mini-ML melampaui threshold
        if (
            auto_fix_threshold > 0.0
            and suspect
            and suggestion
            and suggestion != "[HAPUS]"
            and ml_conf >= auto_fix_threshold
        ):
            w["word"] = suggestion
            w["suspect"] = False
            w["reason"] = f"Auto-fixed oleh Mini-ML [{detected_lang.upper()}] dari '{raw_word}'"
            w["suggestion"] = ""
            auto_fixed_count += 1
        else:
            w["suspect"] = suspect
            w["reason"] = reason
            w["suggestion"] = suggestion

    if auto_fixed_count > 0:
        print(f"[🧠 Mini-ML ({detected_lang.upper()})] Otomatis mengoreksi {auto_fixed_count} kata berdasarkan memori pembelajaran!")

    return words


def load_glossary(path: str) -> Dict[str, str]:
    """
    Muat glosarium koreksi dari file JSON {"salah": "benar"} (case-insensitive).
    Contoh: {"manggila": "manggil", "bangso": "bangsat"}
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k.lower(): v for k, v in data.items()}


def apply_glossary(words: List[Dict[str, Any]], glossary: Dict[str, str]) -> List[Dict[str, Any]]:
    """Ganti kata hasil STT sesuai glosarium; timestamp tidak diubah."""
    if not glossary:
        return words
    fixed = 0
    for w in words:
        repl = glossary.get(w["word"].lower())
        if repl is not None:
            w["word"] = repl
            fixed += 1
    print(f"[*] Glosarium: {fixed} kata dikoreksi.")
    return words


def build_whisper_plan(model: str = "auto", device: str = "auto", compute_type: str = "auto",
                       task: str = "transcribe") -> List[Dict[str, Any]]:
    """
    Susun urutan percobaan (model, device, compute_type).
    - 'auto' -> pakai gpu_check.select_profile(): pilih yang paling akurat yang muat di VRAM,
      lalu rantai fallback (mis. large-v3 -> turbo -> CPU) kalau ternyata OOM / CUDA gagal.
    - Nilai manual tetap dihormati, dengan CPU sebagai jaring pengaman terakhir.
    """
    plan: List[Dict[str, Any]] = []
    profile: Dict[str, Any] = {}
    try:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import gpu_check
        profile = gpu_check.select_profile(task=task)
    except Exception as e:
        print(f"[!] gpu_check gagal ({e}); pakai default aman.")

    if model == "auto" and device == "auto" and compute_type == "auto" and profile:
        print(f"[HW] {profile['reason']}")
        plan.append({"model": profile["model"], "device": profile["device"], "compute_type": profile["compute_type"]})
        plan.extend(profile.get("fallback_chain", []))
    else:
        dev = device if device != "auto" else (profile.get("device") or "cpu")
        mdl = model if model != "auto" else (profile.get("model") or "large-v3-turbo")
        if compute_type != "auto":
            ct = compute_type
        elif dev == "cuda":
            ct = profile.get("compute_type") if profile.get("device") == "cuda" else "int8_float16"
        else:
            ct = "int8"
        plan.append({"model": mdl, "device": dev, "compute_type": ct})
        if dev == "cuda":
            plan.append({"model": mdl, "device": "cuda", "compute_type": "int8_float16" if ct == "float16" else "int8"})
            plan.append({"model": "large-v3-turbo" if task == "transcribe" else "medium", "device": "cpu", "compute_type": "int8"})

    # Hilangkan duplikat dengan tetap menjaga urutan
    seen, uniq = set(), []
    for p in plan:
        key = (p["model"], p["device"], p["compute_type"])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def transcribe_video(input_path: str, model_size: str = "auto", language: str = None,
                     initial_prompt: str = None, hotwords: str = None, task: str = "transcribe",
                     device: str = "auto", compute_type: str = "auto") -> Tuple[List[Dict[str, Any]], str]:
    """
    Transkripsi audio/video menggunakan faster-whisper (prioritas) atau openai-whisper
    dengan word-level timestamps. Model/device/compute dipilih otomatis sesuai hardware
    (lihat gpu_check.py) dan otomatis turun kelas kalau kehabisan VRAM.
    Mengembalikan (words_list, detected_language).
    """
    detected_lang = language or "en"

    try:
        from faster_whisper import WhisperModel
        register_nvidia_dlls()
        plan = build_whisper_plan(model_size, device, compute_type, task)
        last_err: Optional[Exception] = None

        for attempt in plan:
            words_list: List[Dict[str, Any]] = []
            label = f"{attempt['model']} / {attempt['device']} / {attempt['compute_type']}"
            print(f"[*] faster-whisper -> {label} (task: {task})")
            try:
                model = WhisperModel(
                    attempt["model"],
                    device=attempt["device"],
                    compute_type=attempt["compute_type"],
                    cpu_threads=max(1, (os.cpu_count() or 4) - 1) if attempt["device"] == "cpu" else 0,
                )
                segments, info = model.transcribe(
                    input_path,
                    language=language,
                    task=task,
                    word_timestamps=True,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    no_repeat_ngram_size=3,
                    temperature=[0.0, 0.2, 0.4],
                    initial_prompt=initial_prompt,
                    hotwords=hotwords,
                )
                # segments adalah generator: error OOM baru muncul saat diiterasi, jadi iterasi di dalam try
                for segment in segments:
                    if not segment.words:
                        continue
                    for w in segment.words:
                        cleaned = clean_word(w.word)
                        if cleaned:
                            words_list.append({
                                "word": cleaned,
                                "start": float(w.start),
                                "end": float(w.end),
                                "ends_clause": has_clause_break(w.word),
                                "prob": round(float(getattr(w, "probability", 1.0)), 2),
                            })
                detected_lang = "en" if task == "translate" else getattr(info, "language", language or "en")
                print(f"[*] Bahasa terdeteksi: '{detected_lang}' "
                      f"(confidence {int(getattr(info, 'language_probability', 1.0) * 100)}%) | model dipakai: {label}")
                del model
                import gc
                gc.collect()
                return words_list, detected_lang
            except Exception as e:  # OOM, cuDNN/cuBLAS hilang, model tidak ada, dll.
                last_err = e
                print(f"[!] Gagal di {label}: {str(e).splitlines()[0][:160]}")
                print("    -> mencoba opsi berikutnya di fallback chain...")
                try:
                    del model
                except Exception:
                    pass
                import gc
                gc.collect()

        print(f"[X] Semua opsi Whisper gagal. Error terakhir: {last_err}", file=sys.stderr)
        print("    Jalankan: python install.py   (untuk cek & pasang dependency sesuai hardware)", file=sys.stderr)
        sys.exit(1)
    except ImportError:
        pass

    try:
        import whisper
        if model_size == "auto":
            model_size = "turbo" if task == "transcribe" else "medium"
        print(f"[*] Menggunakan openai-whisper (model: {model_size}, task: {task})...")
        model = whisper.load_model(model_size)
        result = model.transcribe(
            input_path,
            language=language,
            task=task,
            word_timestamps=True,
        )
        detected_lang = "en" if task == "translate" else result.get("language", language or "en")
        for segment in result.get("segments", []):
            for w in segment.get("words", []):
                cleaned = clean_word(w["word"])
                if cleaned:
                    words_list.append({
                        "word": cleaned,
                        "start": float(w["start"]),
                        "end": float(w["end"]),
                        "ends_clause": has_clause_break(w["word"]),
                    })
        return words_list, detected_lang
    except ImportError:
        print(
            "[!] Error: Library 'faster-whisper' atau 'openai-whisper' belum terinstall.\n"
            "    Jalankan: pip install faster-whisper",
            file=sys.stderr,
        )
        sys.exit(1)


def group_words_into_chunks(words: List[Dict[str, Any]], config: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """
    Mengelompokkan kata menjadi micro-chunks (2-3 kata per tampilan)
    berdasarkan jumlah kata, panjang karakter, dan jeda antar-kata.
    """
    chunks: List[List[Dict[str, Any]]] = []
    current_chunk: List[Dict[str, Any]] = []

    max_words = config["max_words_per_chunk"]
    max_chars = config["max_chars_per_chunk"]
    pause_thresh = config["pause_threshold_sec"]

    for w in words:
        if not current_chunk:
            current_chunk.append(w)
            continue

        prev_word = current_chunk[-1]
        pause = w["start"] - prev_word["end"]
        current_text = " ".join(item["word"] for item in current_chunk)
        projected_len = len(current_text) + 1 + len(w["word"])

        # Buat baris baru jika sudah mencapai batas kata, batas karakter, jeda napas, atau tanda baca klausa
        if (
            len(current_chunk) >= max_words
            or projected_len > max_chars
            or pause >= pause_thresh
            or prev_word.get("ends_clause", False)
        ):
            chunks.append(current_chunk)
            current_chunk = [w]
        else:
            current_chunk.append(w)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def generate_ass_content(chunks: List[List[Dict[str, Any]]], config: Dict[str, Any]) -> str:
    """
    Membuat isi file .ass lengkap dengan highlight warna kuning per kata aktif.
    """
    header = f"""[Script Info]
Title: GoldPulse Storytelling Caption Preset
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {config['play_res_x']}
PlayResY: {config['play_res_y']}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: GoldPulse,{config['font_name']},{config['font_size']},&H00FFFFFF,&H002EB8F5,{config['outline_color']},{config['shadow_color']},-1,0,0,0,100,100,-1,0,1,{config['outline_width']},{config['shadow_depth']},2,60,60,{config['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue_lines: List[str] = []
    active_c = config["active_color"]
    inactive_c = config["inactive_color"]

    for chunk in chunks:
        num_words = len(chunk)
        for idx, active_word in enumerate(chunk):
            start_sec = active_word["start"]
            # Sambungkan durasi hingga kata berikutnya di dalam chunk yang sama agar tidak berkedip
            if idx < num_words - 1:
                end_sec = chunk[idx + 1]["start"]
            else:
                end_sec = active_word["end"]

            # Pastikan durasi minimal 0.08 detik
            if end_sec <= start_sec:
                end_sec = start_sec + 0.12

            start_str = format_ass_time(start_sec)
            end_str = format_ass_time(end_sec)

            # Rakit kalimat dengan highlight kuning pada kata ke-idx
            formatted_words = []
            for j, w in enumerate(chunk):
                if j == idx:
                    formatted_words.append(f"{{\\1c{active_c}}}{w['word']}{{\\1c{inactive_c}}}")
                else:
                    formatted_words.append(w["word"])

            line_text = " ".join(formatted_words)
            dialogue_lines.append(
                f"Dialogue: 0,{start_str},{end_str},GoldPulse,,0,0,0,,{line_text}"
            )

    return header + "\n".join(dialogue_lines) + "\n"


def update_preview_html(html_path: Path, video_filename: str, ass_content: str,
                        words: List[Dict[str, Any]] = None,
                        memory: Dict[str, Any] = None) -> None:
    """
    Otomatis memperbarui preview_player.html dengan video, daftar kata (beserta flag suspect),
    memori Mini-ML (learned_memory.json), dan isi .ass terbaru langsung dari script.
    """
    if not html_path.exists():
        return
    html_text = html_path.read_text(encoding="utf-8")
    # Update src video pada elemen <video id="videoPlayer" ...>
    html_text = re.sub(
        r'(<video\s+id="videoPlayer"\s+src=")[^"]*(")',
        rf'\g<1>{video_filename}\g<2>',
        html_text,
    )
    if words is not None:
        words_json = json.dumps(words, ensure_ascii=False, indent=2)
        html_text = re.sub(
            r'(<script\s+id="defaultWordsData"\s+type="application/json">).*?(</script>)',
            lambda m: f"{m.group(1)}\n{words_json}\n  {m.group(2)}",
            html_text,
            flags=re.DOTALL,
        )
    if memory is not None and 'id="learnedMemoryData"' in html_text:
        mem_json = json.dumps(memory, ensure_ascii=False, indent=2)
        html_text = re.sub(
            r'(<script\s+id="learnedMemoryData"\s+type="application/json">).*?(</script>)',
            lambda m: f"{m.group(1)}\n{mem_json}\n  {m.group(2)}",
            html_text,
            flags=re.DOTALL,
        )
    html_path.write_text(html_text, encoding="utf-8")
    print(f"[+] Preview HTML & Editor otomatis diperbarui: {html_path}")


def find_ffmpeg() -> str:
    """Cari ffmpeg: PATH dulu, lalu lokasi install winget (Gyan.FFmpeg) supaya tidak perlu restart terminal."""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for cand in base.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
        return str(cand)
    print("[!] ffmpeg tidak ditemukan. Install: winget install Gyan.FFmpeg", file=sys.stderr)
    sys.exit(1)


def burn_subtitles_ffmpeg(video_path: str, ass_path: str, output_path: str) -> None:
    """Burn-in file .ass ke dalam video menggunakan FFmpeg (font dari folder fonts/ di samping script)."""
    # Escape path untuk filter subtitles FFmpeg di Windows/Linux
    escaped_ass = Path(ass_path).resolve().as_posix().replace(":", r"\:")
    vf = f"ass='{escaped_ass}'"
    fonts_dir = Path(__file__).resolve().parent / "fonts"
    if fonts_dir.is_dir():
        escaped_fonts = fonts_dir.as_posix().replace(":", r"\:")
        vf += f":fontsdir='{escaped_fonts}'"
    cmd = [
        find_ffmpeg(),
        "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        output_path,
    ]
    print(f"[*] Merender video dengan FFmpeg ke: {output_path}")
    subprocess.run(cmd, check=True)
    print(f"[+] Selesai! Video tersimpan di: {output_path}")


ORIENTATION_PRESETS = {
    # PlayRes mengikuti orientasi video supaya teks tidak gepeng/melar
    "portrait": {"play_res_x": 1080, "play_res_y": 1920, "font_size": 63, "margin_v": 460, "max_chars_per_chunk": 22},
    "landscape": {"play_res_x": 1920, "play_res_y": 1080, "font_size": 58, "margin_v": 110, "max_chars_per_chunk": 32},
    "square": {"play_res_x": 1080, "play_res_y": 1080, "font_size": 56, "margin_v": 150, "max_chars_per_chunk": 24},
}


def probe_video_size(video_path: str) -> Optional[Tuple[int, int]]:
    """Ambil (width, height) video via ffprobe (memperhitungkan rotasi metadata HP)."""
    try:
        ffmpeg = find_ffmpeg()
    except SystemExit:
        return None
    ffprobe = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if not ffprobe.exists():
        import shutil
        found = shutil.which("ffprobe")
        if not found:
            return None
        ffprobe = Path(found)
    try:
        out = subprocess.run(
            [str(ffprobe), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
             "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(out.stdout or "{}")
        stream = (info.get("streams") or [{}])[0]
        w, h = int(stream.get("width", 0)), int(stream.get("height", 0))
        rot = 0
        for sd in stream.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = int(float(stream.get("tags", {}).get("rotate", rot)))
        if abs(rot) in (90, 270):
            w, h = h, w
        return (w, h) if w and h else None
    except Exception:
        return None


def detect_orientation(size: Optional[Tuple[int, int]]) -> str:
    if not size:
        return "portrait"
    w, h = size
    if h > w * 1.15:
        return "portrait"
    if w > h * 1.15:
        return "landscape"
    return "square"


def hex_to_ass_color(hex_color: str) -> str:
    """'#F5B82E' -> '&H2EB8F5&' (ASS memakai urutan BGR)."""
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Warna harus format #RRGGBB, dapat: {hex_color}")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def ask_language_interactive() -> Tuple[Optional[str], bool]:
    """Tanya bahasa video sebelum transkripsi. Return (lang, translate)."""
    print("\n" + "=" * 56)
    print(" Bahasa caption untuk video ini?")
    print("   [1] English (en)")
    print("   [2] Bahasa Indonesia (id)")
    print("   [3] Suara Indonesia -> caption English (translate)")
    print("   [4] Auto-detect")
    print("=" * 56)
    while True:
        try:
            choice = input(" Pilih 1/2/3/4 [1]: ").strip() or "1"
        except EOFError:
            return None, False
        if choice == "1":
            return "en", False
        if choice == "2":
            return "id", False
        if choice == "3":
            return "id", True
        if choice == "4":
            return None, False
        print(" Pilihan tidak valid, ketik 1, 2, 3, atau 4.")


def load_last_run() -> Dict[str, Any]:
    if LAST_RUN_PATH.exists():
        try:
            return json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def main():
    parser = argparse.ArgumentParser(
        description="GoldPulse Captions - auto caption micro-chunk + highlight kata aktif (EN/ID), Mini-ML, burn-in FFmpeg"
    )
    parser.add_argument("input", help="Video/audio (.mp4, .mov, .mp3, .wav, ...) ATAU words JSON hasil revisi")
    parser.add_argument("-o", "--output-ass", help="Path output .ass (default: <input>.ass)")
    parser.add_argument("--burn", action="store_true", help="Burn-in subtitle ke video dengan FFmpeg")
    parser.add_argument("--output-video", help="Path video hasil burn-in (default: <input>_captioned.mp4)")
    parser.add_argument("--video", help="Video sumber saat input berupa JSON (default: video dari run terakhir)")

    # Bahasa
    parser.add_argument("--lang", choices=["en", "id", "auto"], default=None,
                        help="Bahasa video. Kalau tidak diisi, script akan bertanya dulu (mode interaktif)")
    parser.add_argument("--translate", action="store_true", help="Suara bahasa apapun -> caption Bahasa Inggris")
    parser.add_argument("--no-ask", action="store_true", help="Jangan bertanya bahasa; auto-detect bila --lang kosong")

    # Whisper / hardware
    parser.add_argument("--model", default="auto",
                        help="auto (sesuai GPU) | large-v3 | large-v3-turbo | medium | small | base")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="auto")
    parser.add_argument("--compute-type", default="auto", help="auto | float16 | int8_float16 | int8")
    parser.add_argument("--prompt", default=None, help="Konteks/gaya bicara untuk Whisper")
    parser.add_argument("--hotwords", default=None, help="Nama/istilah dipisah spasi yang sering salah dengar")
    parser.add_argument("--glossary", default=None, help="JSON koreksi kata {\"salah\": \"benar\"}")

    # Mini-ML
    parser.add_argument("--learn-from", default=None, help="JSON mentah Whisper pembanding (default: dari run terakhir)")
    parser.add_argument("--auto-fix-ml", type=float, default=0.80, help="Threshold auto-koreksi Mini-ML (0 = matikan)")
    parser.add_argument("--save-words", default=None, help="Simpan daftar kata + timestamp ke JSON ini")

    # Style
    parser.add_argument("--max-words", type=int, default=3, help="Maksimal kata per baris (default: 3)")
    parser.add_argument("--max-chars", type=int, default=None, help="Maksimal karakter per baris (default: sesuai orientasi)")
    parser.add_argument("--font", default="Montserrat", help="Nama font (default: Montserrat, file di folder fonts/)")
    parser.add_argument("--font-size", type=int, default=None, help="Ukuran font ASS (default: sesuai orientasi)")
    parser.add_argument("--margin-v", type=int, default=None, help="Jarak caption dari bawah (default: sesuai orientasi)")
    parser.add_argument("--highlight", default="#F5B82E", help="Warna kata aktif #RRGGBB (default: #F5B82E emas)")
    parser.add_argument("--open-preview", action="store_true", help="Buka preview_player.html di browser setelah selesai")

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"[!] File tidak ditemukan: {input_path}", file=sys.stderr)
        sys.exit(1)

    is_json = input_path.suffix.lower() == ".json"
    last_run = load_last_run()

    # Video sumber (untuk orientasi, preview & burn)
    if is_json:
        source_video = Path(args.video).resolve() if args.video else (
            Path(last_run["video"]) if last_run.get("video") else SCRIPT_DIR / "test_video.mp4")
    else:
        source_video = input_path

    # Config visual sesuai orientasi + override user
    config = dict(PRESET_CONFIG)
    size = probe_video_size(str(source_video)) if source_video.exists() else None
    orientation = detect_orientation(size)
    config.update(ORIENTATION_PRESETS[orientation])
    config["max_words_per_chunk"] = args.max_words
    config["font_name"] = args.font
    if args.max_chars:
        config["max_chars_per_chunk"] = args.max_chars
    if args.font_size:
        config["font_size"] = args.font_size
    if args.margin_v is not None:
        config["margin_v"] = args.margin_v
    config["active_color"] = hex_to_ass_color(args.highlight)
    print(f"[*] Video: {source_video.name} | {size[0]}x{size[1]} -> {orientation}" if size
          else f"[*] Orientasi default: {orientation}")

    memory = load_learned_memory()

    # 1. Ambil daftar kata beserta timestamp-nya
    if is_json:
        with open(input_path, "r", encoding="utf-8") as f:
            raw_words = json.load(f)
        words = [
            {
                "word": clean_word(w["word"]),
                "start": float(w["start"]),
                "end": float(w["end"]),
                "ends_clause": bool(w.get("ends_clause", False)) or has_clause_break(w.get("word", "")),
            }
            for w in raw_words
            if clean_word(w.get("word", ""))
        ]
        explicit = None if args.lang in (None, "auto") else args.lang
        active_lang = detect_transcript_language(words, explicit_lang=explicit or last_run.get("lang"))

        # Latih Mini-ML: bandingkan revisi user vs hasil mentah Whisper run sebelumnya
        if args.learn_from:
            raw_ref_path = Path(args.learn_from)
        elif last_run.get("raw_words"):
            raw_ref_path = Path(last_run["raw_words"])
        else:
            raw_ref_path = SCRIPT_DIR / "v2_words.json"
        if raw_ref_path.exists() and raw_ref_path.resolve() != input_path:
            try:
                orig_raw = json.loads(raw_ref_path.read_text(encoding="utf-8"))
                memory, learned_logs = train_mini_ml(orig_raw, words, lang=active_lang)
                prof = get_lang_profile(memory, active_lang)
                print(f"[🧠 Mini-ML ({active_lang.upper()})] Belajar dari '{input_path.name}' vs '{raw_ref_path.name}': "
                      f"{len(learned_logs)} koreksi & {len(prof.get('custom_vocab', {}))} kosakata tersimpan")
            except Exception as e:
                print(f"[!] Gagal melatih Mini-ML: {e}")

        words = analyze_suspect_words(words, memory=memory, auto_fix_threshold=0.0, lang=active_lang)
    else:
        # Tanya bahasa dulu sebelum benar-benar jalan (kalau belum ditentukan & terminal interaktif)
        lang_arg = None if args.lang == "auto" else args.lang
        translate = args.translate
        if args.lang is None and not translate and not args.no_ask and sys.stdin.isatty():
            lang_arg, translate = ask_language_interactive()
        elif args.lang is None and not translate:
            print("[i] --lang tidak diisi & mode non-interaktif -> auto-detect bahasa.")

        task_mode = "translate" if translate else "transcribe"
        target_hint_lang = "en" if translate else lang_arg
        mem_prompt, mem_hotwords = build_whisper_hints_from_memory(memory, lang=target_hint_lang)
        effective_prompt = args.prompt or mem_prompt
        effective_hotwords = args.hotwords or mem_hotwords
        if effective_hotwords:
            print(f"[🧠 Mini-ML ({(target_hint_lang or '?').upper()})] Kosakata disuntik ke Whisper: {effective_hotwords}")

        words, detected_lang = transcribe_video(
            str(input_path),
            model_size=args.model,
            language=lang_arg,
            initial_prompt=effective_prompt,
            hotwords=effective_hotwords,
            task=task_mode,
            device=args.device,
            compute_type=args.compute_type,
        )
        active_lang = detect_transcript_language(
            words, explicit_lang=("en" if translate else (lang_arg or detected_lang)))

        # Snapshot mentah (pembanding Mini-ML saat user merevisi)
        raw_snapshot_path = input_path.with_name(f"{input_path.stem}_raw_words.json")
        raw_snapshot_path.write_text(json.dumps(words, indent=1, ensure_ascii=False), encoding="utf-8")
        LAST_RUN_PATH.write_text(json.dumps({
            "video": str(input_path),
            "raw_words": str(raw_snapshot_path),
            "lang": active_lang,
            "task": task_mode,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

        if args.glossary:
            words = apply_glossary(words, load_glossary(args.glossary))

        words = analyze_suspect_words(words, memory=memory, auto_fix_threshold=args.auto_fix_ml, lang=active_lang)
        save_learned_memory(memory)

    if not words:
        print("[!] Tidak ada kata yang terdeteksi.", file=sys.stderr)
        sys.exit(1)

    suspects = [w for w in words if w.get("suspect")]
    if suspects:
        print(f"\n[!] {len(suspects)} kata dicurigai salah dengar / perlu dicek:")
        for s in suspects:
            sug_str = f" -> Saran: '{s['suggestion']}'" if s.get("suggestion") else ""
            print(f"    - [{format_ass_time(s['start'])}] \"{s['word']}\" ({s.get('reason', '')}){sug_str}")
        print()

    if args.save_words:
        Path(args.save_words).write_text(json.dumps(words, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[+] Daftar kata disimpan: {args.save_words}")

    # 2. Micro-chunk 2-3 kata per baris
    chunks = group_words_into_chunks(words, config)

    # 3. Generate .ass
    ass_content = generate_ass_content(chunks, config)
    ass_path = Path(args.output_ass) if args.output_ass else input_path.with_suffix(".ass")
    ass_path.write_text(ass_content, encoding="utf-8")
    print(f"[+] Subtitle: {ass_path} ({len(chunks)} potongan frasa, bahasa: {active_lang})")

    # 4. Update preview/editor HTML
    preview_html_path = SCRIPT_DIR / "preview_player.html"
    if source_video.exists() and source_video.parent == SCRIPT_DIR:
        video_ref = source_video.name
    else:
        video_ref = source_video.as_uri() if source_video.exists() else source_video.name
    update_preview_html(preview_html_path, video_ref, ass_content, words=words, memory=memory)

    # 5. Burn-in
    if args.burn:
        if not source_video.exists():
            print(f"[X] Video sumber tidak ditemukan: {source_video} (pakai --video)", file=sys.stderr)
            sys.exit(1)
        out_vid = Path(args.output_video) if args.output_video else input_path.with_name(f"{input_path.stem}_captioned.mp4")
        burn_subtitles_ffmpeg(str(source_video), str(ass_path), str(out_vid))

    if args.open_preview:
        import webbrowser
        webbrowser.open(preview_html_path.as_uri())


if __name__ == "__main__":
    main()
