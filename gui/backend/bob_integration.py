"""
bob_integration.py
Gör om varje @tool i registry.py till en riktig LangChain StructuredTool.
"""

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import create_model

from gui.backend.registry import ToolRegistry
import gui.backend.gui_tools  # registrerar GUI-verktygen
import gui.backend.window_manager as wm


def _build_args_schema(spec):
    fields = {}

    for name, schema in spec.parameters.items():
        if not isinstance(schema, dict):
            schema = {"type": "string"}

        json_type = schema.get("type", "string")

        if json_type == "integer":
            python_type = int
        elif json_type == "number":
            python_type = float
        elif json_type == "boolean":
            python_type = bool
        elif json_type == "array":
            python_type = list
        elif json_type == "object":
            python_type = dict
        else:
            python_type = str

        if name in spec.required:
            default = ...
        else:
            default = None

        fields[name] = (python_type, default)

    return create_model(
        f"{spec.name}Args",
        **fields
    )


def get_langchain_tools():
    tools = []

    for spec in ToolRegistry._tools.values():
        args_schema = _build_args_schema(spec)

        def _make(spec=spec):
            def _run(**kwargs):
                try:
                    result = ToolRegistry.call(
                        spec.name,
                        **kwargs
                    )

                    if isinstance(result, str):
                        return result

                    return json.dumps(
                        result,
                        ensure_ascii=False
                    )

                except Exception as e:
                    return (
                        f"GUI TOOL ERROR\n"
                        f"Tool: {spec.name}\n"
                        f"Error: {type(e).__name__}\n"
                        f"Message: {e}"
                    )

            return _run

        tools.append(
            StructuredTool.from_function(
                func=_make(),
                name=spec.name,
                description=spec.description,
                args_schema=args_schema,
            )
        )

    return tools

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
        + ToolRegistry.system_prompt_snippet()
        + windows_block
    )