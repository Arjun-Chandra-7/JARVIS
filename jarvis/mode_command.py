"""«study mode» and «Iron Man mode» — the two that change what the machine is for."""

from __future__ import annotations

import asyncio
from typing import Optional

from .modes import ironman, study


async def handle(text: str, config=None) -> Optional[str]:
    said = (text or "").strip()
    if not said:
        return None

    # ---- study mode
    if study.asked_to_stop(said):
        was = study.stop()
        if was is None:
            return "Study mode wasn't on, sir."
        apps = len(set(was.closed_apps))
        return (f"Study mode off after {was.minutes()} minutes, sir. "
                f"I kept {apps} thing{'s' if apps != 1 else ''} shut while you worked."
                if apps else f"Study mode off after {was.minutes()} minutes, sir.")

    if study.asked_to_start(said):
        if study.on():
            return "Already in study mode, sir."
        study.start()
        apps, tabs = await study.enforce()
        shut = len(apps) + len(tabs)
        return ("Study mode, sir. " +
                (f"Closed {shut} distraction{'s' if shut != 1 else ''}, and I'll keep them closed. "
                 if shut else "Nothing to close. ") +
                "Say “exit study mode” when you're done.")

    # ---- Iron Man mode
    if ironman.asked_to_stop(said):
        if not ironman.on():
            return None              # "normal mode" said cold means nothing; let the model have it
        done = ironman.deactivate()
        return f"{ironman.STOOD_DOWN} That was {done['minutes']} minutes in the workshop."

    if ironman.asked_to_start(said):
        if ironman.on():
            return "Already in Iron Man mode, sir."
        result = await asyncio.to_thread(ironman.activate)
        placed = [k for k, ok in result["placed"].items() if ok]
        missing = [k for k, ok in result["placed"].items() if not ok]
        detail = f"Laid out {', '.join(placed)}." if placed else ""
        if missing:
            detail += f" I couldn't find {', '.join(missing)} to place."
        return f"{ironman.ANNOUNCEMENT} {detail}".strip()

    return None
