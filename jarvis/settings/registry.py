"""Every setting the owner can change by asking, and the one way to change it.

A setting is data: an id, what it is called, its type and allowed values, its default, which
running component owns it, whether that component reloads it live, whether a change needs a
yes first, and the words people use for it in English, Hindi and Hinglish. What it takes to
push a value into a running component, and to check that the component really took it, lives
in ``runtime.py`` — so this file can be read as the schema.

A change is: validate → persist (with the old value kept for undo) → apply → verify. A value
that was written but not confirmed by the component is reported as exactly that, never as
done; one the component contradicted is put back.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import preferences


@dataclass(frozen=True)
class Setting:
    id: str
    name: str                                   # how Jarvis says it: "animations"
    kind: str                                   # bool | float | int | enum
    default: Any                                # a value, or a zero-argument callable
    component: str                              # overlay | voice | backend | away | teach
    choices: tuple = ()                         # enum values
    lo: Optional[float] = None
    hi: Optional[float] = None
    step: Optional[float] = None                # the size of "a little more"
    unit: str = ""
    live: bool = True                           # False: the component must restart to pick it up
    restart: tuple[str, ...] = ()               # services to restart when not live
    reversible: bool = True
    confirm_to: tuple = ()                      # values that need a yes before they are applied
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    note: str = ""

    def default_value(self) -> Any:
        return self.default() if callable(self.default) else self.default

    def coerce(self, value: Any) -> Any:
        """The value as this setting's type, clamped into range; ValueError when it cannot be."""
        if self.kind == "bool":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in {"1", "true", "on", "yes", "enabled"}:
                return True
            if text in {"0", "false", "off", "no", "disabled"}:
                return False
            raise ValueError(f"{self.name} is on or off, not {value!r}")
        if self.kind == "enum":
            text = str(value).strip().lower()
            if text not in self.choices:
                raise ValueError(f"{self.name} can be {', '.join(self.choices)}, not {value!r}")
            return text
        if isinstance(value, bool):
            raise ValueError(f"{self.name} needs a number")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{self.name} needs a number, not {value!r}") from None
        if number != number:  # NaN
            raise ValueError(f"{self.name} needs a number")
        if self.lo is not None:
            number = max(self.lo, number)
        if self.hi is not None:
            number = min(self.hi, number)
        return int(round(number)) if self.kind == "int" else round(number, 2)

    def say(self, value: Any) -> str:
        if self.kind == "bool":
            return "on" if value else "off"
        if self.kind == "float" and self.unit == "x":
            return f"{value:.2f}×"
        if self.kind in {"float", "int"}:
            return f"{value:g}{(' ' + self.unit) if self.unit and self.unit != 'x' else ''}"
        return str(value)


def _follow_up_default() -> int:
    try:
        from ..config import CONFIG

        return int(round(float(CONFIG.follow_up_s)))
    except Exception:  # noqa: BLE001
        return 8


def _dictation_history_default() -> bool:
    return os.environ.get("JARVIS_DICTATION_HISTORY", "on").strip().lower() not in {"0", "off", "false", "no"}


