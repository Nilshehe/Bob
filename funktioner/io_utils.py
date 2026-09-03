"""
io_utils.py
Hjälpfunktioner för Bobs I/O, utflyttade från main.py för att hålla den
filen till orkestrering (event-loop, agent-anrop, avbrott) istället för
generella hjälpfunktioner:

- En avbrytningsbar stdin-läsare (används av input_loop i main.py för
  att kunna avbryta en pågående inputrad om Voice Mode slås på medan
  den väntar på en textrad).
- Spegling av Bobs svarsström (text/reasoning/tool-anrop/röstläge) till
  GUI:ts live-widget, som terminalutskriften (formater()) redan gör.
"""
import queue
import sys
import threading

from funktioner.formater import formater
import gui.backend.gui_server as gui_server


# ---------------------------------------------------------------------
# Avbrytningsbar stdin-läsning
# ---------------------------------------------------------------------
_stdin_queue: "queue.Queue[str]" = queue.Queue()
_stdin_reader_started = False
_stdin_reader_lock = threading.Lock()


def _stdin_reader_loop():
    while True:
        line = sys.stdin.readline()
        if line == "":
            return
        _stdin_queue.put(line.rstrip("\n"))


def ensure_stdin_reader():
    global _stdin_reader_started
    with _stdin_reader_lock:
        if _stdin_reader_started:
            return
        _stdin_reader_started = True
    threading.Thread(target=_stdin_reader_loop, daemon=True).start()


def read_line_cancelable(prompt: str, stop_event: threading.Event, poll_interval: float = 0.25):
    """Läs en rad från stdin, men avbryt (returnera None) om `stop_event`
    sätts medan vi väntar - t.ex. när Voice Mode slås på mitt i en
    pågående textinmatning."""
    ensure_stdin_reader()
    print(prompt, end="", flush=True)
    while True:
        if stop_event.is_set():
            print()  # ny rad så prompten inte hänger kvar mitt i raden
            return None
        try:
            return _stdin_queue.get(timeout=poll_interval)
        except queue.Empty:
            continue


# ---------------------------------------------------------------------
# Spegla Bobs svarsström/röstläge till GUI:ts live-widget
# ---------------------------------------------------------------------
def broadcast_voice_state(**fields):
    """Skickar röstläges-status (på/av, vaken, lyssnar, ljudnivå) till alla
    öppna GUI-fönster, så den permanenta text-inputen kan gömmas och
    väckningscirkeln kan animeras i realtid. No-op (tyst) om GUI:t är
    avstängt eller inget fönster är anslutet."""
    try:
        gui_server.manager.broadcast({"type": "voice_state", **fields})
    except Exception:
        pass


def broadcast_agent_stream(node_type, content):
    """Speglar samma svarsström som formater() skriver ut i terminalen till
    GUI:ts live-svarswidget (text/reasoning/tool_call_chunk/interrupt).
    Skickas bara till de fönster som är valda i svarswidgetens
    fönster-filter (tom lista = alla fönster)."""
    if not content:
        return
    try:
        gui_server.broadcast_agent_stream({
            "type": "agent_stream",
            "node_type": node_type,
            "content": content,
        })
    except Exception:
        pass


def emit(response, node_type):
    """Skriver ut i terminalen (som förut) och speglar samtidigt till
    GUI:ts live-svarswidget."""
    formater(response, node_type)
    broadcast_agent_stream(node_type, response)
