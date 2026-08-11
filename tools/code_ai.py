import asyncio
import os
import uuid
import threading
import contextvars
from pathlib import Path

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

from tools.shared_resources import GPU_LOCK

CODE_MODEL = os.environ.get("CODE_AI_MODEL", "qwen3:4b")
EXEC_TIMEOUT = 20          # sekunder per körning av run_python
RECURSION_LIMIT = 15       # max antal agent-steg innan vi ger upp

WORKSPACE_DIR = Path(__file__).resolve().parent.parent / "ai_workspace" / "code"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

_jobs: dict[str, dict] = {}
_notify_callback = None


def register_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn


# ---------------------------------------------------------------------------
# Håller koll på vilket job_id som hör till vilken körning.
# ContextVar (inte threading.local!) eftersom flera jobb kan köra samtidigt
# som asyncio-tasks på samma bakgrundstråd -- threading.local hade läckt
# job_id mellan parallella jobb.
# ---------------------------------------------------------------------------
_job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="adhoc")


# ---------------------------------------------------------------------------
# Verktyget agenten använder för att testa/köra sin egen kod
# ---------------------------------------------------------------------------
@tool
async def run_python(code: str) -> str:
    """Kör Python 3-kod i en isolerad subprocess och returnerar stdout/stderr.
    Använd det här verktyget för att testa och köra kod innan du svarar.
    Koden måste skriva ut resultatet med print(). Om körningen misslyckas,
    läs felmeddelandet, fixa koden och kör run_python igen."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    attempt = 0
    if job is not None:
        job["_attempt"] += 1
        attempt = job["_attempt"]

    filename = f"{job_id}_v{attempt}.py"
    path = WORKSPACE_DIR / filename
    path.write_text(code, encoding="utf-8")
    if job is not None:
        job["_files"].append(path)

    proc = await asyncio.create_subprocess_exec(
        "python3", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"FEL: Timeout efter {EXEC_TIMEOUT}s"

    if proc.returncode == 0:
        if job is not None:
            job["_last_success_path"] = path
        return f"OK\n{stdout.decode('utf-8', errors='replace').strip()}"

    return f"FEL (exit {proc.returncode})\n{stderr.decode('utf-8', errors='replace').strip()}"


CODE_TOOLS = [run_python]

_code_llm = ChatOllama(model=CODE_MODEL)

SYSTEM_PROMPT = (
    "Du är en kodassistent med tillgång till verktyget run_python för att "
    "skriva och köra Python 3-kod. Lös uppgiften genom att skriva kod, köra "
    "den med run_python, och om körningen misslyckas -- läs felet, fixa "
    "koden och kör run_python igen. Koden ska skriva ut resultatet med "
    "print(). Iterera tills koden fungerar eller du är säker på att "
    "uppgiften inte går att lösa. Avsluta alltid med ett kort svar i text "
    "som sammanfattar resultatet -- inga markdown-headers."
)

_code_agent = create_agent(
    model=_code_llm,
    system_prompt=SYSTEM_PROMPT,
    tools=CODE_TOOLS,
)

# ---------------------------------------------------------------------------
# Egen, ständigt levande event loop i bakgrundstråd
# ---------------------------------------------------------------------------
_bg_loop = asyncio.new_event_loop()


def _start_bg_loop():
    asyncio.set_event_loop(_bg_loop)
    _bg_loop.run_forever()


threading.Thread(target=_start_bg_loop, daemon=True).start()


def _cleanup_files(paths: list[Path], keep: Path | None) -> None:
    """Tar bort alla skrivna filer i `paths` utom `keep` (None = ta bort alla)."""
    for p in paths:
        if p == keep:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


async def _execute_job(job_id: str, task: str) -> None:
    _jobs[job_id]["status"] = "running"
    _jobs[job_id]["_attempt"] = 0
    _jobs[job_id]["_files"] = []
    _jobs[job_id]["_last_success_path"] = None

    token = _job_id_var.set(job_id)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, GPU_LOCK.acquire)
        try:
            result = await _code_agent.ainvoke(
                {"messages": [{"role": "user", "content": task}]},
                config={
                    "configurable": {"thread_id": f"code_ai_{job_id}"},
                    "recursion_limit": RECURSION_LIMIT,
                },
            )
        finally:
            GPU_LOCK.release()
    except Exception as exc:  # t.ex. recursion_limit nådd
        job = _jobs[job_id]
        job["status"] = "failed"
        job["result"] = f"### Fel\n```\n{exc}\n```"
        _cleanup_files(job["_files"], keep=None)
        if _notify_callback:
            _notify_callback(job_id, job["result"])
        return
    finally:
        _job_id_var.reset(token)

    job = _jobs[job_id]
    final_text = result["messages"][-1].content
    keep = job["_last_success_path"]

    job["status"] = "done"
    file_note = f"`{keep}`" if keep else "ingen fil sparades (inget lyckat körningssteg)"
    job["result"] = f"### Fil\n{file_note}\n\n### Svar\n{final_text}"

    _cleanup_files(job["_files"], keep=keep)
    if _notify_callback:
        _notify_callback(job_id, job["result"])


@tool
def code_ai(task: str) -> str:
    """Startar ett bakgrundsjobb där en Ollama-agent skriver och kör Python-kod
    (via verktyget run_python) för att lösa `task`, och itererar själv tills
    det funkar eller den ger upp. Returnerar direkt ett job_id.

    VIKTIGT: Jobbet kör i bakgrunden och tar tid. Svara användaren att
    jobbet har startats och gå vidare. Du meddelas automatiskt när
    resultatet är klart."""
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "queued", "result": None}
    asyncio.run_coroutine_threadsafe(_execute_job(job_id, task), _bg_loop)
    return f"Jobb startat: {job_id}. Kolla status med code_ai_status('{job_id}')."


@tool
def code_ai_status(job_id: str) -> str:
    """Kollar status/resultat för ett tidigare startat code_ai-jobb."""
    job = _jobs.get(job_id)
    if not job:
        return f"Okänt job_id: {job_id}"
    if job["status"] in ("queued", "running"):
        return f"Jobb {job_id}: {job['status']}..."
    return f"Jobb {job_id} ({job['status']}):\n\n{job['result']}"