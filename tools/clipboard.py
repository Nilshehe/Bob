import pyperclip
from langchain_core.tools import tool

@tool
def copy_to_clipboard(text: str) -> str:
    """Copy text to the clipboard."""
    pyperclip.copy(text)
    return f"'{text}' copied to clipboard."
