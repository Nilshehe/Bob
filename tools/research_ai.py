import asyncio
import html
import os
import re
import uuid
import threading
import contextvars
from pathlib import Path

import requests
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

from tools.ddgs_tool import web_search as _web_search_impl
from tools.code_ai import code_ai, code_ai_status
import gui.backend.gui_server as gui_server

FETCH_TIMEOUT = 15          # sekunder per web_fetch-anrop
FETCH_MAX_CHARS = 15_000    # skydd mot att en enda sida svämmar över kontexten
FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BobResearchAI/1.0)"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESEARCH_MODEL = os.environ.get("RESEARCH_AI_MODEL", "qwen3:4b")
RECURSION_LIMIT = int(os.environ.get("RESEARCH_RECURSION_LIMIT", "60"))

WORKSPACE_DIR = Path(__file__).resolve().parent.parent / "ai_workspace" / "research"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Job handling
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_notify_callback = None

_job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "research_job_id",
    default="adhoc",
)


def register_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Make a filesystem-friendly name from the task text."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40]


def _current_research_path() -> Path:
    job_id = _job_id_var.get()
    if job_id == "adhoc":
        return WORKSPACE_DIR / "adhoc_research.md"
    return WORKSPACE_DIR / f"{job_id}_research.md"


def _monitor_update(
    job_id,
    status=None,
    activity=None,
    progress=None,
    tool=None,
    step=None,
):
    try:
        gui_server.broadcast_agent_monitor(
            "research_ai",
            job_id,
            status=status,
            activity=activity,
            progress=progress,
            tool=tool,
            step=step,
        )
    except Exception:
        pass


def _incr_step(job_id):
    """Bump and return this job's tool-call step counter, for a more
    granular activity trail in the GUI monitor. Returns None if there is
    no active job (e.g. an ad-hoc/manual call)."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    job["_step"] = job.get("_step", 0) + 1
    return job["_step"]


# ---------------------------------------------------------------------------
# Research workspace tools
# ---------------------------------------------------------------------------

@tool
def save_research(content: str, section: str = "Research") -> str:
    """Save research to the current file."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Saving research notes ({section})...",
        tool="save_research",
        step=_incr_step(job_id),
    )
    path = _current_research_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {section}\n\n")
        f.write(content.strip())
        f.write("\n")
    return f"Research sparad i {path}"


@tool
def read_research() -> str:
    """Read research from the current file."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity="Reading saved research...",
        tool="read_research",
        step=_incr_step(job_id),
    )
    path = _current_research_path()
    if not path.exists():
        return "No research has been saved yet."
    return path.read_text(encoding="utf-8")


def _sources_path() -> Path:
    job_id = _job_id_var.get()
    if job_id == "adhoc":
        return WORKSPACE_DIR / "adhoc_sources.md"
    return WORKSPACE_DIR / f"{job_id}_sources.md"


@tool
def save_source(url: str, note: str = "") -> str:
    """Record a source URL (with an optional short note on what it says or
    why it matters) in this job's running bibliography, separate from the
    main research notes. Use this whenever `web_search` or `fetch_page`
    turns up something you rely on, so the final report can cite real,
    tracked sources instead of ones recalled from memory."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Recording source: {url}...",
        tool="save_source",
        step=_incr_step(job_id),
    )
    path = _sources_path()
    with path.open("a", encoding="utf-8") as f:
        line = f"- {url}"
        if note.strip():
            line += f" — {note.strip()}"
        f.write(line + "\n")
    return f"Source saved to {path}"


@tool
def list_sources() -> str:
    """List every source saved so far in the current job with
    `save_source`."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity="Reviewing saved sources...",
        tool="list_sources",
        step=_incr_step(job_id),
    )
    path = _sources_path()
    if not path.exists():
        return "No sources saved yet."
    return path.read_text(encoding="utf-8")


@tool
def list_research_files() -> str:
    """List all research files in the workspace."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity="Listing research files...",
        tool="list_research_files",
        step=_incr_step(job_id),
    )
    files = sorted(WORKSPACE_DIR.glob("*.md"))
    if not files:
        return "No research files exist yet."
    return "\n".join(str(path) for path in files)


