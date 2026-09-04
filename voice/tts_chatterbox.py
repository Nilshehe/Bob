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

        try:
            _model = ChatterboxMultilingualTTS.from_pretrained(device=device)
        except TypeError as exc:
            # Vanligaste orsaken till "'NoneType' object is not callable"
            # här: resemble-perth (chatterbox vattenmärkning) importerar
            # pkg_resources, som togs bort ur setuptools i version 82+.
            # resemble-perth sväljer det ImportError:et tyst och sätter
            # PerthImplicitWatermarker = None, vilket kraschar först här,
            # vid modell-laddning, med ett helt ointuitivt felmeddelande.
            if "NoneType" in str(exc):
                raise RuntimeError(
                    "Chatterbox-modellen kunde inte laddas ('NoneType' "
                    "object is not callable) - troligen för att "
                    "resemble-perth inte kunde importera pkg_resources "
                    "(borttaget i setuptools>=82) och tyst satte sin "
                    "watermarker till None. Kör: "
                    "pip install \"setuptools<81\" och försök igen."
                ) from exc
            raise

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
    # ChatterboxMultilingualTTS.generate() har INGET default-värde för
    # language_id (obligatorisk positional) - skickar vi inte med den
    # kraschar anropet med "missing 1 required positional argument:
    # 'language_id'" så fort config.json saknar "chatterbox_language".
    # Faller tillbaka på svenska (Bobs default-språk) istället för att
    # låta chatterbox "auto-detektera", vilket den inte stödjer.
    language = language or get_config_value("chatterbox_language") or "sv"

    audio_prompt_path = _resolve_voice_path(voice_name)

    kwargs = {"language_id": language}
    if audio_prompt_path:
        kwargs["audio_prompt_path"] = audio_prompt_path
    elif model.conds is None:
        # Ingen röstfil vald och ingen inbyggd default-röst laddad -
        # generate() skulle annars krascha på
        # "assert self.conds is not None". Varna tydligt istället för
        # att låta det explodera i en AssertionError utan kontext.
        raise RuntimeError(
            "Ingen chatterbox-röst vald och ingen default-röst hittades. "
            "Sätt config.json: chatterbox_voice till ett filnamn i "
            "voice/voices/ (utan .wav)."
        )

    wav = model.generate(text, **kwargs)

    # Chatterbox returnerar en torch-tensor (1, N) eller (N,) float
    # audio vid modellens samplingsfrekvens (model.sr).
    audio = wav.squeeze().detach().cpu().numpy()

    sd.play(audio, model.sr)
    sd.wait()


if __name__ == "__main__":
    speak_sync("Hej! Det här är Chatterbox multilingual-motorn.", language="sv")
