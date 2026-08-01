"""The Obsidian vault as Jarvis's memory brain: structure, seeding, and git versioning.

Jarvis reads and writes the vault with Claude Code's built-in file tools; this module just
guarantees the structure exists, keeps a pinned copy of the profile, and auto-commits every
change so nothing the model does is unrecoverable.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

SUBDIRS = ["Jarvis", "Jarvis/journal", "Projects", "People", "Topics", "Archive"]

PROFILE_TEMPLATE = """\
---
author: jarvis
type: profile
updated: {today}
---
# Profile — {user}

_Durable facts Jarvis keeps about you. Jarvis reads this at the start of every session and
updates it as it learns more. Edit it anytime._

## About
- Name:
- Location / timezone:
- Machine(s):

## Preferences
- How I like to be addressed:
- Communication style:

## Context
- Current focus:
- Active projects:
"""

VAULT_README = """\
# Jarvis's memory vault

This Obsidian vault is Jarvis's long-term memory. Jarvis reads and writes notes here, and you can
open the same vault in Obsidian.

- `Jarvis/profile.md` — durable facts about you (pinned into context every session)
- `Jarvis/journal/` — daily journal entries
- `Projects/<name>/index.md` — per-project memory: goal, decisions, tasks, people
- `People/`, `Topics/` — entities, linked with `[[wikilinks]]`
- `Archive/` — retired notes (Jarvis never hard-deletes; it moves things here)

Notes Jarvis writes carry `author: jarvis` in their frontmatter. Everything is git-versioned —
every change is a commit you can diff or revert.
"""

PROJECTS_README = """\
# Projects

One folder per project: `Projects/<name>/index.md` is the project's brain — goal, status,
decisions, open tasks (`- [ ]`), and linked people. Say "work on <name>" and Jarvis focuses there.
"""


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git", "-C", str(vault),
            "-c", "user.name=Jarvis", "-c", "user.email=jarvis@localhost",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def is_git_repo(vault: Path) -> bool:
    return (vault / ".git").exists()


def _seed_files(vault: Path, user_name: str) -> None:
    seeds = {
        vault / "Jarvis" / "profile.md": PROFILE_TEMPLATE.format(
            user=user_name, today=date.today().isoformat()
        ),
        vault / "Jarvis" / "README.md": VAULT_README,
        vault / "Projects" / "README.md": PROJECTS_README,
        vault / ".gitignore": ".jarvis/\n.trash/\n",
    }
    for path, content in seeds.items():
        if not path.exists():
            path.write_text(content)


def ensure_vault(vault: Path, user_name: str = "you") -> bool:
    """Create the vault structure + git repo if missing. Returns True if newly created."""
    created = not vault.exists()
    for sub in SUBDIRS:
        (vault / sub).mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(vault, 0o700)  # Security: shield long-term memory vault from unauthorized local users
    except OSError:
        pass
    _seed_files(vault, user_name)
    if not is_git_repo(vault):
        _git(vault, "init", "-q")
        _git(vault, "add", "-A")
        _git(vault, "commit", "-q", "-m", "jarvis: initialize memory vault")
    return created


def read_profile(vault: Path) -> str:
    try:
        return (vault / "Jarvis" / "profile.md").read_text()
    except OSError:
        return ""


def read_today_journal(vault: Path) -> str:
    path = vault / "Jarvis" / "journal" / f"{date.today().isoformat()}.md"
    try:
        return path.read_text()
    except OSError:
        return ""


def journal_append(vault: Path, line: str) -> None:
    """Append a timestamped bullet to today's journal note (creating it if needed).

    This is Jarvis's episodic memory of *when* things happened — powering "what did I do today"
    and the morning briefing. `line` is a short human phrase; the timestamp is added here.
    """
    from datetime import datetime

    day = date.today()
    jdir = vault / "Jarvis" / "journal"
    jdir.mkdir(parents=True, exist_ok=True)
    path = jdir / f"{day.isoformat()}.md"
    if not path.exists():
        path.write_text(f"---\nauthor: jarvis\n---\n# {day:%A, %d %B %Y}\n\n")
    with path.open("a") as f:
        f.write(f"- {datetime.now():%H:%M} — {line.strip()}\n")


def git_autocommit(vault: Path, message: str) -> bool:
    """Commit any pending vault changes. Returns True if a commit was made."""
    if not is_git_repo(vault):
        return False
    status = _git(vault, "status", "--porcelain")
    if not status.stdout.strip():
        return False
    _git(vault, "add", "-A")
    _git(vault, "commit", "-q", "-m", message)
    return True
