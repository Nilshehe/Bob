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

# ai_workspace/research
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
    """
    Registrera en callback som anropas när ett research-jobb är klart.

    Callback:
        fn(job_id: str, result: str) -> None
    """
    global _notify_callback
    _notify_callback = fn


# ---------------------------------------------------------------------------
# Research workspace tools
# ---------------------------------------------------------------------------

def _current_research_path() -> Path:
    job_id = _job_id_var.get()

    if job_id == "adhoc":
        return WORKSPACE_DIR / "adhoc_research.md"

    return WORKSPACE_DIR / f"{job_id}_research.md"


@tool
def save_research(content: str, section: str = "Research") -> str:
    """
    Sparar research i jobbets research-fil.

    Använd detta verktyg löpande under researchen, inte bara i slutet.
    Lägg in källor, fakta, observationer, osäkerheter och slutsatser.
    """
    path = _current_research_path()

    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {section}\n\n")
        f.write(content.strip())
        f.write("\n")

    return f"Research sparad i {path}"


@tool
def read_research() -> str:
    """
    Läser all research som redan sparats för det aktuella jobbet.
    Använd detta för att undvika att göra samma research flera gånger.
    """
    path = _current_research_path()

    if not path.exists():
        return "Ingen research har sparats ännu."

    return path.read_text(encoding="utf-8")


@tool
def list_research_files() -> str:
    """Visar tidigare sparade research-filer i ai_workspace/research."""
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
Du får använda web_search hur många gånger som behövs. Gör inte bara en eller
två sökningar om frågan kräver mer research.

ARBETSSÄTT:

1. Förstå exakt vad användaren vill veta.
2. Dela upp frågan i relevanta delområden.
3. Sök på webben med web_search.
4. Följ upp viktiga resultat med nya, mer specifika sökningar.
5. Jämför flera källor när det är relevant.
6. Kontrollera motsägelser och försök hitta den mest tillförlitliga
   informationen.
7. Prioritera primärkällor, officiell dokumentation, forskning och andra
   trovärdiga källor när sådana finns.
8. Notera datum och om information kan ha ändrats.
9. Spara research löpande med save_research.
10. Läs tidigare sparad research med read_research innan du gör om arbete.
11. Om uppgiften kräver programmering, teknisk analys eller testning kan du
    använda code_ai. code_ai kör i bakgrunden. Använd code_ai_status för att
    kontrollera ett jobb när det behövs.
12. Fortsätt forska tills du har tillräckligt starkt underlag för att besvara
    användarens fråga. Sluta inte bara för att du har hittat ett första
    användbart svar.
13. Spara en tydlig slutrapport i research-filen med:
    - frågeställning
    - sammanfattning
    - viktiga fakta
    - detaljerade fynd
    - källor
    - osäkerheter/motsägelser
    - slutsats
    - eventuella rekommendationer

VIKTIGT OM KÄLLOR:
- Hitta inte på källor.
- Skilj mellan vad källorna faktiskt säger och egna slutsatser.
- Om två källor motsäger varandra, skriv det och undersök vidare.
- Använd så många sökningar som behövs för att verifiera viktiga påståenden.
- Spara relevanta URL:er/källidentifierare i research-filen om web_search
  returnerar dem.

VIKTIGT OM CODE_AI:
- code_ai är asynkront och returnerar ett job_id.
- När du startar code_ai ska du inte anta resultatet.
- Använd code_ai_status(job_id) när du behöver resultatet.
- Använd inte code_ai om vanlig research räcker.

SVAR:
När researchen är klar, ge användaren ett kort men informativt slutresultat.
Den fullständiga researchen ska finnas sparad i ai_workspace/research.
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
                    {
                        "role": "user",
                        "content": task,
                    }
                ]
            },
            config={
                "configurable": {
                    "thread_id": f"research_ai_{job_id}",
                },
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

    final_message = result["messages"][-1].content

    research_path = _current_research_path()

    job["status"] = "done"
    job["research_file"] = str(research_path)
    job["result"] = (
        f"Research klar.\n\n"
        f"Research-fil: {research_path}\n\n"
        f"Sammanfattning:\n{final_message}"
    )

    if _notify_callback:
        _notify_callback(job_id, job["result"])


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool
def research_ai(task: str) -> str:
    """
    Startar ett autonomt research-jobb i bakgrunden.

    Agenten kan söka på webben, spara research, läsa tidigare research och
    starta code_ai när teknisk kodanalys behövs.

    Returnerar direkt ett job_id. Använd research_ai_status(job_id) för att
    kontrollera status och hämta resultatet.

    Du komer att automatiskt starta när uppdraget är klart.
    """
    job_id = str(uuid.uuid4())[:8]

    research_path = WORKSPACE_DIR / f"{job_id}_research.md"

    # Skapa filen direkt så att den finns även medan jobbet kör.
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
    """Kollar status/resultat för ett tidigare research_ai-jobb."""
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
