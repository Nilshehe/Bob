"""
main.py
Startar Bob:s GUI-system fristående (för utveckling/test).

Kör:  python main.py

FastAPI-servern körs i en bakgrundstråd; pywebview:s händelseloop måste
köras i huvudtråden (krävs på Windows/macOS) så den startas sist.

I den riktiga Bob-processen anropar du istället launch_gui() från din
egen main-fil, i huvudtråden, efter att LangGraph-agenten satts upp.
"""
import threading

import uvicorn
import webview

import gui.backend.gui_tools  # noqa: F401  registrerar alla verktyg
import gui.backend.window_manager as wm
from gui.backend.state_manager import state

HOST, PORT = "127.0.0.1", 8642


def _run_server():
    import gui.backend.gui_server as gui_server
    uvicorn.run(gui_server.app, host=HOST, port=PORT, log_level="warning")


def launch_gui():
    """Anropa denna en gång från huvudtråden i Bob:s process."""
    t = threading.Thread(target=_run_server, daemon=True)
    t.start()

    if state.state["windows"]:
        wm.restore_windows()
    else:
        wm.create_window(title="Bob", window_id="main", width=1000, height=650)

    webview.start()


def launch_gui_background():
    thread = threading.Thread(
        target=launch_gui,
        daemon=True
    )
    thread.start()
    return thread


if __name__ == "__main__":
    launch_gui()
