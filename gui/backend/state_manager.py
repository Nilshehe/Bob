"""
state_manager.py
Sparar hela GUI-tillståndet (fönster + element) till en JSON-fil så att
Bob:s GUI kan återställas exakt som det var, session efter session.
"""
import json
import threading
from pathlib import Path
from typing import Any, Dict

STATE_FILE = Path(__file__).parent.parent / "state" / "gui_state.json"

DEFAULT_STATE = {
    "windows": {},   # window_id -> {title, x, y, w, h, screen}
    "elements": {},  # element_id -> {type, window_id, x, y, w, h, visible, label, props}
}


class StateManager:
    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self._lock = threading.Lock()
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_STATE))

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    # ---- fönster ----
    def upsert_window(self, window_id: str, **fields):
        self.state["windows"].setdefault(window_id, {})
        self.state["windows"][window_id].update({k: v for k, v in fields.items() if v is not None})
        self.save()

    def remove_window(self, window_id: str):
        self.state["windows"].pop(window_id, None)
        # ta bort elementen som hörde till fönstret också
        self.state["elements"] = {
            eid: e for eid, e in self.state["elements"].items() if e.get("window_id") != window_id
        }
        self.save()

    # ---- element ----
    def upsert_element(self, element_id: str, **fields):
        self.state["elements"].setdefault(element_id, {})
        self.state["elements"][element_id].update({k: v for k, v in fields.items() if v is not None})
        self.save()

    def remove_element(self, element_id: str):
        self.state["elements"].pop(element_id, None)
        self.save()

    def get_element(self, element_id: str):
        return self.state["elements"].get(element_id)

    def all_elements_for_window(self, window_id: str):
        return {eid: e for eid, e in self.state["elements"].items() if e.get("window_id") == window_id}


state = StateManager()
