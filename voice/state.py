"""Central bridge for real voice/microphone state."""

from typing import Callable, Optional


_state_callback: Optional[Callable[..., None]] = None


def register_state_callback(callback: Callable[..., None]) -> None:
    global _state_callback
    _state_callback = callback


def broadcast_voice_state(**fields) -> None:
    callback = _state_callback

    if callback is None:
        return

    try:
        callback(**fields)
    except Exception:
        # GUI state must never break the voice system.
        pass