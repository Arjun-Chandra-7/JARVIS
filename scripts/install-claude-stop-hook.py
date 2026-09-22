#!/usr/bin/env python3
"""Add Jarvis's Stop hook while preserving the user's other Claude settings."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "claude-stop-hook.py"
PYTHON = ROOT / ".venv" / "bin" / "python"
SETTINGS = Path.home() / ".claude" / "settings.json"


def main() -> None:
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    hooks = settings.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    command = f"{PYTHON} {HOOK}"
    if any(command == hook.get("command") for entry in stop for hook in entry.get("hooks", [])):
        print("Jarvis Claude Stop hook already installed")
        return
    stop.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temp = SETTINGS.with_suffix(".json.jarvis-tmp")
    temp.write_text(json.dumps(settings, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(SETTINGS)
    print("Installed Jarvis Claude Stop hook")


if __name__ == "__main__":
    sys.exit(main())
