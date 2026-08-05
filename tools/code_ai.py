import asyncio
import re
import os
import uuid
import threading
from pathlib import Path
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

from tools.shared_resources import GPU_LOCK

CODE_MODEL = os.environ.get("CODE_AI_MODEL", "qwen3:4b")
MAX_RETRIES = 2
EXEC_TIMEOUT = 20  # sekunder

WORKSPACE_DIR = Path(__file__).resolve().parent.parent / "ai_workspace" / "downloads"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

_code_llm = ChatOllama(model=CODE_MODEL)

SYSTEM_PROMPT = (
    "Du är en kodassistent. Skriv fristående, körbar Python 3-kod som löser "
    "uppgiften. Koden ska skriva ut resultatet med print(). Inga förklaringar, "
    "inga markdown-headers — enbart ett enda ```python kodblock."
)

# ---------------------------------------------------------------------------
# Egen, ständigt levande event loop i bakgrundstråd
# ---------------------------------------------------------------------------
_bg_loop = asyncio.new_event_loop()


def _start_bg_loop():
    asyncio.set_event_loop(_bg_loop)
    _bg_loop.run_forever()


threading.Thread(target=_start_bg_loop, daemon=True).start()

_jobs: dict[str, dict] = {}
_notify_callback = None


def register_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn


def _extract_code(text: str) -> str:
    match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


async def _generate_code(task: str, error_context: str | None = None) -> str:
    """Genererar kod via Ollama. Väntar på GPU_LOCK innan själva
    modellanropet -- delar resursen med huvud-AI:n istället för att
    krocka med den. Låset tas i en executor-tråd så bakgrunds-loopen
    inte blockeras medan den väntar."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if error_context:
        messages.append({
            "role": "user",
            "content": f"Uppgift: {task}\n\nFörra försöket gav detta fel:\n{error_context}\nFixa koden.",
        })
    else:
        messages.append({"role": "user", "content": task})

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, GPU_LOCK.acquire)
    try:
        resp = await _code_llm.ainvoke(messages)
    finally:
        GPU_LOCK.release()

    return _extract_code(resp.content)


async def _run_code(code: str, job_id: str, attempt: int) -> tuple[bool, str, Path]:
    filename = f"{job_id}_v{attempt}.py"
    path = WORKSPACE_DIR / filename
    path.write_text(code, encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        "python3", str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"Timeout efter {EXEC_TIMEOUT}s", path

    if proc.returncode == 0:
        return True, stdout.decode("utf-8", errors="replace"), path
    return False, stderr.decode("utf-8", errors="replace"), path


def _cleanup_files(paths: list[Path], keep: Path | None) -> None:
    """Tar bort alla skrivna filer i `paths` utom `keep` (None = ta bort alla).
    Städar upp misslyckade försök/hela jobbet från ai_workspace/downloads."""
    for p in paths:
        if p == keep:
            continue
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


async def _execute_job(job_id: str, task: str) -> None:
    _jobs[job_id]["status"] = "running"
    code = await _generate_code(task)
    last_error = None
    written_paths: list[Path] = []
    final_path: Path | None = None

    for attempt in range(MAX_RETRIES + 1):
        ok, output, path = await _run_code(code, job_id, attempt)
        written_paths.append(path)

        if ok:
            final_path = path
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = (
                f"### Fil\n`{path}`\n\n"
                f"### Kod\n```python\n{code}\n```\n\n### Resultat\n```\n{output.strip()}\n```"
            )
            _cleanup_files(written_paths, keep=final_path)
            if _notify_callback:
                _notify_callback(job_id, _jobs[job_id]["result"])
            return

        last_error = output
        if attempt < MAX_RETRIES:
            code = await _generate_code(task, error_context=last_error)

    _jobs[job_id]["status"] = "failed"
    _jobs[job_id]["result"] = (
        f"### Kod (misslyckades)\n```python\n{code}\n```\n\n### Sista felet\n```\n{last_error}\n```"
    )
    # Jobbet gick aldrig igenom -> ingen fil är värd att spara, städa bort allt.
    _cleanup_files(written_paths, keep=None)
    if _notify_callback:
        _notify_callback(job_id, _jobs[job_id]["result"])


@tool
def code_ai(task: str) -> str:
    """Startar ett bakgrundsjobb som skriver och kör Python-kod lokalt (Ollama)
    för att lösa `task`. Returnerar direkt ett job_id.

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