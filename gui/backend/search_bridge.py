"""
search_bridge.py
Speglar tools/sok_visible.py:s webbläsarsökningar till Bobs eget GUI
(guin) istället för att öppna ett separat, synligt OS-fönster.
Playwright körs headless i bakgrunden (se sok_visible.py); den här
modulen skapar/uppdaterar två fasta widgetar - en "browser"-iframe (visar
sidan Bob just tittat på) och en resultatpanel (visar textträffarna) - i
det första öppna GUI-fönstret.

All koppling till GUI:t är best-effort: om GUI:t är avstängt (inga
fönster öppna) eller något går fel ignoreras det tyst, så att själva
sökningen i sok_visible.py aldrig påverkas.
"""
from typing import Optional

BROWSER_ELEMENT_ID = "visible_search_browser"
RESULTS_ELEMENT_ID = "visible_search_results"


def _target_window_id() -> Optional[str]:
    """Fönstret sökwidgetarna ska visas i: samma fönster de redan finns i
    (om det fortfarande är öppet), annars det först öppnade fönstret.
    None om inget fönster är öppet (GUI:t avstängt)."""
    from gui.backend.state_manager import state

    existing = state.get_element(BROWSER_ELEMENT_ID)
    if existing and existing.get("window_id") in state.state["windows"]:
        return existing["window_id"]

    return next(iter(state.state["windows"]), None)


def show_search_in_gui(url: str, query: str, result_text: str) -> None:
    """Uppdatera (eller skapa) sök-widgetarna i guin: en browser-widget som
    navigerar till `url`, och en resultatpanel med `result_text`. No-op om
    GUI:t är avstängt just nu (webbläsaren körs då synlig på skärmen
    istället, se sok_visible.py:s _gui_is_on())."""
    import gui.backend.main_gui as main_gui

    if not main_gui.is_gui_running():
        return

    from gui.backend.state_manager import state
    import gui.backend.gui_tools as gui_tools

    window_id = _target_window_id()
    if window_id is None:
        return

    label = f"Sök: {query}"[:60]

    _upsert_html_component(
        gui_tools, state, window_id,
        element_id=BROWSER_ELEMENT_ID,
        component="browser",
        props={"url": url, "show_address_bar": True},
        label=label,
        x=40, y=40, w=640, h=420,
    )
    _upsert_html_component(
        gui_tools, state, window_id,
        element_id=RESULTS_ELEMENT_ID,
        component="panel",
        props={"text": result_text},
        label="Sökträffar",
        x=700, y=40, w=360, h=420,
    )


def _upsert_html_component(gui_tools, state, window_id, *, element_id, component,
                            props, label, x, y, w, h):
    if state.get_element(element_id):
        gui_tools.update_html.func(
            element_id=element_id,
            component=component,
            props=props,
            label=label,
        )
    else:
        gui_tools.create_html_component.func(
            component=component,
            window_id=window_id,
            x=x, y=y, w=w, h=h,
            label=label,
            element_id=element_id,
            props=props,
        )
