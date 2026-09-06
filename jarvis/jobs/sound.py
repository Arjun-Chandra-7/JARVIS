"""Best-effort short UI sounds (job completion). Never raises, never blocks the caller."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "sounds"


def _players(asset: Path) -> list[list[str]]:
    cmds = [["canberra-gtk-play", "-i", "complete"]]
    if asset.exists():
        cmds += [["pw-play", str(asset)], ["paplay", str(asset)],
                 ["aplay", "-q", str(asset)],
                 ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(asset)]]
    return cmds


def chime(name: str = "task-complete") -> bool:
    """Play a short completion cue in the background. Returns True if a player was launched."""
    asset = _ASSET_DIR / f"{name}.wav"
    for cmd in _players(asset):
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False
