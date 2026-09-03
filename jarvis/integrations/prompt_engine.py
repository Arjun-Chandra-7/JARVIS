"""Prompt Engine for Antigravity (agy).

Transforms voice instructions from the user into clear prompts from Jarvis to Google Antigravity CLI (agy).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def detect_project_stack(folder: str) -> dict:
    """Inspect a project directory and return tech stack metadata, test commands, and git status."""
    p = Path(folder)
    if not p.is_dir():
        return {
            "root": folder,
            "name": p.name or folder,
            "languages": [],
            "test_cmd": None,
            "lint_cmd": None,
            "git_branch": None,
            "git_status": "",
        }

    languages = []
    test_cmds = []
    lint_cmds = []

    # Python detection
    if (p / "pyproject.toml").exists() or (p / "requirements.txt").exists() or (p / "setup.py").exists():
        languages.append("Python")
        if (p / "pytest.ini").exists() or (p / "tests").is_dir() or (p / "test").is_dir():
            test_cmds.append("pytest -q")
        lint_cmds.append("ruff check .")

    # JavaScript / TypeScript / Node detection
    pkg_json = p / "package.json"
    if pkg_json.exists():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg_data.get("scripts", {})
            if (p / "tsconfig.json").exists():
                languages.append("TypeScript")
            else:
                languages.append("JavaScript")

            if "test" in scripts:
                test_cmds.append("npm test")
            if "lint" in scripts:
                lint_cmds.append("npm run lint")
            if "build" in scripts:
                test_cmds.append("npm run build")
        except Exception:  # noqa: BLE001
            languages.append("JavaScript/TypeScript")

    # Rust detection
    if (p / "Cargo.toml").exists():
        languages.append("Rust")
        test_cmds.append("cargo test")
        lint_cmds.append("cargo clippy")

    # Go detection
    if (p / "go.mod").exists():
        languages.append("Go")
        test_cmds.append("go test ./...")
        lint_cmds.append("golangci-lint run")

    # Git metadata
    git_branch = None
    git_status = ""
    try:
        r = subprocess.run(
            ["git", "-C", folder, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if r.returncode == 0:
            git_branch = r.stdout.strip()

        r_stat = subprocess.run(
            ["git", "-C", folder, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if r_stat.returncode == 0:
            git_status = r_stat.stdout.strip()
    except Exception:  # noqa: BLE001, S110
        pass

    return {
        "root": folder,
        "name": p.name or folder,
        "languages": languages or ["Unknown"],
        "test_cmd": " && ".join(test_cmds) if test_cmds else None,
        "lint_cmd": " && ".join(lint_cmds) if lint_cmds else None,
        "git_branch": git_branch,
        "git_status": git_status[:300] if git_status else "clean",
    }


def clean_voice_text(text: str) -> str:
    """Strip wake words and conversational filler from voice transcripts."""
    s = text.strip()
    patterns = [
        r"^(?:hey\s+|hi\s+|hello\s+)?jarvis[\s,:]*",
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?(?:just\s+)?",
        r"^(?:in\s+vscode|in\s+vs\s+code|on\s+vscode|on\s+vs\s+code)[\s,:]*",
        r"^(?:tell\s+(?:agy|antigravity)\s+(?:that|to)?|ask\s+(?:agy|antigravity)\s+(?:that|to)?)[\s,:]*",
    ]
    changed = True
    while changed:
        changed = False
        for pat in patterns:
            new_s = re.sub(pat, "", s, flags=re.IGNORECASE).strip()
            if new_s != s:
                s = new_s
                changed = True
    return s or text.strip()


def refine_prompt(
    raw_prompt: str,
    folder: str,
    active_file: str | None = None,
    active_file_path: str | None = None,
    user_name: str = "Developer",
) -> tuple[str, str]:
    """Format a prompt from Jarvis to Antigravity (agy).

    Introduces Jarvis as the speaker and provides the task and active file context.

    Returns:
        tuple[prompt, spoken_summary]
    """
    clean_prompt = clean_voice_text(raw_prompt)
    target_file = active_file_path or active_file
    project_name = Path(folder).name or folder

    greeting = "Hi, I am Jarvis and I am speaking as Jarvis."
    lines = []
    if not clean_prompt.lower().startswith("hi, i am jarvis"):
        lines.append(greeting)
    if clean_prompt:
        lines.append(clean_prompt)
    if target_file:
        lines.append(f"Active file: {target_file}")

    brief = "\n\n".join(lines).strip()
    target_name = active_file or project_name
    spoken_summary = f"Refined task for {target_name}: {clean_prompt[:50]}."
    return brief, spoken_summary

