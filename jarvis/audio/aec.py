"""Echo cancellation at the audio layer: what Jarvis plays is subtracted from what it hears.

PipeWire's echo-cancel module with the WebRTC canceller runs as its own small PipeWire client
(``scripts/pipewire/jarvis-aec.conf``, started by the ``jarvis-aec`` user service). It creates

* ``jarvis_aec_sink``   — made the default output, so everything played on this machine (Jarvis,
  the browser's video, music) passes through it on its way to the speakers and becomes the
  reference signal, and
* ``jarvis_aec_source`` — the microphone with that reference subtracted, which Jarvis listens on.

So Jarvis does not hear itself, and a lecture playing aloud is mostly removed before the wake word,
barge-in or the recogniser see it — while the person's own voice, which is not in the reference,
passes through. Nothing is muted while audio plays.

``JARVIS_AEC``: ``auto`` (default: use it when the service is running), ``on`` (the same, and say
so when it is missing), ``off``.
"""
from __future__ import annotations

import os
import subprocess

SOURCE = "jarvis_aec_source"
SINK = "jarvis_aec_sink"


def mode() -> str:
    value = os.environ.get("JARVIS_AEC", "auto").strip().lower()
    return value if value in {"auto", "on", "off"} else "auto"


def running() -> bool:
    """Is the echo-cancelled microphone there to be opened?"""
    try:
        out = subprocess.run(["pw-cli", "ls", "Node"], capture_output=True, text=True,
                             timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f'node.name = "{SOURCE}"' in out


def input_node() -> str:
    """The PipeWire node the microphone should be opened on, or "" for the system default.

    ``JARVIS_MIC_NODE`` names one outright — a particular headset, or the virtual source the
    end-to-end voice test plays speech into."""
    explicit = os.environ.get("JARVIS_MIC_NODE", "").strip()
    if explicit:
        return explicit
    if mode() == "off":
        return ""
    return SOURCE if running() else ""
