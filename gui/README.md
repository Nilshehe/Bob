# Bob – AI-styrt dynamiskt GUI

Ett fristående, utbyggbart GUI-system som Bob kan styra helt genom
funktionsanrop (tools). Byggt på FastAPI + WebSocket (backend) och
pywebview + Three.js (frontend), med blå hologramstil.

## Arkitektur

```
bob_gui/
  backend/
    registry.py         # Central tool- & variabel-registry (kärnan)
    state_manager.py     # Sparar/laddar GUI-tillstånd till state/gui_state.json
    gui_server.py         # FastAPI + WebSocket, skickar kommandon till frontend
    window_manager.py    # pywebview-fönster + screeninfo (multi-monitor)
    gui_tools.py          # De faktiska verktygen: create_element, move_window, osv.
    bob_integration.py   # Gör om registry-verktygen till LangChain-tools
    main.py               # Fristående startpunkt (för test/utveckling)
  frontend/
    index.html
    style.css             # Hologram-blå tema
    app.js                 # Tar emot websocket-kommandon, bygger DOM dynamiskt + Three.js
  state/
    gui_state.json        # Sparat tillstånd (skapas/uppdateras automatiskt)
  requirements.txt
```

## Köra fristående (testa GUI:t utan Bob)

```bash
cd bob_gui/backend
pip install -r ../requirements.txt
python main.py
```

Ett fönster öppnas. Testa t.ex. i en Python-shell samtidigt (eller lägg
in i main.py) för att se att verktygen fungerar:

```python
from gui_tools import create_element, move_element, get_screens
create_element(element_type="status", window_id="main", label="Edit AI", x=40, y=40)
get_screens()
```

## Koppla in i Bob (LangGraph-agenten)

```python
# I Bob:s huvudprocess, i huvudtråden:
import sys
sys.path.append("bob_gui/backend")

from main import launch_gui          # startar server + fönster
from bob_integration import get_langchain_tools, gui_system_prompt

gui_tools = get_langchain_tools()     # lista av LangChain StructuredTool
system_prompt = base_prompt + "\n\n" + gui_system_prompt()

# lägg gui_tools till din befintliga toolslista i LangGraph-agenten
all_tools = [*existing_bob_tools, *gui_tools]

# kör launch_gui() i huvudtråden (pywebview kräver det på Windows/macOS)
# — starta din LangGraph-agent i en egen bakgrundstråd istället.
```

Bob:s LLM ser nu verktygen `create_element`, `remove_element`,
`move_element`, `update_element`, `create_window`, `move_window`,
`close_window`, `get_screens`, `load_3d_model`, `move_3d_model` och
`set_3d_model_style` – med scheman genererade direkt från
`registry.py`, redo för Ollamas function-calling.

### Exempel-flöde

> "Hej Bob, jag vill inte längre ha kvar statusen för Edit AI."

Bob letar upp element-id:t (t.ex. via en egen `list_elements`-variabel du
lägger till, se nedan) och anropar:

```python
remove_element(element_id="status_edit_ai", permanent=False)
```

> "Bob, öppna 3D-modellen och rendera den här, flytta den till mitten."

```python
r = load_3d_model(model_path="C:/models/reaktor.glb", window_id="main")
move_3d_model(element_id=r["element_id"], x=0, y=0, z=0)
set_3d_model_style(element_id=r["element_id"], color="#00eaff", wireframe=True)
```

## Lägga till en helt ny funktion

Öppna `gui_tools.py`, skriv en funktion, dekorera den:

```python
@tool("Ändra bakgrundens ljusstyrka i hologram-GUI:t.",
      parameters={"level": {"type": "number"}}, required=["level"])
def set_brightness(level):
    gui_server.manager.broadcast({"type": "set_brightness", "level": level})
    return {"ok": True}
```

Lägg sedan till motsvarande hantering i `app.js` → `handleMessage()`.
Klart – Bob ser verktyget automatiskt nästa gång `gui_system_prompt()`
eller `get_langchain_tools()` anropas. Ingenting annat behöver ändras.

## Lägga till en ny variabel Bob får läsa/ändra

```python
from registry import variable

_volym = {"value": 50}
variable(
    "volym",
    "Systemvolym i procent (0-100).",
    readable=True, writable=True,
    getter=lambda: _volym["value"],
    setter=lambda v: _volym.update(value=v),
)
```

Bob läser/ändrar via `ToolRegistry.get_variable("volym")` respektive
`ToolRegistry.set_variable("volym", 70)`.

## Persistens

Allt (fönster, positioner, storlekar, synlighet, skärmtillhörighet,
skapade element) sparas löpande till `state/gui_state.json` varje gång
något ändras. Vid omstart läser `main.py` in filen och återskapar
fönster + element automatiskt via `window_manager.restore_windows()`
och websocket-`sync`-meddelandet i `gui_server.py`.

## Multi-fönster & multi-monitor

- `create_window(screen=1)` öppnar ett nytt fönster på skärm index 1.
- `get_screens()` returnerar upplösning, position och vilken som är
  primär för varje ansluten skärm (via `screeninfo`).
- `move_window(window_id, screen=0)` flyttar ett befintligt fönster.

## Att bygga vidare

- **Lokala 3D-tillgångar / offline:** `index.html` laddar Three.js från
  CDN just nu — lägg egna kopior i `frontend/vendor/` om du vill köra
  helt offline (matchar din no-external-deps-preferens).
- **Element-lista åt Bob:** lägg gärna till ett `list_elements()`-verktyg
  i `gui_tools.py` som returnerar `state.state["elements"]`, så Bob kan
  slå upp element-id utifrån etikett innan den anropar `remove_element`
  m.fl. (ett par rader, samma mönster som övriga verktyg).
- **Fler elementtyper:** lägg till en ny `case` i `buildBody()` i
  `app.js` och en motsvarande `enum`-post i `create_element`s schema.
