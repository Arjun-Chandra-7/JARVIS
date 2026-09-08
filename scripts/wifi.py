#!/usr/bin/env python3
"""List nearby Wi-Fi networks and show passwords this machine has saved.

  .venv/bin/python scripts/wifi.py

Reveals the pre-shared key only for networks this computer has already connected to
(your own saved credentials). It does not and cannot recover a network you never joined.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jarvis.integrations import wifi_scan  # noqa: E402

if __name__ == "__main__":
    print(wifi_scan.report())
