"""Where ScreenElements come from: AT-SPI, the browser DOM, and OCR.

Each provider turns its source into ScreenElements and acts through that same source, and each
can read one element again so the model can verify an action by observation.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from .model import SOURCE_CONFIDENCE, ScreenElement

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


# --------------------------------------------------------------------------- AT-SPI

def _atspi_env() -> dict:
    from ..integrations.accessibility import _bridge_env
    env = _bridge_env()
    # A shell that outlived an earlier session carries a dead bus address; the bridge then finds
    # nothing and says the screen is empty. Without it, AT-SPI asks the session bus for the live one.
    address = env.get("AT_SPI_BUS_ADDRESS", "")
    if address.startswith("unix:path="):
        path = address.split("=", 1)[1].split(",", 1)[0]
        if not os.path.exists(path):
            env.pop("AT_SPI_BUS_ADDRESS", None)
    return env


def _bridge(script: str, arg: str, timeout: float = 20.0) -> dict:
    try:
        done = subprocess.run(["/usr/bin/python3", str(_SCRIPTS / script), arg], capture_output=True,
                              text=True, timeout=timeout, env=_atspi_env())
        data = json.loads(done.stdout or "{}")
        return data if isinstance(data, dict) else {"ok": False, "reason": "no reply"}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"accessibility bridge failed ({type(exc).__name__})"}


def element_from_atspi(node: dict, app: str, window: str) -> ScreenElement:
    path = tuple(node.get("path") or ())
    states = set(node.get("states") or ())
    if node.get("secret"):
        states.add("secret")
    actions = list(node.get("actions") or ())
    if "focusable" in states:
        actions.append("focus")
    if "editable" in states:
        actions.append("set_text")
    return ScreenElement(
        id="atspi:" + ".".join(map(str, path)), role=node.get("role", ""), name=node.get("name", ""),
        value="" if node.get("secret") else node.get("value", ""), app=app, window=window,
        bounds=tuple(node.get("rect") or (0, 0, 0, 0)), states=frozenset(states), actions=tuple(actions),
        parent="atspi:" + ".".join(map(str, path[:-1])) if path else None, source="atspi",
        confidence=SOURCE_CONFIDENCE["atspi"], locator={"path": list(path), "app": app})


class AtspiProvider:
    source = "atspi"

    def __init__(self, app_hint: str = "", run=_bridge) -> None:
        self.app_hint = app_hint
        self._run = run

    async def elements(self) -> list[ScreenElement]:
        data = await asyncio.to_thread(self._run, "atspi_snapshot.py", self.app_hint)
        if not data.get("ok"):
            return []
        return [element_from_atspi(n, data.get("app", ""), data.get("window", "")) for n in data.get("nodes", [])]

    async def act(self, element: ScreenElement, action: str, text: str = "") -> dict:
        mapped = {"invoke": "", "focus": "focus", "set_text": "set_text", "select": ""}.get(action)
        if mapped is None:
            return {"ok": False, "reason": f"Native controls can't {action.replace('_', ' ')} through accessibility."}
        payload = json.dumps({"app": element.locator.get("app") or self.app_hint,
                              "path": element.locator["path"], "expect": element.name,
                              "action": mapped, "text": text})
        return await asyncio.to_thread(self._run, "atspi_activate.py", payload)

    async def refresh(self, element: ScreenElement) -> Optional[ScreenElement]:
        for fresh in await self.elements():
            if fresh.id == element.id:
                return fresh
        return None


# --------------------------------------------------------------------------- browser DOM

# Walks the page for things a person would name, tagging each with a ref so it can be found
# again. Password inputs are listed (so "that's a password field" can be said) but never read.
_DOM_ELEMENTS = r"""
const limit = arguments[0] || 400;
const ROLE = {A: "link", BUTTON: "button", INPUT: "entry", TEXTAREA: "entry", SELECT: "combobox",
              VIDEO: "video", H1: "heading", H2: "heading", H3: "heading", IMG: "image"};
const pick = 'a[href],button,input,textarea,select,[role],[tabindex],video,h1,h2,h3,[contenteditable="true"]';
let next = Number(document.documentElement.dataset.jarvisNext || 1);
const out = [];
for (const el of document.querySelectorAll(pick)) {
  if (out.length >= limit) break;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) continue;
  const style = getComputedStyle(el);
  if (style.visibility === "hidden" || style.display === "none") continue;
  if (!el.dataset.jarvisRef) { el.dataset.jarvisRef = String(next++); }
  const type = (el.getAttribute("type") || "").toLowerCase();
  const secret = el.tagName === "INPUT" && type === "password";
  let role = el.getAttribute("role") || ROLE[el.tagName] || el.tagName.toLowerCase();
  if (secret) role = "password";
  const name = (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("alt") ||
                el.getAttribute("placeholder") || (el.innerText || "").trim()).replace(/\s+/g, " ").slice(0, 160);
  const editable = el.isContentEditable || ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") && !el.readOnly);
  const states = [];
  if (document.activeElement === el) states.push("focused");
  if (el.disabled || el.getAttribute("aria-disabled") === "true") states.push("disabled"); else states.push("enabled");
  if (editable) states.push("editable");
  if (el.checked || el.getAttribute("aria-checked") === "true") states.push("checked");
  if (el.getAttribute("aria-selected") === "true") states.push("selected");
  if (el.getAttribute("aria-expanded") === "true") states.push("expanded");
  if (el.getAttribute("aria-pressed") === "true") states.push("pressed");
  if (el.tagName === "VIDEO") states.push(el.paused ? "paused" : "playing");
  let value = "";
  if (!secret) {
    if ("value" in el && typeof el.value === "string") value = el.value.slice(0, 500);
    else if (el.isContentEditable) value = (el.innerText || "").slice(0, 500);
    if (el.tagName === "VIDEO") value = String(el.currentTime);
  }
  const scrollable = el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(style.overflowY);
  out.push({ref: el.dataset.jarvisRef, role, name, value, secret,
            rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
            states, editable, scrollable});
}
document.documentElement.dataset.jarvisNext = String(next);
return {url: location.href, title: document.title, elements: out};
"""

_DOM_ACT = r"""
const [ref, action, text] = arguments;
const el = document.querySelector('[data-jarvis-ref="' + CSS.escape(ref) + '"]');
if (!el) return {ok: false, reason: "That element is no longer on the page."};
if (el.tagName === "INPUT" && (el.getAttribute("type") || "").toLowerCase() === "password" && action === "set_text")
  return {ok: false, reason: "That's a password field."};
