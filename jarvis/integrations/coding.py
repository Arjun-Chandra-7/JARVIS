"""Coding-agent hand-off: detect the active workspace and file in VS Code and drive the Antigravity CLI (agy).

"Jarvis, fix this code" -> pinpoint the exact file and folder focused in VS Code,
refine the prompt into a structured engineering brief, and run `agy` CLI directly on it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_SNAP = ("LD_LIBRARY_PATH", "LD_PRELOAD")
AGY_BIN = Path.home() / ".local/bin/agy"


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SNAP}


def _run(argv: list[str], timeout: int = 6) -> str:
    try:
        return subprocess.run(argv, env=_env(), timeout=timeout, capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return ""


def _recent_folders() -> list[str]:
    """Full paths VS Code has opened recently (newest first)."""
    store = Path.home() / ".config/Code/User/globalStorage/storage.json"
    try:
        blob = json.dumps(json.loads(store.read_text()))
    except Exception:  # noqa: BLE001
        return []
    seen, out = set(), []
    for uri in re.findall(r"file://([^\"]+)", blob):
        p = uri.rstrip("/")
        if p and p not in seen and Path(p).is_dir():
            seen.add(p)
            out.append(p)
    return out


def active_context() -> dict:
    """Return precision context about where the user currently is in VS Code:

    {
        "folder": str | None,          # Workspace root directory
        "file": str | None,            # Active file basename (e.g. "apps.py")
        "file_path": str | None,       # Exact relative file path (e.g. "jarvis/integrations/apps.py")
        "full_file_path": str | None,  # Absolute file path
        "project_name": str | None,    # Project name (e.g. "jarvis")
        "is_vscode_active": bool       # True if VS Code is currently focused
    }
    """
    title = _run(["xdotool", "getactivewindow", "getwindowname"]).strip()
    is_vscode_active = "Visual Studio Code" in title

    active_file: Optional[str] = None
    project_name: Optional[str] = None

    # Format 1: "[● ]file.ext - ProjectName - Visual Studio Code"
    # Format 2: "ProjectName - Visual Studio Code"
    m = re.search(r"^(?:●\s*)?(?:(.+?)\s*-\s*)?([^-]+?)\s*-\s*Visual Studio Code$", title)
    if m:
        if m.group(1):
            active_file = m.group(1).strip()
        project_name = m.group(2).strip()

    recents = _recent_folders()
    matched_folder: Optional[str] = None

    if project_name:
        for p in recents:
            if Path(p).name == project_name:
                matched_folder = p
                break

    # Fallback: scan wmctrl for any open VS Code window
    if not matched_folder:
        windows = _run(["wmctrl", "-l"])
        for line in windows.splitlines():
            m_win = re.search(r"-\s*([^-]+?)\s*-\s*Visual Studio Code", line)
            if m_win:
                win_name = m_win.group(1).strip()
                for p in recents:
                    if Path(p).name == win_name:
                        matched_folder = p
                        if not project_name:
                            project_name = win_name
                        break
            if matched_folder:
                break

    # Fallback: read lastActiveWindow from storage.json
    if not matched_folder:
        try:
            store = Path.home() / ".config/Code/User/globalStorage/storage.json"
            if store.exists():
                data = json.loads(store.read_text())
                last_win = data.get("windowsState", {}).get("lastActiveWindow", {})
                last_uri = last_win.get("folder", "")
                if last_uri.startswith("file://"):
                    cand = last_uri[7:].rstrip("/")
                    if Path(cand).is_dir():
                        matched_folder = cand
        except Exception:
            pass

    if not matched_folder and recents:
        matched_folder = recents[0]

    final_name = Path(matched_folder).name if matched_folder else project_name

    # Precision file resolution within the workspace
    file_rel_path: Optional[str] = None
    full_path: Optional[str] = None

    if matched_folder and active_file:
        folder_path = Path(matched_folder)
        direct = folder_path / active_file
        if direct.is_file():
            file_rel_path = active_file
            full_path = str(direct)
        else:
            # Match via git ls-files first for fast indexing
            git_files = _run(["git", "-C", matched_folder, "ls-files"]).splitlines()
            for gf in git_files:
                if Path(gf).name == active_file or gf.endswith(f"/{active_file}"):
                    file_rel_path = gf
                    full_path = str(folder_path / gf)
                    break

            # Filesystem search fallback
            if not file_rel_path:
                for cand_file in folder_path.rglob(active_file):
                    if not any(
                        p.startswith(".") or p in ("node_modules", "__pycache__", "venv", ".venv")
                        for p in cand_file.relative_to(folder_path).parts
                    ):
                        file_rel_path = str(cand_file.relative_to(folder_path))
                        full_path = str(cand_file)
                        break

    return {
        "folder": matched_folder,
        "file": active_file,
        "file_path": file_rel_path,
        "full_file_path": full_path,
        "project_name": final_name,
        "is_vscode_active": is_vscode_active,
    }


def active_folder() -> str | None:
    """The project folder of the currently focused VS Code window."""
    return active_context().get("folder")


def overview(folder: str, limit: int = 60) -> str:
    """A compact map of the project files."""
    d = Path(folder)
    files = _run(["git", "-C", folder, "ls-files"]).splitlines()
    if not files:
        files = [
            str(p.relative_to(d))
            for p in d.rglob("*")
            if p.is_file()
            and not any(
                part.startswith(".")
                or part in ("node_modules", "__pycache__", "venv", ".venv", "dist", "build")
                for part in p.relative_to(d).parts
            )
        ][:200]
    files = files[:limit]
    return f"{folder} ({len(files)} files shown):\n" + "\n".join(files)


def run_agy_headless(
    folder: str,
    prompt: str,
    timeout_s: int = 600,
    effort: str = "high",
) -> dict:
    """Run Antigravity CLI (`agy`) directly in the active VS Code workspace folder.

    Never launches any GUI app — pure headless agentic CLI.
    """
    agy_path = str(AGY_BIN) if AGY_BIN.exists() else shutil.which("agy")
    if not agy_path:
        return {
            "ok": False,
            "output": "",
            "stderr": "Antigravity CLI ('agy') not found in PATH or ~/.local/bin/agy.",
            "diff_stat": "",
            "status_porcelain": "",
            "summary": "Antigravity CLI binary not installed.",
            "files_touched": [],
        }

    args = [
        agy_path,
        "--new-project",
        "--dangerously-skip-permissions",
        "--effort",
        effort,
        "--print",
        prompt,
    ]

    try:
        proc = subprocess.run(
            args,
            cwd=folder,
            env=_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "output": "",
            "stderr": f"Antigravity CLI timed out after {timeout_s} seconds.",
            "diff_stat": "",
            "status_porcelain": "",
            "summary": "Antigravity CLI task timed out.",
            "files_touched": [],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "output": "",
            "stderr": str(exc),
            "diff_stat": "",
            "status_porcelain": "",
            "summary": f"Failed to execute agy CLI: {exc}",
            "files_touched": [],
        }

    diff_stat = _run(["git", "-C", folder, "diff", "--stat"]).strip()
    status_porcelain = _run(["git", "-C", folder, "status", "--porcelain"]).strip()

    files_touched = []
    if status_porcelain:
        for line in status_porcelain.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                files_touched.append(parts[1])

    summary = stdout.splitlines()[-1] if stdout else ("Completed successfully." if ok else "Execution failed.")
    if diff_stat:
        diff_lines = diff_stat.splitlines()
        last_line = diff_lines[-1] if diff_lines else ""
        summary += f" ({last_line.strip()})"

    return {
        "ok": ok,
        "output": stdout,
        "stderr": stderr,
        "diff_stat": diff_stat,
        "status_porcelain": status_porcelain,
        "summary": summary,
        "files_touched": files_touched,
    }