def _html_to_text(raw_html: str) -> str:
    """Very small, dependency-free HTML-to-text extractor: strips scripts,
    styles, and tags, and collapses whitespace. Good enough to read the
    actual content of a page rather than raw markup."""
    text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


@tool
def fetch_page(url: str) -> str:
    """Fetch a web page by URL and return its readable text content
    (HTML tags stripped). Use this after `web_search` when a result looks
    important and you need the full page, not just the short snippet
    `web_search` returns. Long pages are truncated."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Fetching {url}...",
        tool="fetch_page",
        step=_incr_step(job_id),
    )
    try:
        resp = requests.get(url, headers=FETCH_HEADERS, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"ERROR: could not fetch {url}: {exc}"

    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type or "<html" in resp.text[:500].lower():
        text = _html_to_text(resp.text)
    else:
        text = resp.text

    if len(text) > FETCH_MAX_CHARS:
        text = text[:FETCH_MAX_CHARS] + f"\n\n[...truncated, {len(text)} chars total]"

    if not text.strip():
        return f"Fetched {url} but found no readable text content."
    return text


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for `query` and return up to `max_results` short
    result snippets. Use `fetch_page` afterwards to read a promising
    result's full page content."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Searching web: '{query}'...",
        tool="web_search",
        step=_incr_step(job_id),
    )
    return _web_search_impl.invoke({"query": query, "max_results": max_results})


# ---------------------------------------------------------------------------
# Tools available to the research agent
# ---------------------------------------------------------------------------

RESEARCH_TOOLS = [
    web_search,
    fetch_page,
    save_research,
    read_research,
    save_source,
    list_sources,
    list_research_files,
    code_ai,
    code_ai_status,
]


# ---------------------------------------------------------------------------
# Research LLM + agent
# ---------------------------------------------------------------------------

_research_llm = ChatOllama(model=RESEARCH_MODEL)

SYSTEM_PROMPT = """
You are an autonomous research agent.

Your job is to perform thorough, source-based research for the user's task.
You may use `web_search` as many times as necessary. Do not stop at a few
queries if the question requires more investigation.

WORKFLOW:

1. Understand exactly what the user wants to know.
2. Break the question into relevant subtopics.
3. Search the web using `web_search`.
4. Follow up important results with new, more specific searches. When a
   result's snippet isn't enough — you need the full article, docs page,
   or data — use `fetch_page` on its URL to read the actual content.
5. Compare multiple sources when relevant.
6. Check for contradictions and aim to find the most reliable information.
7. Prioritize primary sources, official documentation, academic research,
   and other credible sources when available.
8. Note dates and whether information may have changed.
9. Save research incrementally with `save_research`, and record every URL
   you actually rely on with `save_source` (with a short note on what it
   says). Use `list_sources` to review the running bibliography.
10. Read previously saved research with `read_research` before duplicating work.
11. If the task requires programming, technical analysis, or testing you may
    use `code_ai`. `code_ai` runs asynchronously. Use `code_ai_status` to
    check a job when needed.
12. Continue researching until you have sufficiently strong evidence to answer
    the user's question. Do not stop after finding the first usable result.
13. Save a clear final report to the research file including:
    - the question
    - an executive summary
    - key facts
    - detailed findings
    - sources
    - uncertainties/contradictions
    - conclusions
    - any recommendations

SOURCE GUIDELINES:
- Do not invent sources.
- Distinguish between what sources actually state and your own conclusions.
- If two sources contradict each other, note it and investigate further.
- Use as many searches as necessary to verify important claims.
- Save every URL/source identifier you rely on with `save_source` when
  `web_search` or `fetch_page` returns them.

ABOUT `code_ai`:
- `code_ai` is asynchronous and returns a job_id.
- When you start `code_ai`, do not assume results immediately.
- Use `code_ai_status(job_id)` to retrieve results when ready.
- Do not use `code_ai` if ordinary research suffices.

