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


class _GPULockedChatOllama(ChatOllama):
    async def ainvoke(self, *args, **kwargs):
        GPU_LOCK.acquire_background()
        try:
            return await super().ainvoke(*args, **kwargs)
        finally:
            GPU_LOCK.release()

_code_llm = _GPULockedChatOllama(model=CODE_MODEL)

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
    """Run Python 3 code in an isolated subprocess and return stdout/stderr.
    Use this tool to test and run code before replying. The code must print
    results using `print()`. If the run fails, read the error, fix the code,
    and call `run_python` again."""
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
        return f"ERROR: Timeout after {EXEC_TIMEOUT}s"

    if proc.returncode == 0:
        if job is not None:
            job["_last_success_path"] = path
        return f"OK\n{stdout.decode('utf-8', errors='replace').strip()}"

    return f"ERROR (exit {proc.returncode})\n{stderr.decode('utf-8', errors='replace').strip()}"


CODE_TOOLS = [run_python]

_code_llm = ChatOllama(model=CODE_MODEL)

SYSTEM_PROMPT = (
    "You are a coding assistant with access to the `run_python` tool to "
    "write and execute Python 3 code. Solve the task by writing code, "
    "running it with `run_python`, and if the run fails — read the error, "
    "fix the code and run `run_python` again. The code should print results "
    "using `print()`. Iterate until the code works or you determine the "
    "task cannot be solved. Always finish with a short textual summary of "
    "the result — no markdown headers."
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
    """Remove all written files in `paths` except `keep` (None = remove all)."""
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
        result = await _code_agent.ainvoke(
            {"messages": [{"role": "user", "content": task}]},
            config={
                "configurable": {"thread_id": f"code_ai_{job_id}"},
                "recursion_limit": RECURSION_LIMIT,
            },
        )
    except Exception as exc:  # e.g. recursion_limit reached
        job = _jobs[job_id]
        job["status"] = "failed"
        job["result"] = f"### Error\n```\n{exc}\n```"
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
    file_note = f"`{keep}`" if keep else "no file saved (no successful run step)"
    job["result"] = f"### File\n{file_note}\n\n### Response\n{final_text}"

    _cleanup_files(job["_files"], keep=keep)
    if _notify_callback:
        _notify_callback(job_id, job["result"])


@tool
def code_ai(task: str) -> str:
    """Start a background job where an Ollama agent writes and runs Python
    code (via the `run_python` tool) to solve `task`, iterating until it
    succeeds or gives up. Returns a job_id immediately.

    IMPORTANT: The job runs in the background and takes time. Inform the
    user that the job has started and continue. You will be notified when
    the result is ready."""
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {"status": "queued", "result": None}
    asyncio.run_coroutine_threadsafe(_execute_job(job_id, task), _bg_loop)
    return f"Job started: {job_id}. Check status with code_ai_status('{job_id}')."


@tool
def code_ai_status(job_id: str) -> str:
    """Check status/result for a previously started code_ai job."""
    job = _jobs.get(job_id)
    if not job:
        return f"Unknown job_id: {job_id}"
    if job["status"] in ("queued", "running"):
        return f"Job {job_id}: {job['status']}..."
    return f"Job {job_id} ({job['status']}):\n\n{job['result']}"