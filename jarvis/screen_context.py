"""What the person is looking at right now, read from the thing itself — on any page, in any app.

"Explain this", "summarise this", "what did he just say", "ye kya hai" were answered for YouTube
and nothing else. The question is the same everywhere; only where the words come from differs:

    focused window is a browser  →  the tab in front of them
        YouTube                     the video's transcript (video_command's own path)
        a page with a <video>       that video's caption track around the current time
        any page                    what they selected, what is in view, the article text
    any other application        →  its accessibility text, and the screen read by OCR

Which tab is "in front" is decided from the focused window's title first (GNOME on Wayland does
not say which window has focus, but AT-SPI and XWayland do), then from which tab the browser
reports visible, then from which one is playing. A new automation session lands on whatever tab
it likes — found live to be a ChatGPT tab while the lecture played in another.

Nothing here writes to a page except, for a caption track that has never been shown, setting it
to "hidden" long enough for the browser to load its cues — then putting it back.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Optional

_BROWSERS = re.compile(r"(?i)\b(?:zen(?:\s+browser)?|mozilla\s+firefox|firefox|google\s+chrome|chromium|"
                       r"opera(?:\s+gx)?|brave|microsoft\s+edge|vivaldi)\s*$")
_TITLE_TAIL = re.compile(r"\s+[—–-]\s+(?:zen(?:\s+browser)?|mozilla\s+firefox|firefox|google\s+chrome|"
                         r"chromium|opera(?:\s+gx)?|brave|microsoft\s+edge|vivaldi)\s*$", re.I)
_YOUTUBE = ("youtube.com/watch", "youtube.com/shorts/", "youtube.com/live/", "youtu.be/")

MAX_TEXT = 12000


@dataclass
class ScreenContext:
    """One reading of the screen, with where it came from."""
    source: str = "none"           # youtube | page | app | none
    app: str = ""
    window: str = ""
    title: str = ""
    url: str = ""
    selection: str = ""            # what the person highlighted: the "this" in "explain this"
    in_view: str = ""              # what is visible right now
    body: str = ""                 # the whole readable text (article, document, OCR)
    video: dict = field(default_factory=dict)   # {time, duration, paused, captions: [(start, text)]}
    page: object = None            # the page handle, for YouTube and video control
    read_at: float = field(default_factory=time.time)
    note: str = ""                 # why something is missing, for diagnostics

    @property
    def has_text(self) -> bool:
        return bool(self.selection or self.in_view or self.body)


# --------------------------------------------------------------------------- the focused window

def active_window() -> tuple[str, str]:
    """(app, window title) of the focused window, as well as the desktop will say."""
    try:
        from .screen.providers import _bridge
        data = _bridge("atspi_snapshot.py", "", timeout=6)
        if data.get("ok"):
            return str(data.get("app") or ""), str(data.get("window") or "")
    except Exception:  # noqa: BLE001
        pass
    return "", ""


def is_browser(app: str, window: str) -> bool:
    return bool(_BROWSERS.search(window or "")) or bool(re.search(
        r"(?i)zen|firefox|chrom|opera|brave|edge|vivaldi", app or ""))


def tab_title_of(window: str) -> str:
    return _TITLE_TAIL.sub("", window or "").strip()


# --------------------------------------------------------------------------- the tab in front

# What each tab is doing, cheaply, to pick the one being looked at.
_TAB_STATE = ("const v=document.querySelector('video');"
              "return {url: location.href, title: document.title,"
              " visible: document.visibilityState === 'visible',"
              " playing: !!(v && !v.paused && !v.ended), at: v ? v.currentTime : 0};")


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", re.sub(r"^\(\d+\)\s*", "", text or "")).strip().lower()


def rank_tab(state: dict, window_title: str = "", prefer_video: bool = False) -> tuple:
    """Higher is more likely the tab in front. The window title says it outright when known."""
    title_match = bool(window_title) and _norm(state.get("title", "")) != "" and \
        _norm(state.get("title", "")) == _norm(tab_title_of(window_title))
    is_video = any(w in str(state.get("url", "")) for w in _YOUTUBE) or bool(state.get("playing"))
    return (title_match, bool(state.get("visible")),
            bool(prefer_video and is_video), bool(state.get("playing")), float(state.get("at") or 0) > 0)


def front_tab(window_title: str = "", prefer_video: bool = False) -> Optional[dict]:
    """The state of the tab in front, with its url, from Zen/Firefox over Marionette."""
    from .integrations import marionette
    if not marionette.reachable():
        return None
    best, best_rank = None, None
    with marionette.Connection(timeout=10) as conn:
        current = conn._send("WebDriver:GetWindowHandle")
        for handle in conn._send("WebDriver:GetWindowHandles") or []:
            try:
                conn._send("WebDriver:SwitchToWindow", {"handle": handle, "focus": False})
                state = conn.script(_TAB_STATE) or {}
            except Exception:  # noqa: BLE001 — a tab that will not answer is not the one
                continue
            rank = rank_tab(state, window_title, prefer_video)
            if best_rank is None or rank > best_rank:
                best, best_rank = state, rank
        if current:
            try:
                conn._send("WebDriver:SwitchToWindow", {"handle": current, "focus": False})
            except Exception:  # noqa: BLE001
                pass
    return best


# --------------------------------------------------------------------------- reading a page

# Selection, what is in view, the article, and any video with its captions. Password fields and
# form values are never read: innerText does not include input values, and inputs are skipped.
_PAGE = r"""
const [around] = arguments;
const w = window.wrappedJSObject || window;
const d = w.document;
const clean = s => (s || "").replace(/\s+/g, " ").trim();
const selection = clean(String(w.getSelection ? w.getSelection() : "")).slice(0, 4000);
const H = w.innerHeight, seen = new Set(), view = [];
const walker = d.createTreeWalker(d.body || d.documentElement, NodeFilter.SHOW_TEXT);
let n, total = 0;
while ((n = walker.nextNode()) && total < 6000) {
  const el = n.parentElement;
  if (!el || seen.has(el) || /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA|INPUT|OPTION)$/.test(el.tagName)) continue;
  const t = clean(n.textContent);
  if (t.length < 2) continue;
  const r = el.getBoundingClientRect();
  if (r.bottom < 0 || r.top > H || r.width === 0 || r.height === 0) continue;
  const st = w.getComputedStyle(el);
  if (st.visibility === "hidden" || st.display === "none") continue;
  seen.add(el); view.push(t); total += t.length;
}
const main = d.querySelector("article, main, [role=main]") || d.body;
const body = clean(main ? main.innerText : "").slice(0, 20000);
let video = null;
const v = Array.from(d.querySelectorAll("video")).sort((a, b) =>
  (b.getBoundingClientRect().width * b.getBoundingClientRect().height) -
  (a.getBoundingClientRect().width * a.getBoundingClientRect().height))[0];
