"""
theme.py
Centralt Bob-tema (GUI-specen punkt 24-30, 59-60).

Ett enda "accent"-färgvärde styr hela temat. Övriga färger (background,
surface, text, muted) räknas fram utifrån accenten så att temat alltid
hänger ihop även när bara en färg byts. Semantiska färger (error/warning/
success/info) är fasta och påverkas INTE av accentbytet (punkt 27).

Temat skickas till frontend som CSS-variabler i ett "theme_state"-
websocket-meddelande (se gui_server.py) och sparas persistent i
gui_state.json via state_manager.py.
"""
import colorsys
import re
from typing import Dict

from gui.backend.state_manager import state

DEFAULT_ACCENT = "#00eaff"

SEMANTIC = {
    "error": "#ff4d4f",
    "warning": "#ffcc33",
    "success": "#33d17a",
    "info": "#5aa9ff",
}

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{3}$|^#?[0-9a-fA-F]{6}$")


def is_valid_hex(value: str) -> bool:
    return bool(_HEX_RE.match(value or ""))


def _hex_to_rgb(hexcolor: str):
    hexcolor = hexcolor.lstrip("#")
    if len(hexcolor) == 3:
        hexcolor = "".join(c * 2 for c in hexcolor)
    return tuple(int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb)


def _rgb_to_hue_deg(rgb) -> float:
    r, g, b = (c / 255 for c in rgb)
    h, _l, _s = colorsys.rgb_to_hls(r, g, b)
    return round(h * 360, 1)


def _shade(rgb, lightness_factor: float) -> str:
    """Bevarar hue/saturation, skalar bara ljusheten (samma princip som
    färgtransformen i punkt 26/59 - relativ ljushet bevaras)."""
    r, g, b = (c / 255 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(1.0, l * lightness_factor))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return _rgb_to_hex((r2 * 255, g2 * 255, b2 * 255))


def build_theme(accent: str) -> Dict[str, object]:
    if not is_valid_hex(accent):
        raise ValueError(f"Ogiltig färg: {accent!r} (använd t.ex. '#00eaff' eller '#f0a')")

    accent = accent if accent.startswith("#") else "#" + accent
    rgb = _hex_to_rgb(accent)

    return {
        "accent": accent,
        "accent_hue": _rgb_to_hue_deg(rgb),
        "background": _shade(rgb, 0.06),
        "surface": _shade(rgb, 0.12),
        "text": _shade(rgb, 1.9),
        "muted": _shade(rgb, 0.7),
        **SEMANTIC,
    }


def get_theme() -> Dict[str, object]:
    accent = state.state.get("theme", {}).get("accent", DEFAULT_ACCENT)
    try:
        return build_theme(accent)
    except ValueError:
        # Gammal/trasig state - fall tillbaka till default istället för
        # att krascha uppstarten.
        return build_theme(DEFAULT_ACCENT)


def set_accent(accent: str) -> Dict[str, object]:
    new_theme = build_theme(accent)  # validerar/kastar innan vi sparar något
    state.state["theme"] = {"accent": new_theme["accent"]}
    state.save()
    return new_theme
