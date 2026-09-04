"""
voice/tts.py
Bobs text-till-tal. Stödjer flera "motorer" (config.json: tts_engine):

    "piper"      - lokal, snabb, en röst per .onnx-fil (default).
    "chatterbox" - multilingual, se voice/tts_chatterbox.py.

speak() är async så att TTS-uppspelning aldrig blockerar
asyncio-loopen den anropas ifrån (se tools/approval_agent.py och
main.py) - den faktiska syntes+uppspelningen (CPU/GPU-bunden resp.
blockerande ljud-I/O) körs i en bakgrundstråd via asyncio.to_thread.
"""
import asyncio

from piper import PiperVoice
import sounddevice as sd

from config_manager import get_config_value

MODEL = "sv_SE-nst-medium.onnx"

# Ladda Piper-rösten en gång när programmet startar.
voice = PiperVoice.load(MODEL)


def _speak_piper_sync(text: str) -> None:
    """Läser upp text med Piper (svensk mansröst)."""
    for audio in voice.synthesize(text):
        sd.play(audio.audio_float_array, audio.sample_rate)
        sd.wait()


def _speak_sync(text: str, engine: str) -> None:
    if engine == "chatterbox":
        try:
            from voice.tts_chatterbox import speak_sync as _chatterbox_speak_sync
            _chatterbox_speak_sync(text)
            return
        except Exception as exc:
            print(f"\033[33mChatterbox TTS misslyckades, faller tillbaka på Piper: {exc}\033[0m")

    _speak_piper_sync(text)


async def speak(text: str, engine: str = None) -> None:
    """Läser upp `text` med den valda TTS-motorn (config.json:
    tts_engine, default "piper"). Async - blockerar inte event-loopen."""
    if not text:
        return

    engine = engine or get_config_value("tts_engine", "piper") or "piper"

    await asyncio.to_thread(_speak_sync, text, engine)


def speak_blocking(text: str, engine: str = None) -> None:
    """Synkron variant för icke-async anropsplatser (t.ex. körd direkt
    som skript, eller från en tråd som inte har en event-loop)."""
    engine = engine or get_config_value("tts_engine", "piper") or "piper"
    _speak_sync(text, engine)


if __name__ == "__main__":
    # Testa att läsa upp en text
    test_text = "Hej! Jag är en svensk mansröst som läser upp text."
    speak_blocking(test_text)
