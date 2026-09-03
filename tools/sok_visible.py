import os
import re
import shutil
import queue
import threading
import requests
from pathlib import Path
from typing import Optional, Callable, Any
from urllib.parse import urlparse
 
from langchain.tools import tool
from playwright.sync_api import sync_playwright, Page
 
# ---------------------------------------------------------------------------
# Sandlåda: allt AI:n gör med filer sker under denna mapp
# ---------------------------------------------------------------------------
AI_FOLDER = Path("./ai_workspace").resolve()
DOWNLOAD_FOLDER = AI_FOLDER / "downloads"
AI_FOLDER.mkdir(parents=True, exist_ok=True)
DOWNLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
 
 
def _safe_path(relative_path: str) -> Path:
    """Join `relative_path` with `AI_FOLDER` and block directory traversal (../ or absolute paths)."""
    candidate = (AI_FOLDER / relative_path).resolve()
    if AI_FOLDER not in candidate.parents and candidate != AI_FOLDER:
        raise ValueError(
            f"Unauthorized path: '{relative_path}' is outside the sandbox {AI_FOLDER}"
        )
    return candidate
 
 
# ---------------------------------------------------------------------------
# Persistent browser-session som körs i EN dedikerad tråd. Om Bobs GUI
# (guin) är på körs den headless och speglas dit (search_bridge.py); om
# guin är avstängt körs den istället som ett vanligt, synligt fönster på
# skärmen (som innan guin-spegling fanns) - beslutet tas när
# webbläsartråden startas, se _gui_is_on()/_run(). Alla Playwright-anrop
# måste ske i den tråden -> vi skickar jobb till den via en kö istället
# för att röra page/browser direkt från tool-funktionerna.
# ---------------------------------------------------------------------------
def _gui_is_on() -> bool:
    """True om Bobs GUI (guin) är igång just nu. Avgör om
    webbläsarsessionen ska köras headless (speglad i guin) eller som ett
    vanligt, synligt OS-fönster (om guin är avstängt)."""
    try:
        import gui.backend.main_gui as main_gui
        return main_gui.is_gui_running()
    except Exception:
        return False


