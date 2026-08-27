"""
window_manager.py
Äger de faktiska OS-fönstren (via pywebview) och känner till anslutna
skärmar (via screeninfo). Det här är det som gör "flytta fönstret till
min andra skärm" möjligt.
"""
import uuid
from typing import Optional

import webview
from screeninfo import get_monitors

from gui.backend.state_manager import state

SERVER_URL = "http://127.0.0.1:8642"
_windows = {}  # window_id -> webview.Window


def get_screens():
    screens = []
    for i, m in enumerate(get_monitors()):
        screens.append({
            "index": i,
            "name": m.name or f"Skärm {i}",
            "x": m.x, "y": m.y,
            "width": m.width, "height": m.height,
            "is_primary": bool(getattr(m, "is_primary", False)),
        })
    return screens


def get_windows():
    out = []
    for window_id, meta in state.state["windows"].items():
        out.append({
            "window_id": window_id,
            "title": meta.get("title"),
            "x": meta.get("x"), "y": meta.get("y"),
            "w": meta.get("w"), "h": meta.get("h"),
            "screen": meta.get("screen"),
            "element_count": len(state.all_elements_for_window(window_id)),
        })
    return out


def _resolve_screen_pos(screen: Optional[int], x: Optional[int], y: Optional[int]):
    if screen is None:
        return x, y
    screens = get_screens()
    if 0 <= screen < len(screens):
        s = screens[screen]
        x = s["x"] + 60 if x is None else x
        y = s["y"] + 60 if y is None else y
    return x, y


def create_window(title: str = "Bob", width: int = 900, height: int = 600,
                   x: Optional[int] = None, y: Optional[int] = None,
                   screen: Optional[int] = None, window_id: Optional[str] = None) -> str:
    window_id = window_id or str(uuid.uuid4())[:8]
    x, y = _resolve_screen_pos(screen, x, y)

    win = webview.create_window(
        title,
        url=f"{SERVER_URL}/?window_id={window_id}",
        width=width, height=height,
        x=x, y=y,
        background_color="#050912",
    )
    _windows[window_id] = win
    state.upsert_window(window_id, title=title, x=x, y=y, w=width, h=height, screen=screen)
    return window_id


def move_window(window_id: str, x: Optional[int] = None, y: Optional[int] = None,
                 screen: Optional[int] = None):
    win = _windows.get(window_id)
    if not win:
        raise ValueError(f"Okänt fönster: {window_id}")
    x, y = _resolve_screen_pos(screen, x, y)
    if x is not None and y is not None:
        win.move(x, y)
        state.upsert_window(window_id, x=x, y=y, screen=screen)


def resize_window(window_id: str, width: int, height: int):
    win = _windows.get(window_id)
    if not win:
        raise ValueError(f"Okänt fönster: {window_id}")
    win.resize(width, height)
    state.upsert_window(window_id, w=width, h=height)


def close_window(window_id: str):
    win = _windows.get(window_id)
    if win:
        win.destroy()
    _windows.pop(window_id, None)
    state.remove_window(window_id)


def restore_windows():
    """Återskapar varje fönster som fanns kvar vid senaste avstängningen."""
    for window_id, w in list(state.state["windows"].items()):
        win = webview.create_window(
            w.get("title", "Bob"),
            url=f"{SERVER_URL}/?window_id={window_id}",
            width=w.get("w", 900), height=w.get("h", 600),
            x=w.get("x"), y=w.get("y"),
            background_color="#050912",
        )
        _windows[window_id] = win
