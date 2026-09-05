from langchain_core.tools import tool
from config_manager import load_config
import time

# Store the start time when the module is loaded
_START_TIME = time.time()

def _get_context_monitor_string() -> str:
    config = load_config()
    max_tokens = config.get("num_ctx", 4096)  # default if not set
    # For now, we don't have real token usage, so we show a placeholder
    used_tokens = "?"
    remaining = "?"
    percentage = "?"
    
    current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    runtime_seconds = int(time.time() - _START_TIME)
    hours, remainder = divmod(runtime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    runtime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    return (f"Context Monitor: max_tokens={max_tokens}, used_tokens={used_tokens}, "
            f"remaining={remaining}, percentage={percentage}%\n"
            f"Current time: {current_time}\n"
            f"Runtime: {runtime_str}")

@tool
def get_context_monitor(_: str = '') -> str:
    """Get the current context window usage and runtime information."""
    return _get_context_monitor_string()