class _BrowserWorker:
    _instance: Optional["_BrowserWorker"] = None
    _instance_lock = threading.Lock()
 
    def __init__(self):
        self._cmd_q: "queue.Queue[tuple]" = queue.Queue()
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="browser-worker")
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._start_error:
            raise self._start_error
 
    def _run(self):
        try:
            headless = _gui_is_on()
            launch_kwargs = {"headless": headless}
            if not headless:
                # guin är avstängt - kör den vanliga, synliga
                # webbläsaren istället (samma beteende som innan
                # guin-speglingen fanns).
                launch_kwargs["args"] = ["--start-maximized"]

            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(
                    accept_downloads=True,
                    no_viewport=True,
                )
                page = context.new_page()
                self._ready.set()
 
                while True:
                    item = self._cmd_q.get()
                    if item is None:  # shutdown-signal
                        break
                    fn, args, kwargs, result_q = item
                    try:
                        result_q.put(("ok", fn(page, *args, **kwargs)))
                    except Exception as e:  # skicka felet tillbaka, krascha inte tråden
                        result_q.put(("err", e))
 
                browser.close()
        except Exception as e:
            self._start_error = e
            self._ready.set()
 
    def call(self, fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Run `fn(page, *args, **kwargs)` inside the browser thread and block until a result is available."""
        result_q: "queue.Queue[tuple]" = queue.Queue(maxsize=1)
        self._cmd_q.put((fn, args, kwargs, result_q))
        status, value = result_q.get()
        if status == "err":
            raise value
        return value
 
    def shutdown(self):
        self._cmd_q.put(None)
        self._thread.join(timeout=10)
        with _BrowserWorker._instance_lock:
            if _BrowserWorker._instance is self:
                _BrowserWorker._instance = None
 
    @classmethod
    def instance(cls) -> "_BrowserWorker":
        with cls._instance_lock:
            if cls._instance is None or not cls._instance._thread.is_alive():
                cls._instance = cls()
            return cls._instance
 
 
def close_browser():
    """Shut down the browser session completely (call on program exit to clean up)."""
    with _BrowserWorker._instance_lock:
        inst = _BrowserWorker._instance
    if inst:
        inst.shutdown()
 
 
@tool
def open_browser() -> str:
    """Start the browser session if it's not already running. Call this before using other browser tools.

    Runs headless and mirrors into Bob's GUI (guin) if guin is currently
    on; otherwise opens a normal, visible OS browser window instead
    (guin off = same behavior as before GUI mirroring existed).
    """
    _BrowserWorker.instance()
    return "Browser started and ready."
 
 
# ---------------------------------------------------------------------------
# Tool 1: sök på synlig webbsida
# ---------------------------------------------------------------------------
def _search_visible_webpage_impl(page: Page, url: str, query: str, max_matches: int) -> str:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(500)  # låt sidan rendera klart
    except Exception as e:
        return f"Could not open {url}: {e}"
 
    full_text = page.evaluate("document.body.innerText") or ""
    if not full_text.strip():
        _mirror_to_gui(url, query, "Page does not seem to contain any visible text.")
        return "Page does not seem to contain any visible text."

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = list(pattern.finditer(full_text))

    if not matches:
        _mirror_to_gui(url, query, f"No match for '{query}' on page {url}.")
        return f"No match for '{query}' on page {url}."

    results = []
    for m in matches[:max_matches]:
        start = max(0, m.start() - 100)
        end = min(len(full_text), m.end() + 100)
        snippet = full_text[start:end].strip().replace("\n", " ")
        results.append(f"...{snippet}...")

    # Markera visuellt på sidan (highlight första träffen) - körs på den
    # headless sidan och syns inte direkt, men träffarna nedan speglas till
    # guin via _mirror_to_gui.
    try:
        page.evaluate(
            """(q) => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while (node = walker.nextNode()) {
                    if (node.nodeValue.toLowerCase().includes(q.toLowerCase())) {
                        const el = node.parentElement;
                        if (el) { el.style.outline = '3px solid red'; el.scrollIntoView({block:'center'}); }
                        break;
                    }
                }
            }""",
            query,
        )
    except Exception:
        pass

    header = f"Found {len(matches)} matches for '{query}' ({url}), showing {len(results)}:\n"
    result_text = header + "\n---\n".join(results)
    _mirror_to_gui(url, query, result_text)
    return result_text


def _mirror_to_gui(url: str, query: str, result_text: str) -> None:
    """Speglar sökningen (sidan + träffarna) i Bobs eget GUI istället för
    ett separat, synligt OS-fönster. No-op om GUI:t är avstängt - får
    aldrig krascha själva sökningen."""
    try:
        from gui.backend import search_bridge
        search_bridge.show_search_in_gui(url=url, query=query, result_text=result_text)
    except Exception:
        pass
 
 
@tool
def search_visible_webpage(url: str, query: str, max_matches: int = 5) -> str:
    """Open `url` in a visible browser window and search the page text for `query`.

    Returns the passages (with surrounding context) where `query` appears.
    Use this when the user should be able to watch what the AI is browsing/searching live.

    Args:
        url: The page to open, e.g. "https://example.com"
        query: Text/phrase to search for on the page (case-insensitive)
        max_matches: Maximum number of matches to return (default 5)
    """
    return _BrowserWorker.instance().call(_search_visible_webpage_impl, url, query, max_matches)
 
 
# ---------------------------------------------------------------------------
# Tool 2: ladda ner fil till egen mapp (rör inte browsern -> ingen trådväxling behövs)
# ---------------------------------------------------------------------------
@tool
def download_file(url: str, filename: Optional[str] = None) -> str:
    """Download a file from `url` into the agent's download folder (ai_workspace/downloads).

    Args:
        url: Direct link to the file to download
        filename: Optional filename to save as. If omitted, the name is guessed from the URL.
    """
    if not filename:
        parsed = urlparse(url)
        filename = os.path.basename(parsed.path) or "downloaded_file"
 
    # sanera filnamn, tillåt inga path-separatorer
    filename = os.path.basename(filename)
    dest = _safe_path(f"downloads/{filename}")
    dest.parent.mkdir(parents=True, exist_ok=True)
 
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
    except Exception as e:
        return f"Download failed: {e}"
 
    size_kb = dest.stat().st_size / 1024
    return f"Downloaded to {dest.relative_to(AI_FOLDER)} ({size_kb:.1f} KB)"
 
 
# ---------------------------------------------------------------------------
# Tool 3: flytta fil inom egen mapp (rör inte browsern)
# ---------------------------------------------------------------------------
@tool
def move_file(source: str, destination: str) -> str:
    """Move/rename a file within the agent's sandbox folder (ai_workspace).

    Both `source` and `destination` are paths relative to `ai_workspace`, e.g.
    `source="downloads/report.pdf"`, `destination="done/report_2026.pdf"`.
    This cannot move files outside of `ai_workspace`.

    Args:
        source: Relative path to the file to move
        destination: Relative path to move the file to (parent directories are created as needed)
    """
    try:
        src_path = _safe_path(source)
        dst_path = _safe_path(destination)
    except ValueError as e:
        return str(e)
 
    if not src_path.exists():
        return f"Source file does not exist: {source}"
 
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src_path), str(dst_path))
    return f"Moved {source} -> {destination}"
 
 
# ---------------------------------------------------------------------------
# Tool 4: läs av vad som finns på sidan (för att hitta selektorer att klicka på)
# ---------------------------------------------------------------------------
def _get_clickable_elements_impl(page: Page, max_items: int) -> str:
    try:
        items = page.evaluate(
            """(max) => {
                const sels = 'a, button, input, textarea, select, [role="button"]';
                const els = Array.from(document.querySelectorAll(sels)).filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                return els.slice(0, max).map((e, i) => {
                    const text = (e.innerText || e.value || e.placeholder || e.getAttribute('aria-label') || '').trim().slice(0, 60);
                    let sel = e.tagName.toLowerCase();
                    if (e.id) sel += '#' + e.id;
                    else if (e.name) sel += `[name="${e.name}"]`;
                    else if (e.className && typeof e.className === 'string' && e.className.trim()) {
                        sel += '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.');
                    }
                    return `${i}: <${e.tagName.toLowerCase()}> "${text}"  selector: ${sel}`;
                });
            }""",
            max_items,
        )
    except Exception as e:
        return f"Could not read the page: {e}"
 
    if not items:
        return "No clickable elements found on the page."
    return "\n".join(items)
 
 
@tool
def get_clickable_elements(max_items: int = 40) -> str:
    """List clickable/editable elements on the current page (links, buttons, inputs).

    Returns index, visible text and a CSS selector that can be used with `click_on_page`/
    `type_into_page`. Call this BEFORE clicking/typing to locate the correct element.

    Args:
        max_items: Maximum number of elements to list (default 40)
    """
    return _BrowserWorker.instance().call(_get_clickable_elements_impl, max_items)
 
 
# ---------------------------------------------------------------------------
# Tool 5: klicka på ett element
# ---------------------------------------------------------------------------
def _click_on_page_impl(page: Page, selector: str, use_text: bool) -> str:
    try:
        if use_text:
            page.get_by_text(selector, exact=False).first.click(timeout=10000)
        else:
            page.click(selector, timeout=10000)
        page.wait_for_timeout(500)
        return f"Clicked: {selector}"
    except Exception as e:
        return f"Could not click '{selector}': {e}"
 
 
@tool
def click_on_page(selector: str, use_text: bool = False) -> str:
    """Click an element on the current page.

    Args:
        selector: CSS selector (e.g. "#submit-btn", "button.buy") OR visible
                  text if `use_text=True` (e.g. "Download")
        use_text: If True, `selector` is interpreted as visible text to click instead of CSS
    """
    return _BrowserWorker.instance().call(_click_on_page_impl, selector, use_text)
 
 
# ---------------------------------------------------------------------------
# Tool 6: skriv text i ett fält
# ---------------------------------------------------------------------------
def _type_into_page_impl(page: Page, selector: str, text: str, press_enter: bool, clear_first: bool) -> str:
    try:
        if clear_first:
            page.fill(selector, text, timeout=10000)
        else:
            page.type(selector, text, timeout=10000)
        if press_enter:
            page.press(selector, "Enter")
        page.wait_for_timeout(500)
        return f"Wrote '{text}' into {selector}" + (" and pressed Enter" if press_enter else "")
    except Exception as e:
        return f"Could not type into '{selector}': {e}"
 
 
@tool
def type_into_page(selector: str, text: str, press_enter: bool = False, clear_first: bool = True) -> str:
    """Type text into an input/textarea field on the page.

    Args:
        selector: CSS selector for the field (e.g. "input[name='q']")
        text: The text to type
        press_enter: If True, press Enter after typing (e.g. for search boxes)
        clear_first: If True, clear the field before typing
    """
    return _BrowserWorker.instance().call(_type_into_page_impl, selector, text, press_enter, clear_first)
 
 
# ---------------------------------------------------------------------------
# Tool 7: scrolla sidan
# ---------------------------------------------------------------------------
def _scroll_page_impl(page: Page, direction: str, amount_px: int) -> str:
    try:
        if direction == "down":
            page.mouse.wheel(0, amount_px)
        elif direction == "up":
            page.mouse.wheel(0, -amount_px)
        elif direction == "top":
            page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            return f"Unknown direction: {direction} (use down/up/top/bottom)"
        page.wait_for_timeout(300)
        return f"Scrolled {direction}"
    except Exception as e:
        return f"Could not scroll: {e}"
 
 
@tool
def scroll_page(direction: str = "down", amount_px: int = 800) -> str:
    """Scroll the page up or down, or jump to top/bottom.

    Args:
        direction: "down", "up", "top" or "bottom"
        amount_px: Number of pixels to scroll for "down"/"up" (default 800)
    """
    return _BrowserWorker.instance().call(_scroll_page_impl, direction, amount_px)
 
 
# ---------------------------------------------------------------------------
# Tool 8: klicka på nåt som triggar en nedladdning, spara i egen mapp
# ---------------------------------------------------------------------------
def _click_and_download_impl(page: Page, selector: str, use_text: bool, filename: Optional[str]) -> str:
    try:
        with page.expect_download(timeout=20000) as download_info:
            if use_text:
                page.get_by_text(selector, exact=False).first.click(timeout=10000)
            else:
                page.click(selector, timeout=10000)
        download = download_info.value
    except Exception as e:
        return f"No download started from '{selector}': {e}"
 
    save_name = os.path.basename(filename) if filename else download.suggested_filename
    dest = _safe_path(f"downloads/{save_name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(dest))
 
    size_kb = dest.stat().st_size / 1024
    return f"Downloaded via click to {dest.relative_to(AI_FOLDER)} ({size_kb:.1f} KB)"
 
 
@tool
def click_and_download(selector: str, use_text: bool = False, filename: Optional[str] = None) -> str:
    """Click an element (e.g. a "Download" link/button) that triggers a
    browser download and save the file to ai_workspace/downloads.

    Args:
        selector: CSS selector or visible text of the clickable element
        use_text: If True, `selector` is interpreted as visible text instead of CSS
        filename: Optional filename to save as. Otherwise the browser's suggested name is used.
    """
    return _BrowserWorker.instance().call(_click_and_download_impl, selector, use_text, filename)


#hämta all text från sidan

def _get_page_text_impl(page: Page) -> str:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(500)

        text = page.evaluate("""
            () => {
                return document.body?.innerText || "";
            }
        """)

        text = text.strip()

        if not text:
            return "No visible text found on the page."

        return text

    except Exception as e:
        return f"Could not read page text: {e}"

@tool
def get_page_text() -> str:
    """Retrieve all visible text from the current web page.

    Returns:
        All visible text (document.body.innerText).
    """
    return _BrowserWorker.instance().call(_get_page_text_impl)
 
 
# ---------------------------------------------------------------------------
# Exempel: binda till en agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Snabbtest utan agent
    print(search_visible_webpage.invoke({"url": "https://example.com", "query": "Example"}))
    print(download_file.invoke({"url": "https://example.com/index.html", "filename": "test.html"}))
    print(move_file.invoke({"source": "downloads/test.html", "destination": "klara/test.html"}))
 
    close_browser()
 
    # Exempel för att lägga in i en LangChain-agent:
    # tools = [
    #     search_visible_webpage, get_clickable_elements, click_on_page,
    #     type_into_page, scroll_page, download_file, click_and_download, move_file,
    # ]
    # agent = create_tool_calling_agent(llm, tools, prompt)