import asyncio
import os
import shutil
import uuid
import threading
import contextvars
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EDIT_MODEL = os.environ.get("EDIT_AI_MODEL", "qwen3:4b")
RECURSION_LIMIT = int(os.environ.get("EDIT_AI_RECURSION_LIMIT", "40"))
MAX_READ_BYTES = 200_000  # skydd mot att en enda fil svämmar över kontexten

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_ROOT / "ai_workspace" / "edit_ai"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
print(f"[EDIT AI] PROJECT_ROOT = {PROJECT_ROOT}")
print(f"[EDIT AI] WORKSPACE_DIR = {WORKSPACE_DIR}")


# Kataloger/mönster som aldrig ska listas, läsas eller skrivas till
IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".idea", ".vscode", ".pytest_cache", "ai_workspace",
    "dist", "build", ".mypy_cache",
}
IGNORE_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".pdf",
}


# ---------------------------------------------------------------------------
# Job handling (samma mönster som code_ai / research_ai)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_notify_callback = None

_job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "edit_ai_job_id", default="adhoc"
)


def register_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn


def _job_workspace(job_id: str) -> Path:
    path = WORKSPACE_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Path-säkerhet: allt måste stanna innanför PROJECT_ROOT, inga ../-tricks
# ---------------------------------------------------------------------------

def _safe_relative(rel_path: str) -> Path:
    rel_path = rel_path.strip().lstrip("/\\")
    candidate = (PROJECT_ROOT / rel_path).resolve()
    root = PROJECT_ROOT.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path '{rel_path}' ligger utanför projektet - inte tillåtet")
    return candidate.relative_to(root)


def _is_ignored(rel_path: Path) -> bool:
    if any(part in IGNORE_DIRS for part in rel_path.parts):
        return True
    if rel_path.suffix.lower() in IGNORE_SUFFIXES:
        return True
    return False


def _iter_project_files():
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PROJECT_ROOT)
        if _is_ignored(rel):
            continue
        yield rel


# ---------------------------------------------------------------------------
# Verktyg agenten använder för att läsa hela codebasen och skriva ändringar
# ---------------------------------------------------------------------------

@tool
def list_codebase_files(pattern: str = "") -> str:
    """List every file in the project. Optionally filter with a substring
    `pattern` (e.g. 'tools/' or '.py'). Use this first to see what exists
    before reading or editing anything."""
    files = [str(rel) for rel in _iter_project_files()]
    if pattern:
        files = [f for f in files if pattern in f]
    files.sort()
    if not files:
        return "No matching files found."
    return "\n".join(files)


