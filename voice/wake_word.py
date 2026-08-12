"""
wake_word.py

Wake-word-detektion helt i ren numpy (MFCC + DTW-mallmatchning),
inget konto/API krävs.

Fristående användning:
    python wake_word.py enroll     # spela in wake word, spara mallar
    python wake_word.py listen     # testa live-detektion (bara utskrift)

Importeras även av live_stt.py.
"""

import sys
import time
import numpy as np
import sounddevice as sd

# ---------------- KONFIG ----------------
WAKE_WORD_LABEL = "BOB"
TEMPLATE_FILE = "voice/wake_templates.npz"
N_ENROLL_SAMPLES = 5
WAKE_WORD_DURATION_S = 1.0

DTW_THRESHOLD = 35.0
CHECK_INTERVAL_S = 0.25
ENERGY_GATE = 0.01

SAMPLE_RATE = 16000

# MFCC-parametrar
N_MFCC = 13
N_MELS = 26
FRAME_MS = 25
HOP_MS = 10
# -----------------------------------------


# ================= MFCC (ren numpy) =================

def hz_to_mel(hz):
    return 2595 * np.log10(1 + hz / 700.0)


def mel_to_hz(mel):
    return 700 * (10 ** (mel / 2595.0) - 1)


def mel_filterbank(n_mels, n_fft, sr):
    low_mel = hz_to_mel(0)
    high_mel = hz_to_mel(sr / 2)
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    fbank = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        f_m_minus, f_m, f_m_plus = bins[m - 1], bins[m], bins[m + 1]
        for k in range(f_m_minus, f_m):
            if f_m > f_m_minus:
                fbank[m - 1, k] = (k - f_m_minus) / (f_m - f_m_minus)
        for k in range(f_m, f_m_plus):
            if f_m_plus > f_m:
                fbank[m - 1, k] = (f_m_plus - k) / (f_m_plus - f_m)
    return fbank


def compute_mfcc(signal: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    if len(signal) == 0:
        return np.zeros((0, N_MFCC))

    signal = signal - np.mean(signal)
    signal = np.append(signal[0], signal[1:] - 0.97 * signal[:-1])

    frame_len = int(sr * FRAME_MS / 1000)
    hop_len = int(sr * HOP_MS / 1000)
    n_fft = 1
    while n_fft < frame_len:
        n_fft *= 2

    if len(signal) < frame_len:
        signal = np.pad(signal, (0, frame_len - len(signal)))

    n_frames = max(1, 1 + (len(signal) - frame_len) // hop_len)
    window = np.hamming(frame_len)
    fbank = mel_filterbank(N_MELS, n_fft, sr)

    mfccs = []
    for i in range(n_frames):
        start = i * hop_len
        frame = signal[start:start + frame_len]
        if len(frame) < frame_len:
            frame = np.pad(frame, (0, frame_len - len(frame)))
        frame = frame * window

        spectrum = np.fft.rfft(frame, n=n_fft)
        power = (np.abs(spectrum) ** 2) / n_fft

        mel_energy = np.dot(fbank, power)
        mel_energy = np.where(mel_energy == 0, np.finfo(float).eps, mel_energy)
        log_mel = np.log(mel_energy)

        n = np.arange(N_MELS)
        mfcc_frame = np.zeros(N_MFCC)
        for k in range(N_MFCC):
            mfcc_frame[k] = np.sum(log_mel * np.cos(np.pi * k * (2 * n + 1) / (2 * N_MELS)))
        mfccs.append(mfcc_frame)

    return np.array(mfccs)


def dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")

    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dist = np.linalg.norm(a[i - 1] - b[j - 1])
            cost[i, j] = dist + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])
    return cost[n, m] / (n + m)


# ================= Enrollment =================

def record_seconds(seconds: float) -> np.ndarray:
    print(f"Recording {seconds:.1f}s...")
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def enroll():
    print(f"Recording wake word '{WAKE_WORD_LABEL}' {N_ENROLL_SAMPLES} times.")
    templates = []
    for i in range(N_ENROLL_SAMPLES):
        input(f"Press Enter and say '{WAKE_WORD_LABEL}' (recording {i + 1}/{N_ENROLL_SAMPLES})...")
        audio = record_seconds(WAKE_WORD_DURATION_S)
        templates.append(compute_mfcc(audio))
        print("  Recorded.")
    np.savez(TEMPLATE_FILE, *templates)
    print(f"Saved {len(templates)} templates to {TEMPLATE_FILE}")


def load_templates():
    data = np.load(TEMPLATE_FILE)
    return [data[key] for key in data.files]


# ================= Live-detektion =================

def wait_for_wake_word(templates=None):
    """
    Blocks until the wake word is heard. Prints DTW distances so you can
    observe that it is listening and how close a match is.
    """
    if templates is None:
        templates = load_templates()

    window_samples = int(WAKE_WORD_DURATION_S * SAMPLE_RATE)
    buf = np.zeros(window_samples, dtype="float32")

    def wake_callback(indata, frames, time_info, status):
        nonlocal buf
        buf = np.roll(buf, -frames)
        buf[-frames:] = indata[:, 0]

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=wake_callback):
        while True:
            time.sleep(CHECK_INTERVAL_S)

            level = np.sqrt(np.mean(buf ** 2))
            if level < ENERGY_GATE:
                #print(f"\r... (tyst, nivå={level:.4f})   ", end="", flush=True)
                continue

            mfcc = compute_mfcc(buf)
            best = min(dtw_distance(mfcc, t) for t in templates)
            #print(f"\rLyssnar... dtw={best:.1f}  (tröskel={DTW_THRESHOLD})   ", end="", flush=True)

            if best < DTW_THRESHOLD:
                #print(f"\n>> '{WAKE_WORD_LABEL}' upptäckt! (dtw={best:.1f})")
                return


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("enroll", "listen"):
        print("Usage: python wake_word.py [enroll|listen]")
        sys.exit(1)

    if sys.argv[1] == "enroll":
        enroll()
    else:
        wait_for_wake_word()
