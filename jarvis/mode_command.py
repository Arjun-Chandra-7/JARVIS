"""«study mode» and «Iron Man mode» — the two that change what the machine is for."""

from __future__ import annotations

import asyncio
from typing import Optional

from .modes import ironman, study


async def _tell_the_overlay(shape: str) -> None:
    """Ask the overlay to change shape. Best effort: the mode works without it."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3) as client:
            await client.post("http://127.0.0.1:8770/emit",
                              json={"kind": "mode", "text": shape})
    except Exception:  # noqa: BLE001
        pass


async def handle(text: str, config=None) -> Optional[str]:
    said = (text or "").strip()
    if not said:
        return None

    # ---- study mode
    if study.asked_to_stop(said):
        from .modes import exam_tutor

        exam_tutor.forget()
        study.forget_judgements()
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

        # The tutor comes up with the mode, so the brief is already in place before the first
        # question rather than being pasted in again every session.
        from .modes import exam_tutor

        tutor_said = ""
        try:
            _opened, tutor_said = await exam_tutor.open_with_brief()
        except Exception:  # noqa: BLE001 — study mode's job is closing things; this is extra
            tutor_said = ""

        return " ".join(p for p in (
            "Study mode, sir.",
            (f"Closed {shut} distraction{'s' if shut != 1 else ''}, and I'll keep them closed."
             if shut else "Nothing to close."),
            tutor_said,
            "Say “exit study mode” when you're done.",
        ) if p)

    # ---- Iron Man mode
    if ironman.asked_to_stop(said):
        done = ironman.deactivate()
        await _tell_the_overlay("pill")
        if done["was_on"]:
            said = f"{ironman.STOOD_DOWN} That was {done['minutes']} minutes in the workshop."
            if not done.get("restored", True):
                # Never imply the desktop was put back when there was no record of how it was.
                said += (" I had no record of how your windows were arranged, so I've left them "
                         "as they are.")
            return said
        # The overlay and backend are separate processes. If one restarted, the in-memory flag
        # cannot tell us what is actually on screen, so "normal mode" is always a rescue command.
        return ironman.STOOD_DOWN

    if ironman.asked_to_start(said):
        if ironman.on():
            return "Already in Iron Man mode, sir."
        # Said before the sequence runs, not after it half-works. An empty terminal pane has one
        # cause and the answer should arrive before the question does.
        from .modes import preflight

        warning = await asyncio.to_thread(preflight.spoken_warning)
        result = await asyncio.to_thread(ironman.activate)
        await _tell_the_overlay("ironman")
        placed = [k for k, ok in result["placed"].items() if ok]
        missing = [k for k, ok in result["placed"].items() if not ok]
        detail = f"Laid out {', '.join(placed)}." if placed else ""
        if missing:
            detail += f" I couldn't find {', '.join(missing)} to place."
        return " ".join(p for p in (ironman.ANNOUNCEMENT, detail, warning) if p).strip()

    return None
