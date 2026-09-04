"""
voice/tts_chatterbox.py
Chatterbox Multilingual TTS-motor för Bob - alternativ till Piper
(voice/tts.py) med stöd för fler språk och röstkloning från en
referens-ljudfil ("voice cloning").

Väljs i settings-widgeten (TTS-sektionen, syns när TALKING är på) via
config.json: "tts_engine": "chatterbox". Röstfiler läggs i
voice/voices/<namn>.wav (en kort referensinspelning, ~5-15 sek räcker)
och väljs med config.json: "chatterbox_voice": "<namn>" - annars
används Chatterbox default-röst.

Modellen laddas lat (första anropet) och cachas sedan i minnet, precis
som Pipers `voice` i tts.py - annars skulle varje repliksyntes ladda om
hela modellen.

Kräver `chatterbox-tts` (pip install chatterbox-tts). Om paketet eller
en GPU/CPU-kompatibel torch-installation saknas kastar speak_sync() ett
tydligt fel istället för att krascha tyst - voice/tts.py fångar det och
faller tillbaka på Piper.
"""
from pathlib import Path
from threading import Lock

VOICES_DIR = Path(__file__).parent / "voices"

_model = None
_model_lock = Lock()

SUPPORTED_LANGUAGES = {
    "ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it",
    "ja", "ko", "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr",
    "zh",
}


def _get_model():
    """Laddar Chatterbox Multilingual-modellen en gång, återanvänds
    sedan (samma mönster som Pipers `voice = PiperVoice.load(...)`)."""
    global _model

    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model

        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as exc:
            raise RuntimeError(
                "chatterbox-tts är inte installerat. "
                "Kör: pip install chatterbox-tts"
            ) from exc

        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = ChatterboxMultilingualTTS.from_pretrained(device=device)

    return _model


def _resolve_voice_path(voice_name: str) -> str | None:
    """Nils fixar själv röstfilerna (voice/voices/<namn>.wav) - den här
    funktionen letar bara upp filen om ett namn är angivet."""
    if not voice_name:
        return None

    candidate = VOICES_DIR / f"{voice_name}.wav"
    if candidate.exists():
        return str(candidate)

    print(f"\033[33mChatterbox: hittar ingen röstfil för '{voice_name}' i {VOICES_DIR}\033[0m")
    return None


def speak_sync(
    text: str,
    language: str = None,
    voice_name: str = None,
) -> None:
    """Syntetiserar och spelar upp `text` med Chatterbox Multilingual.

    Synkron/blockerande - anropas via voice/tts.py:s async speak(),
    som kör den här i en bakgrundstråd (asyncio.to_thread) så
    event-loopen aldrig fryser under syntesen.

    Args:
        text: Texten som ska läsas upp.
        language: ISO 639-1-språkkod (t.ex. "sv", "en"). None = låt
            Chatterbox försöka detektera språket automatiskt.
        voice_name: Namn på en referens-ljudfil i voice/voices/ för
            röstkloning. None = Chatterbox default-röst, eller
            config.json: chatterbox_voice om satt.
    """
    if not text:
        return

    import sounddevice as sd

    from config_manager import get_config_value

    model = _get_model()

    voice_name = voice_name or get_config_value("chatterbox_voice")
    language = language or get_config_value("chatterbox_language")

    audio_prompt_path = _resolve_voice_path(voice_name)

    kwargs = {}
    if language:
        kwargs["language_id"] = language
    if audio_prompt_path:
        kwargs["audio_prompt_path"] = audio_prompt_path

    wav = model.generate(text, **kwargs)

    # Chatterbox returnerar en torch-tensor (1, N) eller (N,) float
    # audio vid modellens samplingsfrekvens (model.sr).
    audio = wav.squeeze().detach().cpu().numpy()

    sd.play(audio, model.sr)
    sd.wait()


if __name__ == "__main__":
    speak_sync("Hej! Det här är Chatterbox multilingual-motorn.", language="sv")
