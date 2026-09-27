"""The teaching overlay's renderer, run for real: teach.html in an offscreen Electron window.

Skipped where Electron or a display is missing. Where it runs, it checks the renderer's own
defences — the ones that hold even if everything upstream were wrong: stale generations, script
in a batch, object limits, cancellation, pausing, anchors going stale, emergency dismissal,
reduced motion and reporting idle.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ELECTRON = next((p for p in (ROOT / "overlay/node_modules/.bin/electron",
                             Path("/home/xor_sensei/Madara/Dev/Jarvis/overlay/node_modules/.bin/electron")) if p.exists()), None)


@pytest.fixture(scope="module")
def results():
    if ELECTRON is None or not os.environ.get("DISPLAY"):
        pytest.skip("no Electron or no display")
    done = subprocess.run([str(ELECTRON), str(ROOT / "overlay/teach/renderer-check.js")], capture_output=True,
                          text=True, timeout=90)
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("{")), None)
    assert line, done.stderr[-1500:]
    return json.loads(line)


def test_old_generations_and_scripts_never_draw(results):
    assert results["drawn"] == 1 and results["after_stale"] == 1 and results["stale_reported"]
    assert results["rejected"] == 2 and results["after_bad"] == 1
    assert results["later_landed"] is False


def test_cancel_pause_and_reduced_motion(results):
    assert results["cancelled_gone"] and results["paused_holds"] and results["reduced_no_dash"]


def test_anchors_follow_then_hide_when_stale(results):
    assert results["anchor_moved"].startswith("translate(50 20)")
    assert results["anchor_stale_hidden"]


def test_limits_and_dismissal(results):
    assert results["objects_capped"] == 400 and results["limit_errors"] > 0
    assert results["after_dismiss"] == 0
    assert results["idle_reported"] and results["frames"] > 5
