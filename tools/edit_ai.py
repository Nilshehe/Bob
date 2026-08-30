import asyncio
import difflib
import os
import re
import shutil
import uuid
import threading
import contextvars
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain.agents import create_agent

import gui.backend.gui_server as gui_server
from funktioner import metrics

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EDIT_MODEL = os.environ.get("EDIT_AI_MODEL", "qwen3:4b")
RECURSION_LIMIT = int(os.environ.get("EDIT_AI_RECURSION_LIMIT", "40"))
MAX_READ_BYTES = 200_000  # skydd mot att en enda fil svämmar över kontexten

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_ROOT / "ai_workspace" / "edit_ai"
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

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
            "edit_ai",
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
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity="Listing codebase files...",
        tool="list_codebase_files",
        step=_incr_step(job_id),
    )
    files = [str(rel) for rel in _iter_project_files()]
    if pattern:
        files = [f for f in files if pattern in f]
    files.sort()
    if not files:
        return "No matching files found."
    return "\n".join(files)


@tool
def search_codebase(query: str, path_pattern: str = "", regex: bool = False) -> str:
    """Search the project for a string (or, if `regex` is True, a regular
    expression) across every non-ignored file. Optionally narrow the search
    to files whose path contains `path_pattern` (e.g. 'tools/'). Returns
    matching lines as 'path:line: text'. Use this to find where something
    is defined/used before reading whole files blind."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Searching codebase for '{query}'...",
        tool="search_codebase",
        step=_incr_step(job_id),
    )
    try:
        pattern = re.compile(query) if regex else None
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"

    matches = []
    for rel in _iter_project_files():
        if path_pattern and path_pattern not in str(rel):
            continue
        full_path = PROJECT_ROOT / rel
        if full_path.stat().st_size > MAX_READ_BYTES:
            continue
        try:
            text = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            hit = pattern.search(line) if regex else (query in line)
            if hit:
                matches.append(f"{rel}:{lineno}: {line.strip()}")
                if len(matches) >= 200:
                    break
        if len(matches) >= 200:
            break

    if not matches:
        return f"No matches for '{query}'."
    return "\n".join(matches)


@tool
def read_codebase_file(path: str) -> str:
    """Read the current, unmodified content of a file at `path` (relative
    to the project root). Always read a file before editing it so your
    changes are based on the real content."""
    job_id = _job_id_var.get()
    _monitor_update(
        job_id,
        activity=f"Reading {path}...",
        tool="read_codebase_file",
        step=_incr_step(job_id),
    )
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

    _monitor_update(job_id, activity=f"Staging edit: {rel}...", tool="write_edited_file", step=_incr_step(job_id))

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

    _monitor_update(job_id, activity=f"Staging new file: {rel}...", tool="create_new_file", step=_incr_step(job_id))

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
    _monitor_update(
        job_id,
        activity=f"Reading staged draft: {path}...",
        tool="read_staged_file",
        step=_incr_step(job_id),
    )
    stage_path = _job_workspace(job_id) / path
    if not stage_path.exists():
        return f"ERROR: staged file does not exist: {path}"
    try:
        return stage_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"ERROR: staged file is not text/UTF-8: {path}"


@tool
def list_staged_files() -> str:
    """List every file staged so far in the current edit_ai job, showing
    whether each is a new file or a modification of an existing one. Use
    this to keep track of what you've changed during a long-running job."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)
    _monitor_update(
        job_id,
        activity="Reviewing staged files...",
        tool="list_staged_files",
        step=_incr_step(job_id),
    )
    if not job or not job["_files"]:
        return "No files staged yet in this job."

    lines = []
    for f in job["_files"]:
        real_exists = (PROJECT_ROOT / f).exists()
        lines.append(f"- {f} ({'modified' if real_exists else 'new file'})")
    return "\n".join(lines)