@tool
def read_codebase_file(path: str) -> str:
    """Read the current, unmodified content of a file at `path` (relative
    to the project root). Always read a file before editing it so your
    changes are based on the real content."""
    try:
        rel = _safe_relative(path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    full_path = PROJECT_ROOT / rel
    if not full_path.exists():
        return f"ERROR: file does not exist: {rel} (use write_edited_file to create a new file)"
    if full_path.stat().st_size > MAX_READ_BYTES:
        return f"ERROR: file too large to read ({full_path.stat().st_size} bytes): {rel}"

    try:
        return full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"ERROR: file is not text/UTF-8: {rel}"


@tool
def write_edited_file(path: str, content: str) -> str:
    """Save your edited version of a file at `path` (relative to the
    project root). This writes ONLY into a staging workspace, never into
    the real project - the real file is untouched until the user approves
    and calls apply_edit_files. Always write the COMPLETE new file content,
    not a diff or partial snippet. Calling this again for the same path
    overwrites your previous draft for that path."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    try:
        rel = _safe_relative(path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    stage_path = _job_workspace(job_id) / rel
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text(content, encoding="utf-8")

    if job is not None and str(rel) not in job["_files"]:
        job["_files"].append(str(rel))

    return f"Staged: {rel} ({len(content)} chars). Not yet applied to the real project."

@tool
def create_new_file(path: str, content: str) -> str:
    """Create a new file at `path` (relative to the project root) with the
    given `content`. This writes ONLY into a staging workspace, never into
    the real project - the real file is untouched until the user approves
    and calls apply_edit_files. Always write the COMPLETE new file content,
    not a diff or partial snippet. Calling this again for the same path
    overwrites your previous draft for that path."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    try:
        rel = _safe_relative(path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    stage_path = _job_workspace(job_id) / rel
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    stage_path.write_text(content, encoding="utf-8")

    if job is not None and str(rel) not in job["_files"]:
        job["_files"].append(str(rel))

    return f"Staged new file: {rel} ({len(content)} chars). Not yet applied to the real project."

@tool
def read_staged_file(path: str) -> str:
    """Read the content of a staged file at `path` (relative to the project
    root) that was previously written with write_edited_file or
    create_new_file. This reads from the staging workspace, not the real
    project files."""
    job_id = _job_id_var.get()
    stage_path = _job_workspace(job_id) / path
    if not stage_path.exists():
        return f"ERROR: staged file does not exist: {path}"
    try:
        return stage_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"ERROR: staged file is not text/UTF-8: {path}"

@tool
def test_files(path: str = "") -> str:
    """Test one or more staged files from the current edit_ai job.

    If `path` is provided, tests that specific staged Python file.
    If `path` is empty, tests all staged Python files.

    The tests currently use Python's compile() to check for syntax errors
    without executing the code.
    """
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)

    if job is None:
        return "ERROR: No active edit_ai job."

    staged_root = _job_workspace(job_id)

    if path:
        try:
            rel = _safe_relative(path)
        except ValueError as exc:
            return f"ERROR: {exc}"

        if str(rel) not in job["_files"]:
            return f"ERROR: File is not staged in this job: {rel}"

        files_to_test = [rel]
    else:
        files_to_test = [
            Path(f)
            for f in job["_files"]
            if Path(f).suffix.lower() == ".py"
        ]

        if not files_to_test:
            return "No staged Python files to test."

    results = []
    failed = False

    for rel in files_to_test:
        staged_file = staged_root / rel

        if not staged_file.exists():
            results.append(f"FAIL: {rel} - staged file does not exist")
            failed = True
            continue

        try:
            content = staged_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            results.append(f"FAIL: {rel} - not valid UTF-8")
            failed = True
            continue

        try:
            compile(
                content,
                str(rel),
                "exec",
            )
            results.append(f"PASS: {rel} - syntax OK")

        except SyntaxError as exc:
            failed = True

            results.append(
                f"FAIL: {rel} - SyntaxError at "
                f"line {exc.lineno}, column {exc.offset}: "
                f"{exc.msg}"
            )

        except Exception as exc:
            failed = True
            results.append(
                f"FAIL: {rel} - {type(exc).__name__}: {exc}"
            )

    status = "TESTS FAILED" if failed else "ALL TESTS PASSED"

    return status + "\n\n" + "\n".join(results)

EDIT_TOOLS = [list_codebase_files, read_codebase_file, write_edited_file, create_new_file, read_staged_file, test_files]


SYSTEM_PROMPT = """You are a coding assistant that edits an existing codebase.

WORKFLOW:
1. Use `list_codebase_files` to see what exists (optionally filtered).
2. Use `read_codebase_file` to read every file you need before changing it.
3. Make your changes and save each changed or new file with
   `write_edited_file`, always passing the FULL new file content.
4. You may call `write_edited_file` again on the same path to revise a
   draft. Nothing you write touches the real project - it only goes into a
   staging area the user reviews and applies later.
5. When you are done, finish with a short plain-text summary (no markdown
   headers) listing exactly which files you changed/created and why.

Only touch files that are actually relevant to the task. Do not rewrite
files you have not read first."""

_edit_llm = ChatOllama(model=EDIT_MODEL)

_edit_agent = create_agent(
    model=_edit_llm,
    system_prompt=SYSTEM_PROMPT,
    tools=EDIT_TOOLS,
)


# ---------------------------------------------------------------------------
# Egen, ständigt levande event loop i bakgrundstråd
# ---------------------------------------------------------------------------

_bg_loop = asyncio.new_event_loop()


def _start_bg_loop():
    asyncio.set_event_loop(_bg_loop)
    _bg_loop.run_forever()


threading.Thread(target=_start_bg_loop, daemon=True, name="edit-ai-loop").start()


async def _execute_job(job_id: str, task: str) -> None:
    job = _jobs[job_id]
    job["status"] = "running"

    token = _job_id_var.set(job_id)
    try:
        result = await _edit_agent.ainvoke(
            {"messages": [{"role": "user", "content": task}]},
            config={
                "configurable": {"thread_id": f"edit_ai_{job_id}"},
                "recursion_limit": RECURSION_LIMIT,
            },
        )
    except Exception as exc:  # t.ex. recursion_limit uppnådd
        job["status"] = "failed"
        job["result"] = f"Edit agent failed:\n{exc}"
        if _notify_callback:
            _notify_callback(job_id, job["result"])
        return
    finally:
        if _job_id_var.get() == job_id:
            _job_id_var.reset(token)

    final_text = result["messages"][-1].content
    changed = job["_files"]

    if changed:
        file_list = "\n".join(f"- {f}" for f in changed)
        files_note = f"Staged files ({len(changed)}):\n{file_list}"
    else:
        files_note = "No files were staged - nothing to apply."

    job["status"] = "done"
    job["result"] = (
        f"{files_note}\n\n"
        f"Summary:\n{final_text}\n\n"
        f"To apply these changes to the real project, call "
        f"apply_edit_files('{job_id}')."
    )

    if _notify_callback:
        _notify_callback(job_id, job["result"])


# ---------------------------------------------------------------------------
# Public tools
# ---------------------------------------------------------------------------

@tool
def edit_ai(task: str) -> str:
    """Start a background edit_ai job.

    The job runs asynchronously and stages changed files under:
    ai_workspace/edit_ai/<job_id>/

    The real project is never modified by this tool.
    """

    job_id = str(uuid.uuid4())[:8]

    # Skapa workspace DIREKT när jobbet startas
    job_workspace = _job_workspace(job_id)

    _jobs[job_id] = {
        "status": "queued",
        "result": None,
        "_files": [],
    }

    asyncio.run_coroutine_threadsafe(
        _execute_job(job_id, task),
        _bg_loop
    )

    return (
        f"Background edit job started successfully.\n"
        f"Job ID: {job_id}\n"
        f"Workspace: {job_workspace}\n"
        f"Do NOT call edit_ai_status now. "
        f"The job will notify the main agent automatically when finished."
    )


@tool
def edit_ai_status(job_id: str) -> str:
    """Check status/result for a previously started edit_ai job."""
    job = _jobs.get(job_id)
    if not job:
        return f"Unknown job_id: {job_id}"
    if job["status"] in ("queued", "running"):
        return f"Job {job_id}: {job['status']}..."
    return f"Job {job_id} ({job['status']}):\n\n{job['result']}"


@tool
def apply_edit_files(job_id: str, files: str = "all") -> str:
    """Replace real project files with the staged versions from a
    completed edit_ai job. `files` is either 'all' or a comma-separated
    list of relative paths (must match paths shown in the job result).
    Existing files are backed up to
    ai_workspace/edit_ai/<job_id>/_backup/<path> before being overwritten,
    so changes can be reverted manually if needed."""
    job = _jobs.get(job_id)
    if not job:
        return f"Unknown job_id: {job_id}"
    if job["status"] != "done":
        return f"Job {job_id} is not finished yet (status: {job['status']})."

    staged_root = _job_workspace(job_id)
    available = job["_files"]
    if not available:
        return f"Job {job_id} has no staged files to apply."

    if files.strip().lower() == "all":
        wanted = list(available)
    else:
        wanted = [f.strip() for f in files.split(",") if f.strip()]

    applied, skipped, errors = [], [], []
    backup_root = staged_root / "_backup" / datetime.now().strftime("%Y%m%d_%H%M%S")

    for rel_str in wanted:
        if rel_str not in available:
            skipped.append(f"{rel_str} (not staged in this job)")
            continue

        try:
            rel = _safe_relative(rel_str)
        except ValueError as exc:
            errors.append(f"{rel_str}: {exc}")
            continue

        staged_file = staged_root / rel
        if not staged_file.exists():
            errors.append(f"{rel_str}: staged file missing on disk")
            continue

        target = PROJECT_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            backup_path = backup_root / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)

        shutil.copy2(staged_file, target)
        applied.append(rel_str)

    parts = []
    if applied:
        parts.append("Applied:\n" + "\n".join(f"- {f}" for f in applied))
    if skipped:
        parts.append("Skipped:\n" + "\n".join(f"- {f}" for f in skipped))
    if errors:
        parts.append("Errors:\n" + "\n".join(f"- {f}" for f in errors))
    if applied:
        parts.append(f"Backups of overwritten files: {backup_root}")

    return "\n\n".join(parts) if parts else "Nothing was applied."


EDIT_AI_TOOLS = [edit_ai, edit_ai_status, apply_edit_files]
