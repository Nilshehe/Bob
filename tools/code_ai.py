import asyncio
import os
import uuid
import threading
import contextvars
from pathlib import Path
from ddgs_tool import web_search

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

CODE_MODEL = os.environ.get("CODE_AI_MODEL", "qwen3:4b")
import gui.backend.gui_server as gui_server
EXEC_TIMEOUT = 20          # sekunder per körning av run_python
SHELL_EXEC_TIMEOUT = int(os.environ.get("CODE_AI_SHELL_TIMEOUT", "30"))  # sekunder per run_shell
RECURSION_LIMIT = 15       # max antal agent-steg innan vi ger upp

PROJECT_ROOT = Path(__file__).resolve().parent.parent



_code_llm = ChatOllama(model=CODE_MODEL)

WORKSPACE_DIR = Path(__file__).resolve().parent.parent / "ai_workspace" / "code"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

_jobs: dict[str, dict] = {}
_notify_callback = None


def register_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn


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
            "code_ai",
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

    _monitor_update(
        job_id,
        activity=f"Testing code (attempt {attempt})...",
        progress=min(90, 20 + attempt * 20),
        tool="run_python",
        step=_incr_step(job_id),
    )

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


@tool
def list_attempts() -> str:
    """List every code attempt (version) tried so far in the current
    code_ai job, in order. Use this to see the history of what you've
    already tried before writing another attempt."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    _monitor_update(
        job_id,
        activity="Reviewing previous attempts...",
        tool="list_attempts",
        step=_incr_step(job_id),
    )

    if not job or not job.get("_files"):
        return "No attempts yet in this job."

    lines = []
    for p in job["_files"]:
        marker = " (last successful run)" if p == job.get("_last_success_path") else ""
        lines.append(f"- {p.name}{marker}")
    return "\n".join(lines)


@tool
def read_attempt(version: int) -> str:
    """Read the code from a previous attempt in the current job, by its
    attempt number (the `attempt N` shown in run_python's progress
    updates, starting at 1). Use this to see exactly what you tried before
    when fixing an error, instead of rewriting from scratch."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    _monitor_update(
        job_id,
        activity=f"Reading attempt #{version}...",
        tool="read_attempt",
        step=_incr_step(job_id),
    )

    if not job:
        return "ERROR: No active code_ai job."

    filename = f"{job_id}_v{version}.py"
    path = WORKSPACE_DIR / filename
    if not path.exists():
        return f"ERROR: no attempt #{version} found in this job."
    return path.read_text(encoding="utf-8")


@tool
async def run_shell(command: str, cwd: str = "") -> str:
    """Run a shell command in a real terminal (bash) and return its exit
    code, stdout, and stderr. Use this for anything `run_python` can't do
    directly: installing packages (e.g. `pip install X --break-system-packages`),
    git commands, checking what tools/versions are installed, running
    scripts or compiled binaries, listing/inspecting files, etc.

    Runs from the project root by default. Pass `cwd` (a path relative to
    the project root) to run somewhere else instead - it must stay inside
    the project directory. Commands time out after a set number of seconds
    (kills the process and returns an error) so a hung command can't block
    the job forever."""
    job_id = _job_id_var.get()

    _monitor_update(
        job_id,
        activity=f"Running: {command[:60]}",
        tool="run_shell",
        step=_incr_step(job_id),
    )

    workdir = PROJECT_ROOT
    if cwd:
        candidate = (PROJECT_ROOT / cwd).resolve()
        root = PROJECT_ROOT.resolve()
        if candidate != root and root not in candidate.parents:
            return f"ERROR: cwd '{cwd}' is outside the project directory - not allowed"
        workdir = candidate

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return f"ERROR: could not start command: {exc}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_EXEC_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return f"ERROR: Timeout after {SHELL_EXEC_TIMEOUT}s (command killed): {command}"

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()

    parts = [f"exit code: {proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n\n".join(parts)


CODE_TOOLS = [run_python, run_shell, list_attempts, read_attempt, web_search]


SYSTEM_PROMPT = (
    "You are a coding assistant with access to `run_python` (write and "
    "execute Python 3 code) and `run_shell` (run real shell/terminal "
    "commands - installing packages, git, checking tool versions, running "
    "scripts or binaries, etc.). Solve the task by writing code and/or "
    "running commands, and if something fails — read the error, fix it, "
    "and try again. Prefer `run_python` for computation and logic; use "
    "`run_shell` when you need the actual system/terminal (installing a "
    "dependency before importing it, running a non-Python tool, checking "
    "what's installed, and so on). The Python code should print results "
    "using `print()`. Use `list_attempts` and `read_attempt` if you need to "
    "recall what you already tried in this job instead of guessing. Iterate "
    "until the task works or you determine it cannot be solved. Always "
    "finish with a short textual summary of the result — no markdown "
    "headers."
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
    _jobs[job_id]["_step"] = 0

    _monitor_update(
        job_id,
        status="RUNNING",
        activity="Starting Code AI...",
        progress=0,
    )

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
        _monitor_update(
            job_id,
            status="FAILED",
            activity="Code AI failed",
            step=job.get("_step"),
        )
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
    _monitor_update(
        job_id,
        status="DONE",
        activity="Code AI complete",
        progress=100,
        step=job.get("_step"),
    )
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