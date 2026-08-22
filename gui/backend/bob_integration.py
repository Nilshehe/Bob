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
    return (
        "Du har kontroll över ett dynamiskt hologram-GUI. "
        "Du kan skapa, flytta, ändra och ta bort element samt hantera "
        "flera fönster över flera skärmar genom verktygen nedan.\n\n"
        + ToolRegistry.system_prompt_snippet()
    )