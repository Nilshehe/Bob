import json
from pathlib import Path
from threading import RLock
from typing import Any


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