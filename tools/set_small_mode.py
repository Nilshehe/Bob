from langchain_core.tools import tool
from config_manager import load_config, save_config
import asyncio
from main import event_loop_instance, event_queue
from gui.backend.gui_server import gui_server

@tool
def set_small_mode(enabled: bool) -> str:
    """Set Small Mode on or off.
    When enabled, the GUI is minimized (only Bob circle visible).
    When disabled, the GUI shows all elements normally.
    """
    config = load_config()
    config["small_mode"] = bool(enabled)
    save_config(config)
    # Broadcast the change to all connected GUI clients
    if gui_server.manager:
        gui_server.manager.broadcast({
            "type": "small_mode_update",
            "enabled": bool(enabled)
        })
    return f"Small Mode set to {enabled}"
