"""What is on the screen, as things a person would name — and acting on them by name.

The desktop tools before this were coordinate-first: find something, compute a point, move the
pointer, click, report "clicked". On Wayland that cannot work for native windows (a client is
never told where its window is, so every AT-SPI rectangle starts at 0,0), and anywhere it can
say "done" about a click that landed on nothing.

So everything here is a ``ScreenElement`` — role, name, value, states, the actions it supports —
read from the best source available, in this order:

    1. AT-SPI       native accessibility tree (GTK, Qt, Electron, LibreOffice …)
    2. DOM          the browser page, over CDP (Chromium) or Marionette (Firefox/Zen)
    3. app APIs     media players over MPRIS/DBus and similar known adapters
    4. OCR          words and their boxes, grouped into lines — read-only, clicks by coordinate
    5. vision       a model pointing at a screenshot — last, and labelled as such

Actions go through the element's own source (an accessibility action, a DOM method), and every
action is followed by reading the element again. ``ActionResult.verified`` is true only when the
expected state was *observed*; issuing a click is not success.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

SOURCES = ("atspi", "dom", "app", "ocr", "vision")
# How far a source is trusted to have named the thing correctly.
SOURCE_CONFIDENCE = {"atspi": 0.95, "dom": 0.95, "app": 0.9, "ocr": 0.6, "vision": 0.4}

# Roles whose content is never read for context: a dictation or "what's on screen" request must
# not pick up a password.
SECRET_ROLES = {"password text", "password"}


@dataclass
class ScreenElement:
    id: str                               # stable within one snapshot: "<source>:<locator>"
    role: str
    name: str = ""
    value: str = ""
    app: str = ""
    window: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)   # x, y, w, h in screen or page pixels
    states: frozenset = frozenset()
    actions: tuple[str, ...] = ()
    parent: Optional[str] = None
    children: tuple[str, ...] = ()
    source: str = "atspi"
    confidence: float = 0.0
    locator: object = None                # whatever the source needs to find it again

    @property
    def secret(self) -> bool:
        return self.role in SECRET_ROLES or "secret" in self.states

    @property
    def focused(self) -> bool:
        return "focused" in self.states

    @property
    def editable(self) -> bool:
        return "editable" in self.states

    def label(self) -> str:
        return f"{self.name or self.value[:30] or 'unnamed'} ({self.role})"


@dataclass
class ActionResult:
    ok: bool                              # the source accepted the action
    verified: bool                        # the expected state was observed afterwards
    message: str
    element: Optional[ScreenElement] = None
    after: Optional[ScreenElement] = None
    fallback: str = ""                    # "coordinates" when a coordinate click was used

    @property
    def succeeded(self) -> bool:
        return self.ok and self.verified


class Provider(Protocol):
    """A source of elements. Async because browser pages are reached over a socket."""
    source: str

    async def elements(self) -> list[ScreenElement]: ...

    async def act(self, element: ScreenElement, action: str, text: str = "") -> dict: ...

    async def refresh(self, element: ScreenElement) -> Optional[ScreenElement]: ...


# --------------------------------------------------------------------------- environment

@dataclass
class Environment:
    session: str            # "wayland" / "x11" / "unknown"
    desktop: str
    xwayland: bool
    limitations: list[str] = field(default_factory=list)


def detect_environment(env: Optional[dict] = None) -> Environment:
    env = dict(os.environ if env is None else env)
    session = (env.get("XDG_SESSION_TYPE") or "").lower()
    if not session:
        session = "wayland" if env.get("WAYLAND_DISPLAY") else "x11" if env.get("DISPLAY") else "unknown"
    desktop = env.get("XDG_CURRENT_DESKTOP", "")
    xwayland = session == "wayland" and bool(env.get("DISPLAY"))
    limits = []
    if session == "wayland":
        limits.append("Native windows report no screen position over AT-SPI (every box starts at "
                      "0,0), so native controls are acted on through their accessibility actions, "
                      "never by coordinates.")
        limits.append("Synthetic pointer and key input needs ydotool or the RemoteDesktop portal; "
                      "xdotool only reaches XWayland windows.")
        if "GNOME" in desktop.upper():
            limits.append("GNOME does not expose which window is focused to other clients; the "
                          "active window comes from AT-SPI's ACTIVE state.")
    if not shutil.which("ydotool") and session == "wayland":
        limits.append("ydotool is not installed: coordinate fallbacks are unavailable.")
    return Environment(session, desktop, xwayland, limits)


# --------------------------------------------------------------------------- matching

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


_ROLE_WORDS = {
    "button": {"push button", "button", "toggle button", "menu button"},
    "field": {"entry", "text", "textbox", "search box", "searchbox", "combobox", "password text"},
    "box": {"entry", "text", "textbox", "searchbox", "combobox"},
    "link": {"link"},
    "tab": {"page tab", "tab"},
    "menu": {"menu item", "menuitem", "menu"},
    "checkbox": {"check box", "checkbox"},
    "slider": {"slider"},
    "video": {"video"},
}


def score(element: ScreenElement, query: str) -> float:
    """How well an element answers to a spoken description like 'the search field'."""
    q = _norm(query)
    words = [w for w in q.split() if w not in {"the", "a", "an", "on", "my", "this", "that"}]
    role_hint = {r for w in words for r in _ROLE_WORDS.get(w, set())}
    name_words = [w for w in words if w not in _ROLE_WORDS]
    name = _norm(element.name)
    if not name_words:
        base = 0.5 if role_hint else 0.0
    elif " ".join(name_words) == name:
        base = 1.0
    elif name and all(w in name.split() for w in name_words):
        base = 0.8
    elif name and " ".join(name_words) in name:
        base = 0.6
    else:
        return 0.0
    if role_hint:
        base = base + 0.1 if element.role.lower() in role_hint else base - 0.3
    return max(0.0, min(1.0, base)) * element.confidence


# --------------------------------------------------------------------------- the model

class ScreenModel:
    """Providers in priority order; the first that knows the thing answers for it."""

    def __init__(self, providers: list[Provider], env: Optional[Environment] = None) -> None:
        self.providers = providers
        self.env = env or detect_environment()
        self._by_source = {p.source: p for p in providers}

    async def snapshot(self) -> list[ScreenElement]:
        out: list[ScreenElement] = []
        for provider in self.providers:
            try:
                out.extend(await provider.elements())
            except Exception:  # noqa: BLE001 — one blind source must not blind the others
                continue
        return out

    async def focused(self) -> Optional[ScreenElement]:
        return next((e for e in await self.snapshot() if e.focused), None)

    async def find(self, query: str, *, role: str = "") -> tuple[Optional[ScreenElement], list[ScreenElement]]:
        """(the one element that clearly matches, the close candidates). Refuses a coin toss."""
        ranked = []
        for element in await self.snapshot():
            if role and element.role != role:
                continue
            s = score(element, query)
            if s > 0:
                ranked.append((s, SOURCES.index(element.source), element))
        ranked.sort(key=lambda t: (-t[0], t[1]))
        if not ranked:
            return None, []
        best = ranked[0]
        close = [e for s, _, e in ranked if best[0] - s < 0.05]
        # Two sources describing the same control is one control, not a tie.
        distinct = {(_norm(e.name), e.role) for e in close}
        if len(distinct) > 1:
            return None, close[:5]
        return best[2], close[:5]

    # ------------------------------------------------------------------ actions
    async def _act(self, element: ScreenElement, action: str, check: Callable[[Optional[ScreenElement]], bool],
             describe: str, text: str = "") -> ActionResult:
        provider = self._by_source.get(element.source)
        if provider is None:
            return ActionResult(False, False, f"Nothing can act on {element.label()}.", element)
        reply = await provider.act(element, action, text) or {}
        if not reply.get("ok"):
            return ActionResult(False, False, reply.get("reason") or f"Couldn't {describe}.", element)
        after = await provider.refresh(element)
        verified = bool(check(after))
        fallback = "coordinates" if reply.get("fallback") == "coordinates" else ""
        if verified:
            note = " (by coordinates — the control had no accessible action)" if fallback else ""
            return ActionResult(True, True, f"Done: {describe}{note}.", element, after, fallback)
        return ActionResult(True, False, f"I tried to {describe}, but I can't see that it took effect.",
                            element, after, fallback)

    async def focus(self, element: ScreenElement) -> ActionResult:
        return await self._act(element, "focus", lambda e: bool(e and e.focused), f"focus {element.label()}")

    async def invoke(self, element: ScreenElement, expect: Optional[Callable[[Optional[ScreenElement]], bool]] = None,
               ) -> ActionResult:
        """Press / click / activate. ``expect`` says what should be true afterwards; without one,
        any observable change of state or value counts, and no change is reported as unverified."""
        before = (element.states, element.value, element.name)

        def changed(after):
            return after is None or (after.states, after.value, after.name) != before
        return await self._act(element, "invoke", expect or changed, f"press {element.label()}")

    async def type(self, element: ScreenElement, text: str) -> ActionResult:
        if element.secret:
            return ActionResult(False, False, "That's a password field; I don't type into those.", element)
        return await self._act(element, "set_text", lambda e: bool(e and text in (e.value or "")),
                         f"type into {element.label()}", text=text)

    async def select(self, element: ScreenElement) -> ActionResult:
        return await self._act(element, "select", lambda e: bool(e and "selected" in e.states),
                         f"select {element.label()}")

    async def scroll(self, element: ScreenElement, direction: str = "down") -> ActionResult:
        before = element.value

        def moved(after):
            return bool(after and after.value != before)
        return await self._act(element, f"scroll_{direction}", moved, f"scroll {direction}")

    def read(self, element: ScreenElement) -> str:
        if element.secret:
            return ""
        return element.value or element.name
