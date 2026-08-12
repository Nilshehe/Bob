import os
import sys
from langchain_core.tools import tool


@tool
def shutdown_ai(reason: str = "User requested shutdown") -> str:
    """Shuts down the AI agent completely. USE ONLY when the user explicitly
    requests the program to exit/shutdown (e.g. 'shut yourself down',
    'exit the program'). Requires no confirmation - exits immediately."""
    print(f"\n[Bob] Shutting down: {reason}")
    sys.exit(0)