SETTINGS: dict[str, Setting] = {s.id: s for s in (
    Setting(
        "overlay.animations", "animations", "bool", True, "overlay",
        aliases={"en": ("animation", "animations", "motion", "moving effects"),
                 "hi": ("एनिमेशन",), "hinglish": ("animation", "animations")},
        note="Off stops every CSS animation and transition in the overlay and freezes the meter."),
    Setting(
        "overlay.visible", "the overlay", "bool", True, "overlay",
        aliases={"en": ("overlay", "hud", "pill"), "hi": ("ओवरले",), "hinglish": ("overlay",)}),
    Setting(
        "overlay.intensity", "overlay brightness", "float", 1.0, "overlay", lo=0.3, hi=1.0, step=0.15,
        aliases={"en": ("overlay brightness", "overlay intensity", "hud brightness", "overlay"),
                 "hi": ("ओवरले की चमक",), "hinglish": ("overlay ki brightness",)}),
    Setting(
        "motion.reduced", "reduced motion", "bool", False, "overlay",
        aliases={"en": ("reduced motion", "reduce motion", "less motion"),
                 "hi": ("कम मोशन",), "hinglish": ("kam motion",)},
        note="Gentler than animations off: short fades stay, loops and slides go. Also the teaching overlay."),
    Setting(
        "voice.speed", "speaking speed", "float", 1.0, "voice", lo=0.8, hi=1.3, step=0.05, unit="x",
        aliases={"en": ("speak", "talk", "speaking speed", "voice speed", "speech rate"),
                 "hi": ("बोलो", "बोलिए"), "hinglish": ("bolo", "bol", "boliye", "baat karo")}),
    Setting(
        "voice.verbosity", "how much I say", "enum", "normal", "backend",
        choices=("brief", "normal", "detailed"),
        aliases={"en": ("verbosity", "wordy", "brief", "shorter answers", "longer answers", "detailed"),
                 "hi": ("छोटा जवाब",), "hinglish": ("chhota jawab", "short mein", "detail mein")}),
    Setting(
        "notifications.level", "spoken notifications", "enum", "all", "voice",
        choices=("all", "quiet", "urgent"),
        aliases={"en": ("notifications", "notification", "announcements", "announce"),
                 "hi": ("नोटिफिकेशन",), "hinglish": ("notification", "notifications")},
        note="quiet: passive readouts off, job and urgent alerts still spoken. urgent: urgent alerts only."),
    Setting(
        "voice.follow_up_s", "follow-up listening", "int", _follow_up_default, "voice",
        lo=3, hi=30, step=2, unit="seconds",
        aliases={"en": ("keep listening", "listening", "follow up", "follow-up"),
                 "hi": ("सुनते रहो",), "hinglish": ("sunte raho", "sunna")}),
    Setting(
        "away.replies", "away-mode replies", "bool", True, "away",
        confirm_to=(True,),
        aliases={"en": ("away mode replies", "away-mode replies", "away replies", "auto replies"),
                 "hi": ("अवे मोड जवाब",), "hinglish": ("away mode reply", "away reply")},
        note="Off is the emergency stop: the running away session ends and nothing more is sent. "
             "Lifting it needs a yes, and does not start away mode by itself."),
    Setting(
        "dictation.history", "dictation history", "bool", _dictation_history_default, "voice",
        aliases={"en": ("dictation history", "dictation log"),
                 "hi": ("डिक्टेशन हिस्ट्री",), "hinglish": ("dictation history",)},
        note="Off stops keeping new dictations. What is already kept expires on its own schedule."),
    Setting(
        "teach.glow", "teaching pen glow", "float", 0.6, "teach", lo=0.0, hi=1.0, step=0.2,
        aliases={"en": ("teaching pen", "pen glow", "teaching overlay glow", "pen"),
                 "hi": ("पेन की चमक",), "hinglish": ("pen ki chamak", "pen")}),
)}


def get(setting_id: str) -> Setting:
    try:
        return SETTINGS[setting_id]
    except KeyError:
        raise KeyError(f"unknown setting {setting_id!r}") from None


# --------------------------------------------------------------------------------- reading
def _effective(setting: Setting, data: dict[str, Any], now: float) -> tuple[Any, Optional[float]]:
    """(value in force, when a temporary value ends or None)."""
    temp = data.get("temporary", {}).get(setting.id)
    if isinstance(temp, dict) and float(temp.get("until", 0)) > now:
        try:
            return setting.coerce(temp.get("value")), float(temp["until"])
        except ValueError:
            pass
    stored = data.get("settings", {}).get(setting.id)
    if stored is None and setting.id == "notifications.level":
        # The older boolean switch is this setting's first form; honour a file written by it.
        return ("all" if data.get("notifications", True) else "quiet"), None
    if stored is None:
        return setting.default_value(), None
    try:
        return setting.coerce(stored), None
    except ValueError:
        return setting.default_value(), None


def value(setting_id: str, *, now: Optional[float] = None) -> Any:
    """The value in force right now, temporary overrides and their expiry included."""
    return _effective(get(setting_id), preferences.load_all(), time.time() if now is None else now)[0]