ANSWER FORMAT:
When the research is complete, provide the user with a brief but informative
final answer. The full research should be saved in `ai_workspace/research`.
"""

_research_agent = create_agent(
    model=_research_llm,
    system_prompt=SYSTEM_PROMPT,
    tools=RESEARCH_TOOLS,
)


# ---------------------------------------------------------------------------
# Background event loop
# ---------------------------------------------------------------------------

_bg_loop = asyncio.new_event_loop()


def _start_bg_loop():
    asyncio.set_event_loop(_bg_loop)
    _bg_loop.run_forever()


threading.Thread(
    target=_start_bg_loop,
    daemon=True,
    name="research-ai-loop",
).start()


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------

async def _execute_job(job_id: str, task: str) -> None:
    job = _jobs[job_id]
    job["status"] = "running"
    job["_step"] = 0

    _monitor_update(
        job_id,
        status="RUNNING",
        activity="Starting research...",
        progress=0,
        step=0,
    )

    token = _job_id_var.set(job_id)

    try:
        _monitor_update(
            job_id,
            status="RUNNING",
            activity="Researching...",
            progress=10,
            step=0,
        )

        result = await _research_agent.ainvoke(
            {
                "messages": [
                    {"role": "user", "content": task}
                ]
            },
            config={
                "configurable": {"thread_id": f"research_ai_{job_id}"},
                "recursion_limit": RECURSION_LIMIT,
            },
        )

    except Exception as exc:
        job["status"] = "failed"
        _monitor_update(
            job_id,
            status="FAILED",
            activity="Research failed",
            step=job.get("_step"),
        )
        job["result"] = f"Research agent failed:\n{exc}"

        if _notify_callback:
            _notify_callback(job_id, job["result"])

        _job_id_var.reset(token)
        return

    finally:
        if _job_id_var.get() == job_id:
            _job_id_var.reset(token)

    # ---------------------------------------------------------
    # Slutrapport
    # ---------------------------------------------------------

    final_message = result["messages"][-1].content

    # Skapa nytt filnamn baserat på uppgiften
    slug = _slugify(task)
    new_path = WORKSPACE_DIR / f"{job_id}_{slug}.md"

    # Hämta nuvarande fil
    old_path = _current_research_path()

    # Döp om filen
    try:
        old_path.rename(new_path)
    except Exception:
        new_path = old_path

    # Skriv slutrapporten i filen
    with new_path.open("a", encoding="utf-8") as f:
        f.write("\n\n## Final report\n\n")
        f.write(final_message)
        f.write("\n")

    job["status"] = "done"
    _monitor_update(
        job_id,
        status="DONE",
        activity="Research complete",
        progress=100,
        step=job.get("_step"),
    )
    job["research_file"] = str(new_path)
    job["result"] = (
        f"Research complete.\n\n"
        f"Research file: {new_path}\n\n"
        f"Summary:\n{final_message}"
    )
    if _notify_callback:
        _notify_callback(job_id, job["result"])


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool
def research_ai(task: str) -> str:
    """Start a research job in the background. Returns job_id."""
    job_id = str(uuid.uuid4())[:8]

    research_path = WORKSPACE_DIR / f"{job_id}_research.md"

    research_path.write_text(
        f"# Research job {job_id}\n\n"
        f"## Uppgift\n\n{task}\n",
        encoding="utf-8",
    )

    _jobs[job_id] = {
        "status": "queued",
        "result": None,
        "research_file": str(research_path),
        "_step": 0,
    }

    asyncio.run_coroutine_threadsafe(
        _execute_job(job_id, task),
        _bg_loop,
    )

    return (
        f"Research job started: {job_id}. "
        f"Research is saved in {research_path}. "
        f"Check status with research_ai_status('{job_id}')."
    )


@tool
def research_ai_status(job_id: str) -> str:
    """Check status for a research job."""
    job = _jobs.get(job_id)
    if not job:
        return f"Unknown job_id: {job_id}"

    if job["status"] in ("queued", "running"):
        return (
            f"Research job {job_id}: {job['status']}...\n"
            f"File: {job['research_file']}"
        )

    return (
        f"Research job {job_id} ({job['status']}):\n\n"
        f"{job['result']}"
    )


# ---------------------------------------------------------------------------
# Optional convenience export
# ---------------------------------------------------------------------------

RESEARCH_AI_TOOLS = [
    research_ai,
    research_ai_status,
]