switch (action) {
  case "invoke": el.click(); return {ok: true};
  case "focus": el.focus(); return {ok: document.activeElement === el,
                                   reason: document.activeElement === el ? "" : "It would not take focus."};
  case "set_text": {
    el.focus();
    if (el.isContentEditable) { el.innerText = text; }
    else {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, text);
    }
    el.dispatchEvent(new Event("input", {bubbles: true}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
    return {ok: true};
  }
  case "select": el.click(); return {ok: true};
  case "scroll_down": el.scrollBy(0, el.clientHeight * 0.8); return {ok: true};
  case "scroll_up": el.scrollBy(0, -el.clientHeight * 0.8); return {ok: true};
  case "play": { const p = el.play && el.play(); if (p && p.catch) await p.catch(() => {}); return {ok: true}; }
  case "pause": if (el.pause) el.pause(); return {ok: true};
}
return {ok: false, reason: "Unknown action " + action};
"""


def element_from_dom(node: dict, url: str, title: str) -> ScreenElement:
    states = set(node.get("states") or ())
    if node.get("secret"):
        states.add("secret")
    actions = ["invoke", "focus"]
    if node.get("editable"):
        actions.append("set_text")
    if node.get("scrollable"):
        actions += ["scroll_down", "scroll_up"]
    if node.get("role") == "video":
        actions += ["play", "pause"]
    if node.get("scrollable"):
        states.add("scrollable")
    return ScreenElement(
        id=f"dom:{node['ref']}", role=node.get("role", ""), name=node.get("name", ""),
        value="" if node.get("secret") else node.get("value", ""), app="browser", window=title,
        bounds=tuple(node.get("rect") or (0, 0, 0, 0)), states=frozenset(states), actions=tuple(actions),
        source="dom", confidence=SOURCE_CONFIDENCE["dom"], locator={"ref": node["ref"], "url": url})


class DomProvider:
    source = "dom"

    def __init__(self, page) -> None:
        self.page = page

    async def elements(self) -> list[ScreenElement]:
        data = await self.page.run(_DOM_ELEMENTS, [400])
        if not isinstance(data, dict):
            return []
        return [element_from_dom(n, data.get("url", ""), data.get("title", "")) for n in data.get("elements", [])]

    async def act(self, element: ScreenElement, action: str, text: str = "") -> dict:
        reply = await self.page.run(_DOM_ACT, [element.locator["ref"], action, text])
        return reply if isinstance(reply, dict) else {"ok": False, "reason": "No reply from the page."}

    async def refresh(self, element: ScreenElement) -> Optional[ScreenElement]:
        for fresh in await self.elements():
            if fresh.id == element.id:
                return fresh
        return None


# --------------------------------------------------------------------------- OCR (read-only)

class OcrProvider:
    """Words on screen grouped into lines. Read-only: acting on them is a coordinate click, which
    the model labels, and which this provider only offers where input can be synthesised."""
    source = "ocr"

    def __init__(self, read=None) -> None:
        self._read = read

    async def elements(self) -> list[ScreenElement]:
        from ..vision import ocr
        words = await asyncio.to_thread(self._read or ocr.read)
        lines: dict[int, list] = {}
        for w in words:
            lines.setdefault(round(w.y / 12), []).append(w)
        out = []
        for key, ws in sorted(lines.items()):
            ws.sort(key=lambda w: w.left)
            text = " ".join(w.text for w in ws)
            left, top = min(w.left for w in ws), min(w.top for w in ws)
            right, bottom = max(w.right for w in ws), max(w.bottom for w in ws)
            conf = min(SOURCE_CONFIDENCE["ocr"], sum(w.confidence for w in ws) / len(ws) * SOURCE_CONFIDENCE["ocr"])
            out.append(ScreenElement(id=f"ocr:{key}:{left}", role="text", name=text, value=text,
                                     bounds=(left, top, right - left, bottom - top), source="ocr",
                                     confidence=conf, actions=("invoke",),
                                     locator={"x": (left + right) // 2, "y": (top + bottom) // 2}))
        return out

    async def act(self, element: ScreenElement, action: str, text: str = "") -> dict:
        if action != "invoke":
            return {"ok": False, "reason": "Text read from the screen can only be clicked."}
        from ..integrations import desktop_control
        done = await asyncio.to_thread(desktop_control.move_click, element.locator["x"], element.locator["y"])
        return {"ok": bool(done), "fallback": "coordinates",
                "reason": "" if done else "No way to click by coordinates on this desktop."}

    async def refresh(self, element: ScreenElement) -> Optional[ScreenElement]:
        from ..vision import ocr
        ocr.forget()
        for fresh in await self.elements():
            if abs(fresh.bounds[1] - element.bounds[1]) < 8 and fresh.name == element.name:
                return fresh
        return None
