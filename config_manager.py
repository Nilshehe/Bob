import json
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any
import requests

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except Exception:
    pass


def get_chatterbox_voices() -> list[str]:
    """Listar tillgängliga röstklonings-filer i voice/voices/ (namnen
    som chatterbox_voice i config.json/settings-widgeten kan sättas
    till - utan .wav-ändelsen). Tom lista = bara Chatterbox
    inbyggda default-röst går att välja."""
    voices_dir = Path(__file__).parent / "voice" / "voices"
    if not voices_dir.exists():
        return []
    return sorted(p.stem for p in voices_dir.glob("*.wav"))


# get_ollama_models() gör ett blockerande HTTP-anrop (requests, inte
# httpx/async) - och anropas synkront från gui_server.py:s
# _rerender_config_widget() varje gång NÅGON toggle/textfält ändras i
# settings-widgeten, rakt inne i den enda asyncio event-loopen som
# hela GUI-websocketen delar på (main_gui.py). Utan cache fryser alltså
# HELA GUI:t (alla fönster, alla widgetar) i upp till timeout-tiden
# (3s) för varje enskild toggle-klick - det är det som känts som att
# knapparna "ändras väldigt långsamt". 15s-cache gör att bara den
# FÖRSTA togglen inom ett 15s-fönster betalar nätverksanropet.
_OLLAMA_MODELS_CACHE_TTL = 15
_ollama_models_cache: dict = {"ts": 0.0, "models": []}


def get_ollama_models(force_refresh: bool = False):
    now = time.monotonic()
    if not force_refresh and (now - _ollama_models_cache["ts"]) < _OLLAMA_MODELS_CACHE_TTL:
        return _ollama_models_cache["models"]

    models = _fetch_ollama_models()
    _ollama_models_cache["ts"] = now
    _ollama_models_cache["models"] = models
    return models


def _fetch_ollama_models():
    try:
        response = requests.get(
            "http://localhost:11434/api/tags",
            timeout=3,
        )

        response.raise_for_status()

        data = response.json()

        return [
            model["name"]
            for model in data.get("models", [])
        ]

    except Exception:
        return []


# ---------------------------------------------------------------------
# Modell-providers
# ---------------------------------------------------------------------
# "ollama" körs lokalt och behöver ingen API-nyckel - övriga är API-
# providers som langchains init_chat_model() stödjer (model_provider=
# nyckeln nedan). default_key_env är bara ett förslag som fylls i
# första gången en provider väljs i settings-widgeten - användaren kan
# döpa om den till vad .env-variabeln faktiskt heter hos dem
# (config.json: api_key_envs.<provider>).
API_PROVIDERS = {
    "ollama": {"label": "Ollama (lokalt)", "default_key_env": None},
    "openai": {"label": "OpenAI", "default_key_env": "OPENAI_API_KEY"},
    "anthropic": {"label": "Anthropic", "default_key_env": "ANTHROPIC_API_KEY"},
    "google_genai": {"label": "Google (Gemini)", "default_key_env": "GOOGLE_API_KEY"},
    "groq": {"label": "Groq", "default_key_env": "GROQ_API_KEY"},
    "mistralai": {"label": "Mistral", "default_key_env": "MISTRAL_API_KEY"},
    "deepseek": {"label": "DeepSeek", "default_key_env": "DEEPSEEK_API_KEY"},
    "xai": {"label": "xAI (Grok)", "default_key_env": "XAI_API_KEY"},
    "openrouter": {"label": "OpenRouter", "default_key_env": "OPENROUTER_API_KEY"},
}

# Providers som har ett OpenAI-kompatibelt GET /models-listan-API - vi
# kan använda samma koll-logik för alla dessa (se check_model()).
_OPENAI_STYLE_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistralai": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


CONFIG_PATH = Path(__file__).parent / "config.json"

_lock = RLock()


def load_config() -> dict:
    with _lock:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_config(config: dict) -> None:
    with _lock:
        temp_path = CONFIG_PATH.with_suffix(".json.tmp")

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(
                config,
                f,
                indent=4,
                ensure_ascii=False,
            )
            f.write("\n")

        temp_path.replace(CONFIG_PATH)


def get_config_value(path: str, default: Any = None) -> Any:
    config = load_config()

    current = config

    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default

        current = current[part]

    return current


def set_config_value(path: str, value: Any) -> None:
    config = load_config()

    parts = path.split(".")
    current = config

    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}

        current = current[part]

    current[parts[-1]] = value

    save_config(config)


def get_enabled_tools() -> dict:
    config = load_config()
    return {
        name: enabled
        for name, enabled in config.get("tools", {}).items()
        if enabled is True
    }


def is_tool_enabled(name: str) -> bool:
    return bool(
        get_config_value(
            f"tools.{name}",
            False,
        )
    )


def get_interrupt_tools() -> dict:
    config = load_config()
    return config.get("interupt_tools", {})


def is_interrupt_enabled(name: str) -> bool:
    return bool(
        get_config_value(
            f"interupt_tools.{name}",
            False,
        )
    )


def get_provider() -> str:
    return get_config_value("provider", "ollama") or "ollama"


# ---------------------------------------------------------------------
# Per-agent AI (Approval AI, Edit AI, Research AI, Code AI)
# ---------------------------------------------------------------------
# Varje underagent kan köra en egen provider/modell, oberoende av
# huvud-AI:n (config.json: agents.<agent_key>.provider/model). Tomt
# fält/saknad nyckel = ärver huvud-AI:ns provider, och underagentens
# egna default_model (t.ex. "qwen3:4b") om inget annat är satt.
# Settings-widgeten (config_widget) läser/skriver de här via
# config_path "agents.<agent_key>.provider" / ".model".

