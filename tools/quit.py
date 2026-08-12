import os
from langchain_core.tools import tool


@tool
def shutdown_ai(reason: str = "Användaren bad om avstängning") -> str:
    """Stänger av AI-agenten helt. Använd ENDAST när användaren uttryckligen
    ber att programmet ska avslutas/stängas av (t.ex. 'stäng av dig själv',
    'avsluta programmet'). Kräver ingen bekräftelse - avslutar direkt."""
    print(f"\n[Bob] Stänger av: {reason}")
    os.exit(0)