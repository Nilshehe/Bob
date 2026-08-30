"""
metrics.py
Generisk tidsserie-lagring för värden som uppdateras löpande (t.ex.
tokenanvändning per agent). Andra delar av systemet (agenter, GUI-verktyg)
kan registrera nya serier bara genom att anropa record()/add_tokens() -
inget annat behöver ändras.

Tokenanvändning:
- add_tokens(agent, tokens) anropas av varje agent (main, code_ai,
  research_ai, edit_ai) när ett LLM-svar är klart.
- En bakgrundstråd (start_ticker) skriver var 5:e sekund en ögonblicksbild
  {agent_namn: använda_tokens, "updated_at": ...} till
  funktioner/data/token_usage.json, och broadcastar samma ögonblicksbild
  till alla öppna GUI-fönster (typ "metrics_tick") så att graf-/
  stortext-widgets uppdateras live.
- Alla kända agenters totalsumma registreras också som skriv-/läsbara
  ToolRegistry-variabler ("Token Usage: <agent>"), så Bob själv kan läsa
  eller nollställa dem precis som Voice Mode (_setter-mönstret).
"""
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).parent / "data"
TOKEN_USAGE_FILE = DATA_DIR / "token_usage.json"

TICK_SECONDS = 5
HISTORY_MAXLEN = 2000  # per serie, ~ knappt 3h historik vid 5s-tick

KNOWN_AGENTS = ["main", "code_ai", "research_ai", "edit_ai"]

_lock = threading.Lock()
_totals: Dict[str, int] = {agent: 0 for agent in KNOWN_AGENTS}
_series: Dict[str, deque] = {}  # serie_namn -> deque[{"t": epoch, "v": value}]
_ticker_started = False


# ---------------------------------------------------------------------
# Generella tidsserier
# ---------------------------------------------------------------------

def record(series_name: str, value) -> None:
    """Lägg en punkt (nu, value) till en godtycklig namngiven serie."""
    with _lock:
        buf = _series.setdefault(series_name, deque(maxlen=HISTORY_MAXLEN))
        buf.append({"t": time.time(), "v": value})


def get_series(series_name: str, since: Optional[float] = None) -> List[dict]:
    with _lock:
        buf = list(_series.get(series_name, ()))
    if since is not None:
        buf = [p for p in buf if p["t"] >= since]
    return buf


def list_series() -> List[str]:
    with _lock:
        return list(_series.keys())


# ---------------------------------------------------------------------
# Tokenanvändning
# ---------------------------------------------------------------------

def add_tokens(agent: str, tokens: int) -> None:
    """Ackumulera `tokens` på agentens totalsumma. Ignorerar icke-positiva
    eller okända värden tyst - anropande kod ska inte behöva
    felhantera det här."""
    if not tokens:
        return
    with _lock:
        _totals[agent] = _totals.get(agent, 0) + int(tokens)


def record_llm_usage(agent: str, usage_metadata: Optional[dict]) -> None:
    """Bekvämlighetsfunktion för streamade AIMessageChunks: plockar ut
    total_tokens (fallback input+output) ur LangChains usage_metadata-dict
    om det finns, annars gör den ingenting."""
    if not usage_metadata:
        return
    total = usage_metadata.get("total_tokens")
    if total is None:
        total = (usage_metadata.get("input_tokens") or 0) + (usage_metadata.get("output_tokens") or 0)
    add_tokens(agent, total)


def record_result_messages(agent: str, messages) -> None:
    """Bekvämlighetsfunktion för agent.ainvoke()-resultat: summerar
    usage_metadata över samtliga meddelanden i resultatet och lägger till
    på agentens totalsumma en gång per körning."""
    total = 0
    for msg in messages or []:
        usage = getattr(msg, "usage_metadata", None)
        if not usage:
            continue
        total += usage.get("total_tokens") or ((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0))
    if total:
        add_tokens(agent, total)


def get_total(agent: str) -> int:
    with _lock:
        return _totals.get(agent, 0)


def reset_tokens(agent: str, value=0) -> None:
    """Setter-kompatibel (tar emot valfritt värde från ToolRegistry, men
    nollställer alltid - att "sätta" tokenanvändning är i praktiken bara
    meningsfullt som nollställning)."""
    with _lock:
        _totals[agent] = 0


def snapshot_totals() -> Dict[str, int]:
    with _lock:
        return dict(_totals)


# ---------------------------------------------------------------------
# Bakgrundstråd: skriv till json + broadcasta var 5:e sekund
# ---------------------------------------------------------------------

def _write_token_json(totals: Dict[str, int]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "agents": totals,
            "updated_at": time.time(),
        }
        TOKEN_USAGE_FILE.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _broadcast_tick(totals: Dict[str, int]) -> None:
    try:
        import gui.backend.gui_server as gui_server
        gui_server.manager.broadcast({
            "type": "metrics_tick",
            "tokens": totals,
            "t": time.time(),
        })
    except Exception:
        pass


def _tick_loop():
    while True:
        time.sleep(TICK_SECONDS)
        totals = snapshot_totals()
        for agent, total in totals.items():
            record(f"tokens:{agent}", total)
        _write_token_json(totals)
        _broadcast_tick(totals)


def start_ticker():
    """Startar bakgrundstråden en gång. Säkert att anropa flera gånger."""
    global _ticker_started
    with _lock:
        if _ticker_started:
            return
        _ticker_started = True
    t = threading.Thread(target=_tick_loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------
# Registrera tokenräknarna som Bob-styrbara variabler (samma _setter-
# mönster som Voice Mode i main.py) - importera den här modulen räcker,
# ingen extra registrering behövs i main.py.
# ---------------------------------------------------------------------

def _register_variables():
    from gui.backend.registry import ToolRegistry

    for agent in KNOWN_AGENTS:
        ToolRegistry.variable(
            f"Token Usage: {agent}",
            f"Ackumulerat antal tokens {agent} har använt sedan senaste nollställning. "
            f"Går att skriva till (valfritt värde) för att nollställa räknaren.",
            readable=True,
            writable=True,
            getter=lambda a=agent: get_total(a),
            setter=lambda value, a=agent: reset_tokens(a, value),
        )


try:
    _register_variables()
except Exception:
    # GUI-registryt kanske inte är importerbart ännu (t.ex. vid fristående
    # tester av den här filen) - inte kritiskt, bara token-variablerna
    # som saknas i systemprompten.
    pass