AGENT_KEYS = ("approval", "edit_ai", "research_ai", "code_ai")


def get_agent_settings(agent_key: str, default_model: str) -> dict:
    """Returnerar {"provider", "model"} för en underagent, med fallback
    till huvud-AI:ns provider och till `default_model`."""
    config = load_config()
    agent_cfg = (config.get("agents") or {}).get(agent_key) or {}

    provider = agent_cfg.get("provider") or config.get("provider", "ollama") or "ollama"
    model = agent_cfg.get("model") or default_model

    return {"provider": provider, "model": model}


def get_all_configured_providers() -> set:
    """Alla providers som faktiskt används just nu - huvud-AI:n plus
    varje underagent som fått en egen provider satt. Används för att
    visa "har API-nyckel?" per provider i settings-widgeten."""
    config = load_config()
    providers = {config.get("provider", "ollama") or "ollama"}
    for agent_cfg in (config.get("agents") or {}).values():
        if isinstance(agent_cfg, dict) and agent_cfg.get("provider"):
            providers.add(agent_cfg["provider"])
    return providers


def make_chat_model(provider: str, model: str, temperature: float = 0.7, **extra):
    """Bygger en langchain chat-modell för valfri provider - samma
    logik som main.py:s make_agent() använde tidigare (bara för
    huvud-AI:n), nu delad så Approval/Edit/Research/Code AI kan
    använda vilken provider de vill, inte bara Ollama."""
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            temperature=temperature,
            **extra,
        )

    from langchain.chat_models import init_chat_model

    api_key = os.environ.get(get_api_key_env_name(provider))

    # num_ctx/num_predict/reasoning m.m. är Ollama-specifika kwargs -
    # skicka bara med det API-providers faktiskt förstår.
    extra = {k: v for k, v in extra.items() if k in ("max_tokens",)}

    return init_chat_model(
        model,
        model_provider=provider,
        temperature=temperature,
        api_key=api_key,
        **extra,
    )


def get_api_key_env_name(provider: str) -> str:
    """Namnet på den .env-variabel som ska innehålla API-nyckeln för
    `provider`. Användaren kan döpa om den fritt via settings-widgeten
    (config.json: api_key_envs.<provider>) - annars föreslås ett
    standardnamn."""
    configured = get_config_value(f"api_key_envs.{provider}")
    if configured:
        return configured
    return (API_PROVIDERS.get(provider) or {}).get("default_key_env") or f"{provider.upper()}_API_KEY"


def has_api_key(provider: str) -> bool:
    """True om .env-variabeln som är inställd för `provider` faktiskt
    innehåller något just nu."""
    env_name = get_api_key_env_name(provider)
    return bool(env_name and os.environ.get(env_name))


def check_model(provider: str, model: str) -> dict:
    """Kollar om `model` faktiskt finns hos `provider` just nu.

    - ollama: frågar den lokala Ollama-servern (samma lista som
      get_ollama_models()).
    - API-providers: listar modeller via providerns REST-API med
      nyckeln från .env-variabeln som är inställd för providern, och
      letar efter en exakt träff.

    Returnerar alltid {"ok": bool, "message": str} - kastar aldrig.
    """
    model = (model or "").strip()
    if not model:
        return {"ok": False, "message": "Inget modellnamn angivet."}

    try:
        if provider == "ollama":
            names = get_ollama_models()
            if model in names:
                return {"ok": True, "message": f"'{model}' finns i din lokala Ollama."}
            return {
                "ok": False,
                "message": f"Hittar inte '{model}' i din lokala Ollama ({len(names)} modeller installerade).",
            }

        env_name = get_api_key_env_name(provider)
        api_key = os.environ.get(env_name)
        if not api_key:
            return {"ok": False, "message": f"Ingen API-nyckel hittad i .env-variabeln '{env_name}'."}

        if provider in _OPENAI_STYLE_BASE_URLS:
            base = _OPENAI_STYLE_BASE_URLS[provider]
            resp = requests.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            ids = [m.get("id") for m in resp.json().get("data", [])]
            if model in ids:
                return {"ok": True, "message": f"'{model}' finns hos {provider}."}
            return {
                "ok": False,
                "message": f"Hittar inte '{model}' hos {provider} ({len(ids)} modeller tillgängliga för din nyckel).",
            }

        if provider == "anthropic":
            resp = requests.get(
                f"https://api.anthropic.com/v1/models/{model}",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"ok": True, "message": f"'{model}' finns hos Anthropic."}
            if resp.status_code == 404:
                return {"ok": False, "message": f"Hittar inte '{model}' hos Anthropic."}
            resp.raise_for_status()

        if provider == "google_genai":
            resource = model if model.startswith("models/") else f"models/{model}"
            resp = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/{resource}",
                params={"key": api_key},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"ok": True, "message": f"'{model}' finns hos Google."}
            if resp.status_code == 404:
                return {"ok": False, "message": f"Hittar inte '{model}' hos Google."}
            resp.raise_for_status()

        return {"ok": False, "message": f"Vet inte hur man kollar modeller hos providern '{provider}' än."}

    except requests.exceptions.RequestException as e:
        return {"ok": False, "message": f"Kunde inte nå {provider}: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"Fel vid kontroll: {e}"}