"""
main_gui.py
Startar/stänger Bob:s GUI-system.

GUI:t är AVSTÄNGT som standard - Bob startar det själv via
start_gui()-verktyget i gui_tools.py (eller stop_gui() för att stänga av
det igen). FastAPI-servern startas första gången start_gui() (eller
launch_gui()) anropas och lämnas sedan igång i bakgrunden - stop_gui()
stänger bara fönstren, så start_gui() kan öppna nya fönster direkt utan
att behöva starta om servern.

Kör:  python main_gui.py
för att starta GUI:t fristående (för utveckling/test) - det motsvarar då
launch_gui(), som blockerar tills alla fönster stängts.

pywebview:s händelseloop (webview.start()) måste köras i huvudtråden på
vissa plattformar (krävs på Windows/macOS). start_gui()/launch_gui_background()
kör den i en bakgrundstråd istället så att Bob kan anropa den som ett
vanligt verktyg mitt i en pågående konversation - fungerar på Linux, men
tänk på begränsningen ovan om du kör Bob på Windows/macOS.
"""
import threading

import uvicorn
import webview

import gui.backend.gui_tools  # noqa: F401  registrerar alla verktyg
import gui.backend.window_manager as wm
from gui.backend.state_manager import state

HOST, PORT = "127.0.0.1", 8642

_server_thread: threading.Thread = None
_gui_active = False
_lock = threading.Lock()


def _run_server():
    import gui.backend.gui_server as gui_server
    uvicorn.run(gui_server.app, host=HOST, port=PORT, log_level="warning")


def _ensure_server():
    """Startar FastAPI/uvicorn-servern i en bakgrundstråd om den inte
    redan kör. Trådsäkert, och billigt att anropa flera gånger."""
    global _server_thread
    if _server_thread is None or not _server_thread.is_alive():
        _server_thread = threading.Thread(target=_run_server, daemon=True)
        _server_thread.start()


def is_gui_running() -> bool:
    """True om minst ett GUI-fönster är öppet just nu."""
    return _gui_active


def start_gui():
    """Slå på GUI:t: startar servern (om den inte redan kör) och öppnar
    Bobs fönster (återställer tidigare fönster om det finns några
    sparade, annars ett nytt huvudfönster). Blockerar inte anroparen -
    webview:s händelseloop körs i en egen bakgrundstråd. No-op om GUI:t
    redan är igång."""
    global _gui_active

    with _lock:
        if _gui_active:
            return
        _ensure_server()
        if state.state["windows"]:
            wm.restore_windows()
        else:
            wm.create_window(title="Bob", window_id="main", width=1000, height=650)
        _gui_active = True

    def _run_webview():
        global _gui_active
        webview.start()
        # webview.start() returnerar när alla fönster stängts (via
        # stop_gui, eller att användaren stänger dem för hand).
        with _lock:
            _gui_active = False

    threading.Thread(target=_run_webview, daemon=True, name="gui-webview").start()


def stop_gui():
    """Slå av GUI:t: stänger alla öppna fönster. Servern (uvicorn) lämnas
    igång i bakgrunden så start_gui() kan öppna nya fönster igen direkt
    utan att behöva starta om något. No-op om GUI:t redan är avstängt."""
    global _gui_active

    with _lock:
        if not _gui_active:
            return
        wm.close_all_windows()
        _gui_active = False


def launch_gui():
    """Kör GUI:t blockerande i anropande tråd (kräver huvudtråden på
    Windows/macOS). Används av `python main_gui.py` för fristående
    utveckling/test. Bob:s riktiga process ska istället låta GUI:t
    börja avstängt och styra det via start_gui()/stop_gui()."""
    global _gui_active

    _ensure_server()
    if state.state["windows"]:
        wm.restore_windows()
    else:
        wm.create_window(title="Bob", window_id="main", width=1000, height=650)
    _gui_active = True
    webview.start()
    with _lock:
        _gui_active = False


def launch_gui_background():
    """Gammalt namn - motsvarar nu start_gui()."""
    start_gui()


if __name__ == "__main__":
    launch_gui()
