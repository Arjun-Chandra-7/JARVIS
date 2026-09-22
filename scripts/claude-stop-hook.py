#!/usr/bin/env python3
"""Claude Code Stop hook entry point; safe to call from any working directory."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.coding.claude_stop import main  # noqa: E402


if __name__ == "__main__":
    main()
