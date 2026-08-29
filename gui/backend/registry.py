"""
registry.py
Variabel-registry för Bob:s GUI.

GUI-verktygen (funktioner Bob kan anropa) definieras numera direkt som
riktiga LangChain-verktyg i gui_tools.py (@tool-dekoratorn från
langchain_core.tools) - de går via gui.backend.bob_integration.get_langchain_tools()
istället för att gå omvägen via ett eget registry/schema-bygge.

Den här modulen sköter bara VARIABLER: saker Bob kan läsa och/eller
skriva som inte är ett "anrop" i vanlig mening, t.ex. Voice Mode eller
hologram_color. Lägg till en ny med ToolRegistry.variable(...)/@variable,
så dyker den upp automatiskt i systemprompt-snippeten nedan.
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class VariableSpec:
    name: str
    description: str
    readable: bool
    writable: bool
    getter: Optional[Callable] = None
    setter: Optional[Callable] = None


class ToolRegistry:
    _variables: Dict[str, VariableSpec] = {}

    # ---- registrering ----
    @classmethod
    def variable(cls, name: str, description: str, readable: bool = True,
                 writable: bool = False, getter: Optional[Callable] = None,
                 setter: Optional[Callable] = None):
        """Registrerar en variabel Bob får läsa och/eller ändra."""
        cls._variables[name] = VariableSpec(name, description, readable, writable, getter, setter)

    # ---- anrop ----
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
    def list_variables(cls) -> List[Dict[str, Any]]:
        return [
            {"name": v.name, "description": v.description,
             "readable": v.readable, "writable": v.writable}
            for v in cls._variables.values()
        ]

    @classmethod
    def system_prompt_snippet(cls) -> str:
        """Läsbar capability-lista över variabler att klistra in i Bob:s
        systemprompt (verktygslistan byggs separat, se bob_integration.py)."""
        if not cls._variables:
            return ""

        lines = ["\n## Tillgängliga variabler"]
        for v in cls._variables.values():
            rw = []
            if v.readable:
                rw.append("läs")
            if v.writable:
                rw.append("skriv")
            lines.append(f"- **{v.name}** ({'/'.join(rw)}) — {v.description}")
        return "\n".join(lines)


variable = ToolRegistry.variable
