"""
registry.py
Central Tool- & Variabel-registry för Bob:s GUI.

Det här är hela poängen: lägg till EN funktion, dekorera den med @tool(...),
och Bob får den automatiskt tillgänglig — utan att du behöver röra något
annat i systemet. Samma sak för variabler med @variable / ToolRegistry.variable.
"""
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON-schema "properties"
    required: List[str]
    func: Callable


@dataclass
class VariableSpec:
    name: str
    description: str
    readable: bool
    writable: bool
    getter: Optional[Callable] = None
    setter: Optional[Callable] = None


class ToolRegistry:
    _tools: Dict[str, ToolSpec] = {}
    _variables: Dict[str, VariableSpec] = {}

    # ---- registrering ----
    @classmethod
    def tool(cls, description: str, parameters: Optional[Dict[str, Any]] = None,
              required: Optional[List[str]] = None):
        """Dekorator: registrerar en Python-funktion som ett verktyg Bob kan anropa."""
        def decorator(func):
            name = func.__name__
            sig = inspect.signature(func)
            props = parameters or {}
            req = required if required is not None else [
                p.name for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty and p.name != "self"
            ]
            cls._tools[name] = ToolSpec(name, description, props, req, func)
            return func
        return decorator

    @classmethod
    def variable(cls, name: str, description: str, readable: bool = True,
                 writable: bool = False, getter: Optional[Callable] = None,
                 setter: Optional[Callable] = None):
        """Registrerar en variabel Bob får läsa och/eller ändra."""
        cls._variables[name] = VariableSpec(name, description, readable, writable, getter, setter)

    # ---- anrop ----
    @classmethod
    def call(cls, name: str, **kwargs):
        if name not in cls._tools:
            raise ValueError(f"Okänt verktyg: {name}")
        return cls._tools[name].func(**kwargs)

    @classmethod
    def get_variable(cls, name: str):
        spec = cls._variables.get(name)
        if not spec or not spec.readable:
            raise ValueError(f"Variabeln '{name}' finns inte eller är inte läsbar")
        return spec.getter() if spec.getter else None

    @classmethod
    def set_variable(cls, name: str, value: Any):
        spec = cls._variables.get(name)
        if not spec or not spec.writable:
            raise ValueError(f"Variabeln '{name}' finns inte eller är inte skrivbar")
        if spec.setter:
            spec.setter(value)

    # ---- introspektion åt Bob (LLM function-calling) ----
    @classmethod
    def list_tools(cls) -> List[Dict[str, Any]]:
        """OpenAI/Ollama-kompatibelt function-calling-schema."""
        out = []
        for spec in cls._tools.values():
            out.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "type": "object",
                        "properties": spec.parameters,
                        "required": spec.required,
                    },
                },
            })
        return out

    @classmethod
    def list_variables(cls) -> List[Dict[str, Any]]:
        return [
            {"name": v.name, "description": v.description,
             "readable": v.readable, "writable": v.writable}
            for v in cls._variables.values()
        ]

    @classmethod
    def system_prompt_snippet(cls) -> str:
        """Läsbar capability-lista att klistra in i Bob:s systemprompt."""
        lines = ["## Tillgängliga GUI-verktyg", ""]
        for spec in cls._tools.values():
            params = ", ".join(spec.parameters.keys())
            lines.append(f"- **{spec.name}({params})** — {spec.description}")
        if cls._variables:
            lines.append("\n## Tillgängliga variabler")
            for v in cls._variables.values():
                rw = []
                if v.readable:
                    rw.append("läs")
                if v.writable:
                    rw.append("skriv")
                lines.append(f"- **{v.name}** ({'/'.join(rw)}) — {v.description}")
        return "\n".join(lines)


tool = ToolRegistry.tool
variable = ToolRegistry.variable
