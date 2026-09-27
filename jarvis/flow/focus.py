"""Talks to scripts/atspi_focus_bridge.py: the focused field, read and written through AT-SPI.

The bridge runs under system Python (PyGObject is not in the venv) as one long-lived child, so
each question costs a pipe round trip rather than a process start. It is restarted if it dies.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Optional

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "atspi_focus_bridge.py"


class FocusBridge:
    def __init__(self, python: str = "/usr/bin/python3") -> None:
        self.python = python
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def _ensure(self) -> Optional[subprocess.Popen]:
        if self._proc is not None and self._proc.poll() is None:
            return self._proc
        try:
            from ..screen.providers import _atspi_env
            env = _atspi_env()
        except Exception:  # noqa: BLE001
            env = None
        try:
            self._proc = subprocess.Popen([self.python, str(_SCRIPT)], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          text=True, bufsize=1, env=env)
        except OSError:
            self._proc = None
        return self._proc

    def request(self, op: str, timeout: float = 4.0, **args) -> dict:
        with self._lock:
            proc = self._ensure()
            if proc is None or proc.stdin is None or proc.stdout is None:
                return {"ok": False, "reason": "accessibility bridge unavailable"}
            box: dict = {}

            def read():
                try:
                    proc.stdin.write(json.dumps({"op": op, **args}, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                    box["line"] = proc.stdout.readline()
                except OSError:
                    box["line"] = ""

            t = threading.Thread(target=read, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive() or not box.get("line"):
                self.close()                   # a stuck bridge is restarted on the next call
                return {"ok": False, "reason": "accessibility bridge did not answer"}
            try:
                return json.loads(box["line"])
            except ValueError:
                return {"ok": False, "reason": "accessibility bridge sent garbage"}

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass


BRIDGE = FocusBridge()
