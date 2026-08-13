from piper import PiperVoice
import sounddevice as sd


MODEL = "sv_SE-nst-medium.onnx"

# Ladda rösten en gång när programmet startar
voice = PiperVoice.load(MODEL)


def speak(text: str):
    """Läser upp text med svensk mansröst."""
    
    for audio in voice.synthesize(text):
        sd.play(audio.audio_float_array, audio.sample_rate)
        sd.wait()

if __name__ == "__main__":
    # Testa att läsa upp en text
    test_text = "Hej! Jag är en svensk mansröst som läser upp text."
    speak(test_text)