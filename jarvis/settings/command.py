"""The deterministic handler for "disable your animations", "thoda tez bolo", "undo that".

Only the owner's own front-ends may change a setting (see ``jarvis.trust``). The answer says
what is now true, as the component reported it: a change the overlay or the voice confirmed is
stated plainly; one that was saved but not confirmed says so; one the component contradicted
was put back, and the answer says that instead.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from .. import route_log
from ..trust import is_trusted, own_words
from . import parse as parser
from . import registry

# "Undo that" is ambiguous on its own. It means the last setting change only while that change
# is fresh; after this long it is left for whatever else "undo" might refer to.
UNDO_FRESH_S = 15 * 60


def _pick(options: tuple[str, ...], seed: int) -> str:
    # Varied but deterministic: the same revision always gets the same wording, which keeps
    # tests stable and still avoids saying "Done, sir" every single time.
    return options[seed % len(options)]


def _describe(change: registry.Change) -> str:
    s, new = change.setting, change.new
    sid = s.id
    if sid == "overlay.animations":
        return "animations are off" if not new else "animations are back on"
    if sid == "overlay.visible":
        return "the overlay is hidden" if not new else "the overlay is showing"
    if sid == "overlay.intensity":
        return f"the overlay is at {round(new * 100)}% brightness"
    if sid == "motion.reduced":
        return "motion is reduced" if new else "full motion is back"
    if sid == "voice.speed":
        if change.old is not None and new > change.old:
            pace = "a little faster" if new - change.old <= 0.06 else "faster"
        elif change.old is not None and new < change.old:
            pace = "a little slower" if change.old - new <= 0.06 else "slower"
        else:
            pace = "at the usual pace" if new == 1.0 else "at this pace"
        return f"I'm speaking {pace} — {new:.2f}×"
    if sid == "voice.verbosity":
        return {"brief": "I'll keep answers short", "detailed": "I'll give fuller answers",
                "normal": "answers are back to normal length"}[new]
    if sid == "notifications.level":
        return {"all": "notifications will be read out again",
                "quiet": "I won't read out notifications",
                "urgent": "I'll only announce urgent things"}[new]
    if sid == "voice.follow_up_s":
        return f"I'll keep listening for {new} seconds after I answer"
    if sid == "away.replies":
        return ("away mode has stopped replying — nothing more will be sent" if not new
                else "away-mode replies are allowed again; away mode itself is still off")
    if sid == "dictation.history":
        return "I've stopped keeping dictation history" if not new else "dictation history is on again"
    if sid == "teach.glow":
        return f"the teaching pen glow is at {round(new * 100)}%"
    return f"{s.name} is {s.say(new)}"


def _until_phrase(until: Optional[float]) -> str:
    if not until:
        return ""
    minutes = max(1, round((until - time.time()) / 60))
    if minutes >= 60 and minutes % 60 == 0:
        hours = minutes // 60
        return f" for the next {hours} hour{'s' if hours != 1 else ''}"
    return f" for the next {minutes} minutes"


def reply_for(change: registry.Change) -> str:
    what = _describe(change)
    v = change.verification
    status = getattr(v, "status", "unconfirmed")
    if change.restored:
        return (f"I tried, but {change.setting.name} didn't take the change "
                f"({v.detail}), so I've left it as it was.")
    if status == "verified":
        sentence = what[0].upper() + what[1:] + _until_phrase(change.until) + "."
        if change.undo_of is not None:
            return f"Undone — {what}."
        return _pick((sentence, f"Done — {what}{_until_phrase(change.until)}.",
                      f"{sentence[:-1]}, sir."), change.revision)
    detail = f" — {v.detail}" if v is not None and v.detail else ""
    prefix = "Undone and saved" if change.undo_of is not None else "Saved"
    return (f"{prefix}: {what}{_until_phrase(change.until)}. I couldn't confirm it took effect yet"
            f"{detail}; it applies as soon as that part is running.")


def _list_reply() -> str:
    now = registry.snapshot()
    parts = [f"{s.name} {s.say(now[sid])}" for sid, s in registry.SETTINGS.items()]
    return "Current settings: " + "; ".join(parts) + "."


async def handle(text: str, config=None, session_id: str = "local") -> Optional[str]:
    said = own_words(text)
    request = parser.parse(said)
    if request is None:
        return None
    if not is_trusted(session_id):
        # A settings change from a message, a caller or a page is content, not a request.
        route_log.record(intent="settings", action="refused_untrusted", source=session_id[:20])
        return None
    if request.op == "list":
        return _list_reply()
    if request.op == "undo":
        last = registry.last_change()
        if last is None:
            return None if said.lower().strip() in {"undo that", "undo it", "undo"} else \
                "There's no settings change to undo."
        if time.time() - float(last.get("ts", 0)) > UNDO_FRESH_S and "prefer" not in said.lower() \
                and "setting" not in said.lower():
            return None
        change = await asyncio.to_thread(registry.undo, source=session_id)
        if change is None:
            return "There's no settings change to undo."
        route_log.record(intent="settings.undo", action=getattr(change.verification, "status", ""),
                         setting=change.setting.id)
        return reply_for(change)

    setting = request.setting
    assert setting is not None
    try:
        target = request.target()
    except ValueError as exc:
        return str(exc)
    current = registry.value(setting.id)
    if target == current and request.until is None:
        if request.op == "adjust":
            edge = "fastest" if setting.id == "voice.speed" and request.steps > 0 else \
                "slowest" if setting.id == "voice.speed" else "limit"
            return f"That's already the {edge} setting — {setting.say(current)}."
        verb = "are" if setting.name.endswith("s") else "is"      # "animations are", "the overlay is"
        return f"{setting.name[0].upper() + setting.name[1:]} {verb} already {setting.say(current)}."

    if registry.needs_confirmation(setting, target):
        from ..approvals import MANAGER

        def execute():
            change = registry.set_value(setting.id, target, source=session_id, until=request.until)
            return {"ok": change.verified or getattr(change.verification, "status", "") == "unconfirmed",
                    "message": reply_for(change)}

        action = MANAGER.propose("preference", f"set {setting.name} to {setting.say(target)}",
                                 {"setting": setting.id, "value": target}, execute, session=session_id)
        return action.prompt()

    change = await asyncio.to_thread(registry.set_value, setting.id, target,
                                     source=session_id, until=request.until)
    route_log.record(intent="settings.set", action=getattr(change.verification, "status", ""),
                     setting=setting.id)
    return reply_for(change)
