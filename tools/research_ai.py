import asyncio
import os
import uuid
import threading
import contextvars
from pathlib import Path

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

from tools.ddgs_tool import web_search
from tools.code_ai import code_ai, code_ai_status


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
    """Gör ett filvänligt namn av uppgiften."""
    import re
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:40]


def _current_research_path() -> Path:
    job_id = _job_id_var.get()
    if job_id == "adhoc":
        return WORKSPACE_DIR / "adhoc_research.md"
    return WORKSPACE_DIR / f"{job_id}_research.md"


# ---------------------------------------------------------------------------
# Research workspace tools
# ---------------------------------------------------------------------------

@tool
def save_research(content: str, section: str = "Research") -> str:
    """Spara research i aktuell fil."""
    path = _current_research_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {section}\n\n")
        f.write(content.strip())
        f.write("\n")
    return f"Research sparad i {path}"


@tool
def read_research() -> str:
    """Läs research från aktuell fil."""
    path = _current_research_path()
    if not path.exists():
        return "Ingen research har sparats ännu."
    return path.read_text(encoding="utf-8")


@tool
def list_research_files() -> str:
    """Lista alla research-filer i workspace."""
    files = sorted(WORKSPACE_DIR.glob("*.md"))
    if not files:
        return "Inga research-filer finns ännu."
    return "\n".join(str(path) for path in files)


# ---------------------------------------------------------------------------
# Tools available to the research agent
# ---------------------------------------------------------------------------

RESEARCH_TOOLS = [
    web_search,
    save_research,
    read_research,
    list_research_files,
    code_ai,
    code_ai_status,
]


# ---------------------------------------------------------------------------
# Research LLM + agent
# ---------------------------------------------------------------------------

_research_llm = ChatOllama(model=RESEARCH_MODEL)

SYSTEM_PROMPT = """
Du är en autonom research-agent.

Ditt jobb är att göra grundlig, källbaserad research för användarens uppgift.
...
(du har kvar hela din system prompt här)
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

    token = _job_id_var.set(job_id)

    try:
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
        job["result"] = f"Research-agenten misslyckades:\n{exc}"

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
        f.write("\n\n## Slutrapport\n\n")
        f.write(final_message)
        f.write("\n")

    job["status"] = "done"
    job["research_file"] = str(new_path)
    job["result"] = (
        f"Research klar.\n\n"
        f"Research-fil: {new_path}\n\n"
        f"Sammanfattning:\n{final_message}"
    )

    if _notify_callback:
        _notify_callback(job_id, job["result"])


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool
def research_ai(task: str) -> str:
    """Starta ett research-jobb i bakgrunden. Returnerar job_id."""
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
    }

    asyncio.run_coroutine_threadsafe(
        _execute_job(job_id, task),
        _bg_loop,
    )

    return (
        f"Research-jobb startat: {job_id}. "
        f"Research sparas i {research_path}. "
        f"Kolla status med research_ai_status('{job_id}')."
    )


@tool
def research_ai_status(job_id: str) -> str:
    """Kolla status för ett research-jobb."""
    job = _jobs.get(job_id)
    if not job:
        return f"Okänt job_id: {job_id}"

    if job["status"] in ("queued", "running"):
        return (
            f"Research-jobb {job_id}: {job['status']}...\n"
            f"Fil: {job['research_file']}"
        )

    return (
        f"Research-jobb {job_id} ({job['status']}):\n\n"
        f"{job['result']}"
    )


# ---------------------------------------------------------------------------
# Optional convenience export
# ---------------------------------------------------------------------------

RESEARCH_AI_TOOLS = [
    research_ai,
    research_ai_status,
]