if (v && (v.duration || 0) > 0) {
  const caps = [];
  for (const track of Array.from(v.textTracks || [])) {
    if (!/captions|subtitles/.test(track.kind)) continue;
    const was = track.mode;
    if (was === "disabled") track.mode = "hidden";           // loads the cues without showing them
    for (let i = 0; i < 20 && (!track.cues || !track.cues.length); i++) await new Promise(r => setTimeout(r, 100));
    for (const c of Array.from(track.cues || [])) {
      if (c.endTime >= v.currentTime - around && c.startTime <= v.currentTime + 5)
        caps.push([c.startTime, clean(c.text).replace(/<[^>]+>/g, "")]);
    }
    if (was === "disabled") track.mode = was;
    if (caps.length) break;
  }
  video = {time: v.currentTime, duration: v.duration, paused: v.paused, captions: caps.slice(-400)};
}
return {url: location.href, title: d.title, selection, in_view: view.join("\n").slice(0, 6000), body, video};
"""


async def read_page(page, around_s: float = 60.0) -> dict:
    got = await page.run(_PAGE, [around_s], timeout=15)
    return got if isinstance(got, dict) else {}


# --------------------------------------------------------------------------- reading an app

def _ocr_lines(words) -> str:
    rows: list[list] = []
    for w in sorted((w for w in words if w.text and w.confidence > 0.5), key=lambda w: (w.top, w.left)):
        if rows and abs(rows[-1][0].top - w.top) < 12:
            rows[-1].append(w)
        else:
            rows.append([w])
    return "\n".join(" ".join(w.text for w in sorted(r, key=lambda w: w.left)) for r in rows)


def read_app(app_hint: str = "") -> tuple[str, str]:
    """(accessibility text, OCR text) of the focused application. Never a password field."""
    atspi = ""
    try:
        from .screen.providers import _bridge
        data = _bridge("atspi_snapshot.py", app_hint, timeout=10)
        parts = []
        for node in data.get("nodes", []) if data.get("ok") else []:
            if node.get("secret") or node.get("role") in {"password text", "password"}:
                continue
            for key in ("name", "value"):
                text = str(node.get(key) or "").strip()
                if len(text) > 1 and text not in parts:
                    parts.append(text)
        atspi = "\n".join(parts)[:MAX_TEXT]
    except Exception:  # noqa: BLE001
        pass
    ocr_text = ""
    try:
        from .vision import ocr
        if ocr.available():
            ocr.forget()                      # a cached reading is of a screen that has changed
            ocr_text = _ocr_lines(ocr.read())[:MAX_TEXT]
    except Exception:  # noqa: BLE001
        pass
    return atspi, ocr_text


# --------------------------------------------------------------------------- one reading

async def locate(prefer_video: bool = False) -> ScreenContext:
    """Where to read: the browser tab in front (``page`` set), or the focused app (``source``
    "app", text filled in). ``source`` "none" with no page means the browser could not be
    driven; the caller decides what to try next."""
    app, window = await asyncio.to_thread(active_window)
    ctx = ScreenContext(app=app, window=window)
    if is_browser(app, window) or not window:
        try:
            tab = await asyncio.to_thread(front_tab, window, prefer_video)
        except Exception as exc:  # noqa: BLE001
            tab, ctx.note = None, f"browser: {type(exc).__name__}"
        if tab:
            from .screen.page import MarionettePage
            ctx.url, ctx.title = str(tab.get("url") or ""), str(tab.get("title") or "")
            ctx.page = MarionettePage(url=ctx.url)
            ctx.source = "youtube" if any(w in ctx.url for w in _YOUTUBE) else "page"
        return ctx
    await fill_from_app(ctx)
    return ctx


async def fill_from_app(ctx: ScreenContext) -> ScreenContext:
    atspi, ocr_text = await asyncio.to_thread(read_app, "")
    if atspi or ocr_text:
        ctx.source = "app"
        ctx.title = tab_title_of(ctx.window) or ctx.window
        ctx.in_view = atspi if len(atspi) >= 200 else (ocr_text or atspi)
        ctx.body = "\n".join(t for t in (atspi, ocr_text) if t)[:MAX_TEXT]
        ctx.note = "atspi" if len(atspi) >= 200 else "ocr"
    return ctx
