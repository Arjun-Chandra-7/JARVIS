"""Start a Firefox-family browser so it can be driven, including one shipped as a flatpak.

Marionette listens only when the browser was started with `--marionette`. That is the same trade
the debug port asked of Opera — one restart — with one difference worth knowing: Firefox restores
its session by default, so it costs a few seconds of flicker rather than your tabs.

The flatpak is the part that needs care. `zen` is not on PATH; the browser is
`flatpak run app.zen_browser.zen`, it cannot see paths outside its sandbox unless they are
granted, and it will not accept a second launch as a new instance unless told. Getting any of
that wrong produces a browser that starts, looks fine, and never answers on 2828.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Optional

from . import marionette, web_browser

# How long to wait for the port after asking for a launch. A cold flatpak start is slow —
# measured at eight to twelve seconds on this machine — and giving up early looks like failure.
LAUNCH_WAIT_S = 30.0


def flatpak_id(name: Optional[str] = None) -> str:
    """The flatpak application id, when the chosen browser is one. Empty when it is not."""
    chosen = name if name is not None else web_browser.preferred()
    # A flatpak desktop id is the application id: "app.zen_browser.zen".
    return chosen if chosen.count(".") >= 2 and shutil.which("flatpak") else ""


def _argv(name: str, extra: list[str]) -> Optional[list[str]]:
    app = flatpak_id(name)
    if app:
        return ["flatpak", "run", app, *extra]
    exe = shutil.which(name) or shutil.which(name.split(".")[-1])
    return [exe, *extra] if exe else None


def running(name: Optional[str] = None) -> bool:
    """Whether this browser has a process at all, driveable or not."""
    chosen = (name if name is not None else web_browser.preferred()).split(".")[-1]
    if not chosen:
        return False
    try:
        return subprocess.run(["pgrep", "-f", chosen],
                              capture_output=True, timeout=4).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def control_ready() -> bool:
    """Whether a Firefox-family browser is listening for automation right now."""
    return marionette.reachable()


def launch(url: str = "", name: Optional[str] = None,
           wait_s: float = LAUNCH_WAIT_S) -> bool:
    """Start the browser with Marionette on. True once the port answers."""
    chosen = name if name is not None else web_browser.preferred()
    if not chosen:
        return False
    extra = ["--marionette"]
    if url:
        extra.append(url)
    argv = _argv(chosen, extra)
    if not argv:
        return False

    # The same environment scrubbing the Chromium launcher does: a snap or flatpak inherits
    # loader variables from whatever started Jarvis and fails in ways that look like its own bug.
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR")}
    try:
        subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False

    deadline = time.time() + wait_s
    while time.time() < deadline:
        if control_ready():
            return True
        time.sleep(0.5)
    return False


def stop(name: Optional[str] = None) -> None:
    chosen = (name if name is not None else web_browser.preferred()).split(".")[-1]
    if not chosen:
        return
    try:
        subprocess.run(["pkill", "-f", chosen], timeout=5)
    except Exception:  # noqa: BLE001
        pass


def ensure(url: str = "", allow_restart: bool = False) -> dict:
    """Make a driveable Firefox available, or say precisely why there is not one.

    The shape matches `browser.ensure` so the caller does not branch on which family it is
    talking to — only the words differ, because the remedy differs.
    """
    if control_ready():
        return {"ok": True, "state": "ready", "message": "Zen is under control."}

    spoken = web_browser._spoken(web_browser.preferred())
    if running():
        if not allow_restart:
            return {
                "ok": False, "state": "needs_restart",
                "message": (f"{spoken} is running without automation enabled, so I can open "
                            "pages but not click inside them. Restarting it with automation on "
                            "restores your tabs — say 'restart the browser with control' and I "
                            "will."),
            }
        stop()
        time.sleep(2.0)
        if launch(url):
            return {"ok": True, "state": "launched",
                    "message": f"{spoken} restarted with control, restoring your tabs."}
        return {"ok": False, "state": "missing",
                "message": f"{spoken} did not come back with automation enabled."}

    if launch(url):
        return {"ok": True, "state": "launched", "message": f"{spoken} started with control."}
    return {"ok": False, "state": "missing",
            "message": f"I could not start {spoken} with automation enabled."}
