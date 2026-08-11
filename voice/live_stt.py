import sys
import time
import collections
import queue
import numpy as np
import sounddevice as sd
import webrtcvad
from faster_whisper import WhisperModel


# ---------------- KONFIG ----------------
SAMPLE_RATE = 16000
VAD_FRAME_MS = 20
VAD_FRAME_SIZE = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)

VAD_AGGRESSIVENESS = 2
SILENCE_TIMEOUT_MS = 800
MAX_UTTERANCE_S = 30

COMMAND_MODEL_SIZE = "medium"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE = "int8"
LANGUAGE = "sv"
# -----------------------------------------

vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
audio_q = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    if status:
        print(status, file=sys.stderr)
    audio_q.put(bytes(indata))


def is_speech(frame_bytes: bytes) -> bool:
    try:
        return vad.is_speech(frame_bytes, SAMPLE_RATE)
    except Exception:
        return False


def record_utterance() -> bytes:
    """VAD-baserad inspelning: väntar på tal, avslutar vid riktig tystnad efter tal."""
    ring_buffer = collections.deque(maxlen=10)
    triggered = False
    voiced_frames = []
    silence_frames = 0
    silence_limit = int(SILENCE_TIMEOUT_MS / VAD_FRAME_MS)
    start_time = None

    while True:
        frame = audio_q.get()
        speech = is_speech(frame)

        if not triggered:
            ring_buffer.append((frame, speech))
            num_voiced = len([f for f, s in ring_buffer if s])
            if num_voiced > 0.6 * ring_buffer.maxlen:
                triggered = True
                start_time = time.time()
                voiced_frames.extend(f for f, s in ring_buffer)
                ring_buffer.clear()
        else:
            voiced_frames.append(frame)
            silence_frames = 0 if speech else silence_frames + 1
            elapsed = time.time() - start_time
            if silence_frames > silence_limit or elapsed > MAX_UTTERANCE_S:
                break

    return b"".join(voiced_frames)


def transcribe(pcm_bytes: bytes, whisper_model: WhisperModel) -> str:
    if not pcm_bytes:
        return ""
    audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = whisper_model.transcribe(audio_np, language=LANGUAGE, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()

print(f"Laddar Whisper-modell ({COMMAND_MODEL_SIZE}, svenska)...")
WHISPER = WhisperModel(COMMAND_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
print("Klar.")

def stt_main():
    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=VAD_FRAME_SIZE,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    ):
        while not audio_q.empty():
            audio_q.get()

        print("Lyssnar på kommando...")
        cmd_pcm = record_utterance()

    cmd_text = transcribe(cmd_pcm, WHISPER)
    return cmd_text
        


if __name__ == "__main__":
    stt_main()