def snapshot(*, now: Optional[float] = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    data = preferences.load_all()
    return {sid: _effective(s, data, now)[0] for sid, s in SETTINGS.items()}


def describe_all(*, now: Optional[float] = None) -> list[dict[str, Any]]:
    """The schema with current values — for GET /settings and the overlay."""
    now = time.time() if now is None else now
    data = preferences.load_all()
    rows = []
    for s in SETTINGS.values():
        current, until = _effective(s, data, now)
        rows.append({
            "id": s.id, "name": s.name, "type": s.kind, "value": current, "default": s.default_value(),
            "choices": list(s.choices), "min": s.lo, "max": s.hi, "step": s.step, "unit": s.unit,
            "component": s.component, "live": s.live, "restart": list(s.restart),
            "reversible": s.reversible, "confirm_to": list(s.confirm_to), "until": until,
            "aliases": {k: list(v) for k, v in s.aliases.items()},
        })
    return rows


# --------------------------------------------------------------------------------- changing
@dataclass
class Change:
    setting: Setting
    old: Any
    new: Any
    revision: int
    until: Optional[float] = None
    verification: Any = None                   # runtime.Verification
    restored: bool = False                      # the component refused it, so it was put back
    undo_of: Optional[dict] = None

    @property
    def verified(self) -> bool:
        return getattr(self.verification, "status", "") == "verified"


def _mirror_legacy(data: dict[str, Any], setting_id: str, new: Any) -> None:
    # Processes still running older code read the boolean flag; keep it truthful.
    if setting_id == "notifications.level":
        data["notifications"] = new == "all"


def _store(setting: Setting, new: Any, *, source: str, until: Optional[float], old: Any,
           record: bool = True) -> tuple[dict[str, Any], None]:
    def change(data: dict[str, Any]) -> None:
        now = time.time()
        # Expired temporary values are dropped whenever the file is written anyway.
        data["temporary"] = {k: v for k, v in data.get("temporary", {}).items()
                             if isinstance(v, dict) and float(v.get("until", 0)) > now and k != setting.id}
        if until is not None:
            data["temporary"][setting.id] = {"value": new, "until": until}
        else:
            data.setdefault("settings", {})[setting.id] = new
        if until is None:
            # A timed value is not mirrored: when it runs out, the lasting value is what remains.
            _mirror_legacy(data, setting.id, new)
        if record:
            data.setdefault("history", []).append({
                "id": setting.id, "old": old, "new": new, "until": until,
                "ts": round(now, 3), "source": source[:20],
            })
    return preferences.update(change)


def set_value(setting_id: str, new: Any, *, source: str = "local", until: Optional[float] = None,
              verify: bool = True, runtime=None, timeout: Optional[float] = None) -> Change:
    """Persist, apply and verify one change. Raises ValueError for a value the setting refuses."""
    from . import runtime as default_runtime

    rt = runtime or default_runtime
    setting = get(setting_id)
    new = setting.coerce(new)
    old = value(setting_id)
    data, _ = _store(setting, new, source=source, until=until, old=old)
    change = Change(setting, old, new, data["revision"], until)
    rt.apply(setting, new, data["revision"])
    if not verify:
        return change
    change.verification = rt.verify(setting, new, data["revision"], timeout=timeout)
    if change.verification.status == "failed":
        # The component contradicted the change. Leave nothing half-applied: put the old value
        # back, un-record the change, and tell the component again.
        _restore(setting, old, until_was=until)
        rt.apply(setting, old, preferences.revision())
        change.restored = True
    return change


def _restore(setting: Setting, old: Any, *, until_was: Optional[float]) -> None:
    def change(data: dict[str, Any]) -> None:
        if until_was is not None:
            data.get("temporary", {}).pop(setting.id, None)
        else:
            data.setdefault("settings", {})[setting.id] = old
            _mirror_legacy(data, setting.id, old)
        history = data.get("history", [])
        if history and history[-1].get("id") == setting.id:
            history.pop()
    preferences.update(change)


def last_change() -> Optional[dict]:
    history = preferences.load_all()["history"]
    return dict(history[-1]) if history else None


def undo(*, source: str = "local", runtime=None, timeout: Optional[float] = None) -> Optional[Change]:
    """Put back the most recent change. Repeating it walks further back. None when there is none."""
    from . import runtime as default_runtime

    rt = runtime or default_runtime
    entry = last_change()
    if entry is None or entry.get("id") not in SETTINGS:
        return None
    setting = SETTINGS[entry["id"]]
    current = value(setting.id)

    def change(data: dict[str, Any]) -> None:
        history = data.get("history", [])
        if history:
            history.pop()
        if entry.get("until") is not None:
            data.get("temporary", {}).pop(setting.id, None)
        else:
            data.setdefault("settings", {})[setting.id] = entry.get("old")
            _mirror_legacy(data, setting.id, entry.get("old"))

    data, _ = preferences.update(change)
    restored = value(setting.id)
    result = Change(setting, current, restored, data["revision"], undo_of=entry)
    rt.apply(setting, restored, data["revision"])
    result.verification = rt.verify(setting, restored, data["revision"], timeout=timeout)
    return result


def needs_confirmation(setting: Setting, new: Any) -> bool:
    try:
        return setting.coerce(new) in setting.confirm_to
    except ValueError:
        return False


