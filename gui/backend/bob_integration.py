"""
bob_integration.py
Samlar ihop GUI-verktygen (redan riktiga LangChain-verktyg, definierade
med @tool i gui_tools.py) och bygger den bit av systemprompten som
beskriver GUI:t för Bob.
"""

from langchain_core.tools import BaseTool

from gui.backend.registry import ToolRegistry
import gui.backend.gui_tools as gui_tools_module  # registrerar GUI-verktygen
import gui.backend.window_manager as wm


def get_langchain_tools():
    """Plockar upp alla LangChain-verktyg som är definierade i
    gui_tools.py (dvs. alla modulnivå-objekt skapade med @tool). Lägg
    till en ny @tool-funktion där, så dyker den upp här automatiskt -
    inget att röra i den här filen."""
    return [
        obj for obj in vars(gui_tools_module).values()
        if isinstance(obj, BaseTool)
    ]


def _tools_snippet(tools) -> str:
    """Läsbar capability-lista över GUI-verktygen att klistra in i Bob:s
    systemprompt (utöver det schema Bob redan får via function-calling)."""
    lines = ["## Tillgängliga GUI-verktyg", ""]
    for t in tools:
        params = ", ".join(t.args.keys())
        lines.append(f"- **{t.name}({params})** — {t.description}")
    return "\n".join(lines)


def gui_system_prompt() -> str:
    windows = wm.get_windows()

    if windows:
        window_lines = "\n".join(
            f"- window_id=\"{w['window_id']}\" title=\"{w.get('title')}\" "
            f"({w.get('element_count', 0)} element)"
            for w in windows
        )
        windows_block = (
            "\n\nCurrently open windows (use these exact window_id values, "
            "never guess or invent one — call list_windows if you need to "
            "re-check):\n" + window_lines
        )
    else:
        windows_block = (
            "\n\nNo windows are currently open. Call create_window first "
            "and use the window_id it returns — do not guess one."
        )

    return (
        "You control a dynamic holographic GUI. You can create, move, "
        "modify, and delete elements, as well as manage multiple windows "
        "across multiple screens using the tools below."
        "\n\nAGENT MONITOR:\n"
        "Use create_agent_monitor when the user wants to see a background "
        "agent working live. Supported agents are code_ai, research_ai, "
        "and edit_ai. For example, when the user says 'visa vad Research AI "
        "gör' or 'lägg till en monitor för Research AI', create a "
        "research_ai agent monitor in an existing window. Do not guess a "
        "window_id; use one of the exact currently open window_id values "
        "listed above."
        + "\n\n" + _tools_snippet(get_langchain_tools())
        + ToolRegistry.system_prompt_snippet()
        + windows_block
    )
