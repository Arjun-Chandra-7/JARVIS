"""Coding-agent hand-off: figure out which folder is open in VS Code and drive Antigravity on it.

"Jarvis, fix this code on my screen" → find the focused VS Code project, summarise it so Jarvis can
discuss it, then launch Antigravity (Google's agentic IDE) on that folder to do the work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

_SNAP = ("LD_LIBRARY_PATH", "LD_PRELOAD")


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


def active_folder() -> str | None:
    """The project folder of the currently focused VS Code window (best-effort)."""
    title = _run(["xdotool", "getactivewindow", "getwindowname"]).strip()
    m = re.search(r"-\s*([^-]+?)\s*-\s*Visual Studio Code", title)
    name = m.group(1).strip() if m else None
    recents = _recent_folders()
    if name:
        for p in recents:
            if Path(p).name == name:
                return p
    # fall back: any open VS Code window title → match a recent folder
    windows = _run(["wmctrl", "-l"])
    for line in windows.splitlines():
        m = re.search(r"-\s*([^-]+?)\s*-\s*Visual Studio Code", line)
        if m:
            for p in recents:
                if Path(p).name == m.group(1).strip():
                    return p
    return recents[0] if recents else None


def overview(folder: str, limit: int = 60) -> str:
    """A compact map of the project: tracked files (git) or a shallow file listing."""
    d = Path(folder)
    files = _run(["git", "-C", folder, "ls-files"]).splitlines()
    if not files:
        files = [str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()
                 and not any(part.startswith(".") or part in ("node_modules", "__pycache__", "venv", ".venv")
                             for part in p.relative_to(d).parts)][:200]
    files = files[:limit]
    return f"{folder} ({len(files)} files shown):\n" + "\n".join(files)


def open_antigravity(folder: str) -> bool:
    if not shutil.which("antigravity"):
        return False
    try:
        subprocess.Popen(["antigravity", folder], env=_env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except Exception:  # noqa: BLE001
        return False