@tool
def diff_staged_file(path: str) -> str:
    """Show a unified diff between the real project file at `path` and its
    staged draft in the current job. If `path` is a new file (no real
    counterpart yet), the whole staged content is shown as additions. Use
    this before finishing, as a self-review step, to double check your
    changes look right."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)
    _monitor_update(
        job_id,
        activity=f"Diffing {path}...",
        tool="diff_staged_file",
        step=_incr_step(job_id),
    )

    try:
        rel = _safe_relative(path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    if job is None or str(rel) not in job["_files"]:
        return f"ERROR: {rel} is not staged in this job."

    stage_path = _job_workspace(job_id) / rel
    if not stage_path.exists():
        return f"ERROR: staged file missing on disk: {rel}"

    try:
        new_text = stage_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"ERROR: staged file is not text/UTF-8: {rel}"

    real_path = PROJECT_ROOT / rel
    old_text = ""
    if real_path.exists():
        try:
            old_text = real_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"ERROR: real file is not text/UTF-8: {rel}"

    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    diff_text = "".join(diff)
    return diff_text if diff_text else f"No differences: {rel} staged content matches the real file."


@tool
def discard_staged_file(path: str) -> str:
    """Remove a file from the current job's staging area, discarding that
    draft. Use this if you decide a change you staged earlier is no longer
    needed."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)
    _monitor_update(
        job_id,
        activity=f"Discarding staged draft: {path}...",
        tool="discard_staged_file",
        step=_incr_step(job_id),
    )

    try:
        rel = _safe_relative(path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    if job is None or str(rel) not in job["_files"]:
        return f"ERROR: {rel} is not staged in this job."

    stage_path = _job_workspace(job_id) / rel
    stage_path.unlink(missing_ok=True)
    job["_files"].remove(str(rel))
    return f"Discarded staged draft: {rel}"


@tool
def rename_staged_file(old_path: str, new_path: str) -> str:
    """Rename/move a staged draft from `old_path` to `new_path` (both
    relative to the project root). Only affects the staging area for the
    current job - use this if you realize a file you already staged
    should live at a different path."""
    job_id = _job_id_var.get()
    job = _jobs.get(job_id)
    _monitor_update(
        job_id,
        activity=f"Renaming staged draft: {old_path} -> {new_path}...",
        tool="rename_staged_file",
        step=_incr_step(job_id),
    )

    try:
        old_rel = _safe_relative(old_path)
        new_rel = _safe_relative(new_path)
    except ValueError as exc:
        return f"ERROR: {exc}"

    if job is None or str(old_rel) not in job["_files"]:
        return f"ERROR: {old_rel} is not staged in this job."

    workspace = _job_workspace(job_id)
    old_stage = workspace / old_rel
    new_stage = workspace / new_rel
    if not old_stage.exists():
        return f"ERROR: staged file missing on disk: {old_rel}"

    new_stage.parent.mkdir(parents=True, exist_ok=True)
    old_stage.rename(new_stage)

    job["_files"].remove(str(old_rel))
    if str(new_rel) not in job["_files"]:
        job["_files"].append(str(new_rel))

    return f"Renamed staged draft: {old_rel} -> {new_rel}"


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

    _monitor_update(job_id, activity="Testing staged files...", tool="test_files", step=_incr_step(job_id))

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

EDIT_TOOLS = [
    list_codebase_files,
    search_codebase,
    read_codebase_file,
    write_edited_file,
    create_new_file,
    read_staged_file,
    list_staged_files,
    diff_staged_file,
    discard_staged_file,
    rename_staged_file,
    test_files,
]


SYSTEM_PROMPT = """You are a coding assistant that edits an existing codebase.

WORKFLOW:
1. Use `list_codebase_files` (and `search_codebase` to find where something
   is defined/used) to see what exists before touching anything.
2. Use `read_codebase_file` to read every file you need before changing it.
3. Make your changes and save each changed or new file with
   `write_edited_file` (existing files) or `create_new_file` (new files),
   always passing the FULL new file content.
4. You may call `write_edited_file` again on the same path to revise a
   draft, `discard_staged_file` to drop one you no longer want, or
   `rename_staged_file` if a staged file should live at a different path.
   Nothing you write touches the real project - it only goes into a
   staging area the user reviews and applies later.
5. Use `test_files` to check staged Python files for syntax errors, and
   `diff_staged_file` on each changed file as a final self-review step so
   you can see exactly what will change before you finish. Use
   `list_staged_files` any time you want to check what you've staged so
   far in a long job.
6. When you are done, finish with a short plain-text summary (no markdown
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
    job["_step"] = 0

    _monitor_update(
        job_id,
        status="RUNNING",
        activity="Starting Edit AI...",
        progress=0,
        step=0,
    )

    token = _job_id_var.set(job_id)
    try:
        result = await _edit_agent.ainvoke(
            {"messages": [{"role": "user", "content": task}]},
            config={
                "configurable": {"thread_id": f"edit_ai_{job_id}"},
                "recursion_limit": RECURSION_LIMIT,
            },
        )
        metrics.record_result_messages("edit_ai", result.get("messages"))
    except Exception as exc:  # t.ex. recursion_limit uppnådd
        job["status"] = "failed"
        _monitor_update(
            job_id,
            status="FAILED",
            activity="Edit AI failed",
            step=job.get("_step"),
        )
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
    _monitor_update(
        job_id,
        status="DONE",
        activity="Edit AI complete",
        progress=100,
        step=job.get("_step"),
    )
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
        "_step": 0,
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

    _monitor_update(
        job_id,
        activity=f"Applying staged files ({files})...",
        tool="apply_edit_files",
    )

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


@tool
def list_apply_backups(job_id: str) -> str:
    """List backup snapshots created by `apply_edit_files` for a given
    edit_ai job. Each snapshot is timestamped and contains the pre-apply
    versions of whatever files were overwritten at that time. Use this
    before `restore_from_backup` to find the right timestamp."""
    _monitor_update(
        job_id,
        activity=f"Listing backups for job {job_id}...",
        tool="list_apply_backups",
    )
    backup_dir = _job_workspace(job_id) / "_backup"
    if not backup_dir.exists():
        return f"No backups found for job {job_id}."

    snapshots = sorted(p for p in backup_dir.iterdir() if p.is_dir())
    if not snapshots:
        return f"No backups found for job {job_id}."

    lines = []
    for snap in snapshots:
        files = [str(p.relative_to(snap)) for p in snap.rglob("*") if p.is_file()]
        lines.append(f"{snap.name}:\n" + "\n".join(f"  - {f}" for f in files))
    return "\n\n".join(lines)


@tool
def restore_from_backup(job_id: str, timestamp: str, files: str = "all") -> str:
    """Restore real project files from a backup snapshot created by
    `apply_edit_files` for job `job_id`. `timestamp` must match one shown
    by `list_apply_backups`. `files` is either 'all' or a comma-separated
    list of relative paths to restore from that snapshot. Use this to undo
    an apply that turned out to be wrong."""
    _monitor_update(
        job_id,
        activity=f"Restoring backup {timestamp} ({files})...",
        tool="restore_from_backup",
    )
    snap_dir = _job_workspace(job_id) / "_backup" / timestamp
    if not snap_dir.exists():
        return f"ERROR: no backup snapshot '{timestamp}' for job {job_id}."

    available = [str(p.relative_to(snap_dir)) for p in snap_dir.rglob("*") if p.is_file()]
    if not available:
        return f"Backup snapshot '{timestamp}' is empty."

    if files.strip().lower() == "all":
        wanted = available
    else:
        wanted = [f.strip() for f in files.split(",") if f.strip()]

    restored, skipped = [], []
    for rel_str in wanted:
        if rel_str not in available:
            skipped.append(f"{rel_str} (not in this snapshot)")
            continue
        try:
            rel = _safe_relative(rel_str)
        except ValueError as exc:
            skipped.append(f"{rel_str}: {exc}")
            continue

        target = PROJECT_ROOT / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap_dir / rel, target)
        restored.append(rel_str)

    parts = []
    if restored:
        parts.append("Restored:\n" + "\n".join(f"- {f}" for f in restored))
    if skipped:
        parts.append("Skipped:\n" + "\n".join(f"- {f}" for f in skipped))
    return "\n\n".join(parts) if parts else "Nothing was restored."


EDIT_AI_TOOLS = [
    edit_ai,
    edit_ai_status,
    apply_edit_files,
    list_apply_backups,
    restore_from_backup,
]
