from langchain_core.tools import tool
from config_manager import load_config
import time

# Store the start time when the module is loaded
_START_TIME = time.time()

def _get_context_indicator() -> str:
    config = load_config()
    max_tokens = config.get("num_ctx", 4096)  # default if not set
    # For now, we don't have real token usage, so we show a placeholder
    used_tokens = 0  # We don't have actual usage, so we set to 0
    remaining = max_tokens - used_tokens
    percentage = 0 if max_tokens == 0 else (used_tokens / max_tokens) * 100
    
    # Determine color based on percentage
    if percentage >= 100:
        color = "#FF0000"  # Red - auto-new-context triggered
        status = "AUTO-NEW-CONTEXT"
    elif percentage >= 99:
        color = "#FF4500"  # OrangeRed
        status = "WARNING 99%"
    elif percentage >= 95:
        color = "#FFA500"  # Orange
        status = "WARNING 95%"
    elif percentage >= 90:
        color = "#FFFF00"  # Yellow
        status = "WARNING 90%"
    else:
        color = "#FFFFFF"  # White
        status = "NORMAL"
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    runtime_seconds = int(time.time() - _START_TIME)
    hours, remainder = divmod(runtime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    return (f"Context Indicator: {status}\n"
            f"Color: {color}\n"
            f"Usage: {used_tokens}/{max_tokens} tokens ({percentage:.1f}%)\n"
            f"Current time: {current_time}\n"
            f"Runtime: {runtime_str}")

@tool
def get_context_indicator(_: str = '') -> str:
    """Get the current context window usage as a color indicator."""
    return _get_context_indicator()
