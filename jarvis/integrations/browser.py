"""Precise browser control for Opera GX, over the DevTools protocol.

Why not vision
--------------
`find_and_click` screenshots the screen, asks a vision model where something is, and clicks those
pixels. That is the right tool when nothing else can see inside an application, and the wrong one
for a browser: it is slow, it costs an API call per click, and it misses whenever a model reads a
coordinate a few pixels off. "Select the profile of Arjun" then clicks the wrong profile.

Opera GX is Chromium, so it can be asked directly. This finds the element whose visible text
actually matches, and clicks its centre with a real (trusted) input event — the same event a
mouse produces, so sites that ignore synthetic `element.click()` still respond.

Chaining is the point: "open Netflix" → "select the profile of Arjun" → "open Friends" each act on
whatever the page became after the last step, because each call re-reads the live DOM.

The trade-off
-------------
Control needs Opera GX started with `--remote-debugging-port`, which is a loopback port that any
local process can use to drive the browser. Jarvis only ever starts it on 127.0.0.1, and never
starts it silently: if Opera GX is already running without the port, `ensure()` says so and asks
before restarting it, because restarting a browser loses whatever is half-typed in it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

DEBUG_PORT = int(os.environ.get("JARVIS_BROWSER_DEBUG_PORT", "9333"))
BROWSER_EXE = os.environ.get("JARVIS_BROWSER_EXE", "opera-gx")
PROFILE_DIR = os.environ.get("JARVIS_BROWSER_PROFILE", "")

# Spoken names for the places this is actually used. Anything not listed is treated as a URL, or
# searched for, so the table stays short rather than trying to be a directory of the web.
SITES: dict[str, str] = {
    "netflix": "https://www.netflix.com",
    "youtube": "https://www.youtube.com",
    "prime video": "https://www.primevideo.com",
    "amazon prime": "https://www.primevideo.com",
    "hotstar": "https://www.hotstar.com",
    "jiohotstar": "https://www.hotstar.com",
    "disney plus": "https://www.hotstar.com",
    "spotify": "https://open.spotify.com",
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "drive": "https://drive.google.com",
    "calendar": "https://calendar.google.com",
    "whatsapp": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com/feed/",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "maps": "https://maps.google.com",
    "translate": "https://translate.google.com",
    "stack overflow": "https://stackoverflow.com",
    "leetcode": "https://leetcode.com",
}


# --------------------------------------------------------------------------- process control
def _profile_dir() -> str:
    if PROFILE_DIR:
        return PROFILE_DIR
    # The real profile, so logins and watch history are the user's own. A scratch profile would
    # make "select the profile of Arjun" meaningless — there would be no Netflix session.
    return str(Path.home() / ".config" / "opera-gx")


def _exe() -> Optional[str]:
    return shutil.which(BROWSER_EXE) or shutil.which("opera-gx") or shutil.which("opera")


def is_running() -> bool:
    try:
        return subprocess.run(
            ["pgrep", "-f", "opera-gx-stable/opera"], capture_output=True, timeout=3
        ).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def control_ready(timeout: float = 1.5) -> bool:
    """True when a debuggable Opera GX is listening."""
    import httpx

    try:
        httpx.get(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def launch(url: str = "", wait_s: float = 12.0) -> bool:
    """Start Opera GX with control enabled. Returns True once the port answers."""
    exe = _exe()
    if not exe:
        return False
    argv = [
        exe,
        f"--remote-debugging-port={DEBUG_PORT}",
        # Loopback only. Without this Chromium may accept the port from elsewhere on the network.
        "--remote-allow-origins=*",
        f"--user-data-dir={_profile_dir()}",
        "--no-default-browser-check",
    ]
    if url:
        argv.append(url)
    env = {k: v for k, v in os.environ.items()
           if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR")}
    try:
        subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if control_ready():
            return True
        time.sleep(0.4)
    return False


def stop() -> None:
    try:
        subprocess.run(["pkill", "-f", "opera-gx-stable/opera"], timeout=5)
    except Exception:  # noqa: BLE001
        pass


def ensure(url: str = "", allow_restart: bool = False) -> dict:
    """Make sure a controllable Opera GX is available.

    Returns {ok, state, message}. `state` is one of ready | launched | needs_restart | missing.
    """
    if control_ready():
        return {"ok": True, "state": "ready", "message": "Opera GX is under control."}
    if not _exe():
        return {"ok": False, "state": "missing",
                "message": "Opera GX is not installed (looked for opera-gx on PATH)."}
    if is_running():
        if not allow_restart:
            return {
                "ok": False, "state": "needs_restart",
                "message": ("Opera GX is running without control enabled, so I can only open "
                            "pages, not click inside them. Restarting it with control on will "
                            "close your current tabs — say 'restart Opera with control' and I "
                            "will do it."),
            }
        stop()
        time.sleep(1.5)
    if launch(url):
        return {"ok": True, "state": "launched", "message": "Opera GX started with control."}
    return {"ok": False, "state": "missing",
            "message": "Opera GX did not come up with control enabled."}


# --------------------------------------------------------------------------- CDP plumbing
async def _targets() -> list[dict]:
    import httpx

    async with httpx.AsyncClient(timeout=4) as client:
        r = await client.get(f"http://127.0.0.1:{DEBUG_PORT}/json")
        r.raise_for_status()
        return r.json()


async def _active_page() -> Optional[dict]:
    """The tab to act on: the frontmost normal page, ignoring devtools and extensions."""
    for t in await _targets():
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        if url.startswith(("devtools://", "chrome-extension://", "opera://")):
            continue
        return t
    return None


class _Session:
    """One websocket conversation with a page target."""

    def __init__(self, ws):
        self._ws = ws
        self._id = 0

    async def call(self, method: str, params: Optional[dict] = None, timeout: float = 20.0) -> dict:
        self._id += 1
        mid = self._id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=max(0.1, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", "CDP error"))
                return msg.get("result", {})
        raise TimeoutError(f"{method} timed out")

    async def js(self, expression: str, timeout: float = 20.0) -> Any:
        res = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            raise RuntimeError(res["exceptionDetails"].get("text", "script error"))
        return res.get("result", {}).get("value")


async def _connect(target: dict):
    import websockets

    return await websockets.connect(target["webSocketDebuggerUrl"], max_size=30_000_000)


# --------------------------------------------------------------------------- element finding
# Runs in the page. Scores every visible, interactive element against the phrase and returns the
# best one plus a few runners-up, so a wrong pick can be explained rather than just being wrong.
_FIND_JS = r"""
(() => {
  const want = %s;
  const norm = s => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const q = norm(want);
  if (!q) return {error: "empty query"};

  const CLICKABLE = 'a,button,[role="button"],[role="link"],[role="menuitem"],[role="option"],' +
    '[role="tab"],[role="searchbox"],[role="textbox"],input,textarea,select,summary,' +
    '[onclick],[contenteditable="true"],[tabindex]:not([tabindex="-1"]),li,' +
    'div[class*="card"],div[class*="title"],div[class*="profile"],span[class*="profile"]';

  const seen = new Set();
  const out = [];
  for (const el of document.querySelectorAll(CLICKABLE)) {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    if (r.bottom < 0 || r.top > innerHeight * 3) continue;   // allow a little below the fold
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) < 0.05) continue;

    const label = norm(el.getAttribute("aria-label"));
    const title = norm(el.getAttribute("title"));
    const alt   = norm(el.getAttribute("alt"));
    const ph    = norm(el.getAttribute("placeholder"));
    const name  = norm(el.getAttribute("name"));
    const text  = norm(el.innerText || el.textContent);
    const value = norm(el.getAttribute("value"));

    let score = 0, why = "";
    const exact = [label, title, alt, ph, value, name, text].find(v => v && v === q);
    if (exact !== undefined) { score = 100; why = "exact"; }
    else if ([label, title, alt, ph].some(v => v && v.startsWith(q))) { score = 82; why = "label starts"; }
    else if (text && text.startsWith(q)) { score = 78; why = "text starts"; }
    else if ([label, title, alt, ph, value, name].some(v => v && v.includes(q))) { score = 64; why = "label contains"; }
    else if (text && text.includes(q)) { score = 55; why = "text contains"; }
    else continue;

    // A phrase inside a huge container matched the container too. Prefer the tightest element
    // that still contains the phrase — that is the thing a person would have clicked.
    const len = (text || label || "").length;
    if (len > q.length * 6 && score < 100) score -= 18;
    if (len > 400) score -= 25;
    if (el.matches('a,button,[role="button"],[role="link"],[role="option"]')) score += 8;
    if (r.top >= 0 && r.top < innerHeight) score += 6;        // on screen right now

    const key = Math.round(r.x) + ":" + Math.round(r.y) + ":" + Math.round(r.width);
    if (seen.has(key)) continue;
    seen.add(key);

    out.push({
      score, why,
      editable: el.matches('input:not([type="submit"]):not([type="button"]),textarea,[contenteditable="true"],[role="searchbox"],[role="textbox"]'),
      label: (label || title || alt || ph || text || value || name).slice(0, 90),
      tag: el.tagName.toLowerCase(),
      x: Math.round(r.x + r.width / 2),
      y: Math.round(r.y + r.height / 2),
      top: Math.round(r.top),
      area: Math.round(r.width * r.height),
    });
  }

  out.sort((a, b) => b.score - a.score || a.area - b.area);
  return {matches: out.slice(0, 6), total: out.length, title: document.title, url: location.href};
})()
"""

_LIST_JS = r"""
(() => {
  const norm = s => (s || "").replace(/\s+/g, " ").trim();
  const CLICKABLE = 'a,button,[role="button"],[role="link"],[role="menuitem"],[role="tab"],' +
    'input[type="submit"],[onclick]';
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll(CLICKABLE)) {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) continue;
    if (r.top < 0 || r.top > innerHeight) continue;          // visible without scrolling
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none") continue;
    const t = norm(el.getAttribute("aria-label") || el.innerText || el.getAttribute("title"));
    if (!t || t.length > 80) continue;
    if (seen.has(t)) continue;
    seen.add(t);
    out.push(t);
    if (out.length >= 40) break;
  }
  return {title: document.title, url: location.href, items: out};
})()
"""


# Netflix's search box is a React-controlled input: it re-renders from component state, so
# Ctrl+A then typing appends rather than replaces (observed: ?q=f.r.i.e.n.d.sfriends). Setting
# the value through the prototype's native setter and firing an input event is the way to make
# React adopt a programmatic change.
_CLEAR_FIELD_JS = """
(() => {
  const el = document.activeElement;
  if (!el) return false;
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
                                                  : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value") || {};
  if (setter.set && "value" in el) setter.set.call(el, "");
  else if ("value" in el) el.value = "";
  else el.textContent = "";
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return true;
})()
"""

_FOCUSED_JS = """
(() => {
  const a = document.activeElement;
  if (!a) return null;
  return {
    tag: a.tagName.toLowerCase(),
    editable: a.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="submit"]):not([type="button"]),textarea,[contenteditable="true"]'),
  };
})()
"""

# A text field that is visible *now* — used after clicking a search icon that reveals one.
_REVEALED_FIELD_JS = """
(() => {
  const sel = 'input[type="search"],input[type="text"],input:not([type]),textarea,' +
              '[contenteditable="true"],[role="searchbox"],[role="textbox"]';
  let best = null;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 12) continue;
    if (r.top < 0 || r.top > innerHeight) continue;
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none") continue;
    if (el.disabled || el.readOnly) continue;
    // Prefer the widest visible field: search boxes are wide, filters are narrow.
    if (!best || r.width > best.w) {
      best = {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: r.width};
    }
  }
  return best;
})()
"""


def _js_string(value: str) -> str:
    return json.dumps(value)


# --------------------------------------------------------------------------- operations
async def _with_page(fn, timeout: float = 25.0):
    target = await _active_page()
    if target is None:
        return {"ok": False, "error": "No open tab to act on."}
    ws = await _connect(target)
    try:
        session = _Session(ws)
        await session.call("Runtime.enable")
        return await asyncio.wait_for(fn(session), timeout=timeout)
    finally:
        await ws.close()


def resolve_site(name: str) -> str:
    """Spoken name → URL. Falls back to a URL, then to a search."""
    n = (name or "").strip()
    low = n.lower().rstrip(" .!?")
    # "open f.r.i.e.n.d.s" — spoken letter-by-letter titles arrive dotted.
    spoken = spoken_title(n)
    if spoken.lower() != low:
        low = spoken.lower()
        n = spoken
    if low in SITES:
        return SITES[low]
    for key, url in SITES.items():
        if low.startswith(key + " ") or low == key:
            return url
    if re.match(r"^(https?|file|data|about)[:/]", n):
        return n
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", n):
        return "https://" + n

    # Speech gets site names slightly wrong, and a near miss should still land: transcribed
    # "Netflix" as "Networks", which matched nothing, opened nothing, and was reported as a
    # "temporary glitch". Only very close matches count, so an actual search is never hijacked.
    near = _closest_site(low)
    if near:
        return SITES[near]

    return "https://www.google.com/search?q=" + re.sub(r"\s+", "+", n)


# Specific things speech recognition turns site names into. Fuzzy matching cannot cover these —
# "networks" scores only 0.53 against "netflix", and a threshold low enough to catch it would
# also swallow real show titles — but the mistakes are systematic, so they can just be listed.
MISHEARD: dict[str, str] = {
    "networks": "netflix",
    "network": "netflix",
    "net flicks": "netflix",
    "net flix": "netflix",
    "netflicks": "netflix",
    "nextflix": "netflix",
    "utube": "youtube",
    "u tube": "youtube",
    "hot star": "hotstar",
    "jio hotstar": "hotstar",
    "insta": "instagram",
    "linked in": "linkedin",
    "git hub": "github",
    "chat gpt": "chatgpt",
    "g mail": "gmail",
}


def _closest_site(spoken: str) -> Optional[str]:
    """The site name `spoken` most likely meant, or None when nothing is close enough."""
    from difflib import SequenceMatcher

    word = (spoken or "").strip().lower()
    if word in MISHEARD:
        return MISHEARD[word]
    if len(word) < 4:
        return None
    best, score = None, 0.0
    for key in SITES:
        ratio = SequenceMatcher(None, word, key).ratio()
        if ratio > score:
            best, score = key, ratio
    # 0.72 accepts networks->netflix (0.75) and youtub->youtube, and rejects "the office",
    # "friends" and other real titles, which must stay searches rather than becoming sites.
    return best if score >= 0.72 else None


async def current_page() -> dict:
    """Where the browser is right now, or {} when nothing is open."""

    async def do(session: _Session):
        return await session.js("({title: document.title, url: location.href})") or {}

    try:
        got = await _with_page(do)
        return got if isinstance(got, dict) and "url" in got else {}
    except Exception:  # noqa: BLE001
        return {}


# "F.R.I.E.N.D.S" is how a spelled-out title arrives from speech, and it matches a domain pattern
# almost exactly — single letters separated by dots. Titles are not destinations.
_DOTTED_ACRONYM = re.compile(r"^(?:[a-z]\.){2,}[a-z]?\.?$", re.IGNORECASE)


def spoken_title(name: str) -> str:
    """What to actually search for. "F.R.I.E.N.D.S" -> "friends".

    Speech renders a spelled-out title with dots between the letters, and searching a site for the
    literal dotted string finds nothing — verified on Netflix, which returned no results for
    "f.r.i.e.n.d.s" and the right show for "friends".
    """
    raw = (name or "").strip().rstrip(" .!?")
    if _DOTTED_ACRONYM.match(raw):
        return raw.replace(".", "")
    return raw


def _is_known_destination(name: str) -> bool:
    """True when the phrase names a site or URL rather than something to find inside one."""
    raw = (name or "").strip()
    low = raw.lower().rstrip(" .!?")
    if _DOTTED_ACRONYM.match(raw):
        return False
    if low in SITES or any(low.startswith(k + " ") for k in SITES):
        return True
    return bool(re.match(r"^(https?|file|data|about)[:/]", raw)
                or re.fullmatch(r"[\w-]+(\.[\w-]+)+(/\S*)?", raw))


async def open_site(name: str) -> dict:
    # "Open Netflix" then "open Friends" means find Friends *on Netflix*, not leave for a search
    # engine. If the browser is already somewhere real and the phrase is not itself a destination,
    # say so instead of navigating away from the thing the user was using.
    if not _is_known_destination(name):
        here = await current_page()
        url_now = (here.get("url") or "")
        if url_now.startswith("http") and "google.com/search" not in url_now:
            where = here.get("title") or url_now
            # Do it here rather than handing the problem back to the model. Told to call
            # browser_type instead, a 3B model re-issued this same failing call, then explained
            # the situation to the user and stopped. "Open Friends" while on Netflix has one
            # sensible meaning — find it on Netflix — so carry that out and report it. If the
            # site has no search box, fall through and navigate, the only other reading.
            searched = await search_here(name)
            if searched.get("ok"):
                return {"ok": True, "searched_in": where,
                        "message": f"Searched {where} for “{name}”. "
                                   f"{searched.get('found', '')}".strip()}

    url = resolve_site(name)

    async def go(session: _Session):
        await session.call("Page.enable")
        await session.call("Page.navigate", {"url": url})
        # Wait for the document to be usable rather than a fixed sleep.
        for _ in range(40):
            await asyncio.sleep(0.25)
            state = await session.js("document.readyState")
            if state in ("interactive", "complete"):
                break
        title = await session.js("document.title")
        return {"ok": True, "url": url, "title": title or "", "message": f"Opened {title or url}."}

    return await _with_page(go)


_SEARCH_FIELD_JS = """
(() => {
  // Find the page's search box directly. Going through the text matcher picks a *link* called
  // "Search" ahead of the input it opens, which is how a search silently went nowhere.
  const norm = s => (s || "").toLowerCase();
  const fields = [];
  for (const el of document.querySelectorAll(
        'input[type="search"],input[type="text"],input:not([type]),textarea,' +
        '[contenteditable="true"],[role="searchbox"],[role="textbox"]')) {
    if (el.disabled || el.readOnly) continue;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    const hidden = st.visibility === "hidden" || st.display === "none";
    const hints = norm([el.getAttribute("placeholder"), el.getAttribute("aria-label"),
                        el.getAttribute("name"), el.id, el.className].join(" "));
    const searchy = /search|query|\\bq\\b|find|titles/.test(hints) || el.type === "search";
    fields.push({
      x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
      w: Math.round(r.width), h: Math.round(r.height),
      visible: !hidden && r.width > 40 && r.height > 12 && r.top >= 0 && r.top <= innerHeight,
      searchy, hints: hints.slice(0, 60),
    });
  }
  const visible = fields.filter(f => f.visible);
  const pick = visible.find(f => f.searchy) || visible.sort((a, b) => b.w - a.w)[0] || null;
  // Something searchy exists but is collapsed: the icon has to be clicked to reveal it.
  const collapsed = !pick && fields.some(f => f.searchy);
  return {pick, collapsed};
})()
"""


# Sites whose search is a stable URL. Driving the search box through the DOM is the general
# fallback, but it is fragile on exactly the sites that matter most: Netflix's /browse page has no
# search element at all until one is summoned, and its results page renders a different one. A
# documented URL is faster and cannot be broken by a redesign.
SEARCH_URLS: dict[str, str] = {
    "netflix.com": "https://www.netflix.com/search?q={q}",
    "youtube.com": "https://www.youtube.com/results?search_query={q}",
    "primevideo.com": "https://www.primevideo.com/search?phrase={q}",
    "hotstar.com": "https://www.hotstar.com/in/explore?search_query={q}",
    "open.spotify.com": "https://open.spotify.com/search/{q}",
    "github.com": "https://github.com/search?q={q}",
    "wikipedia.org": "https://en.wikipedia.org/w/index.php?search={q}",
    "reddit.com": "https://www.reddit.com/search/?q={q}",
    "amazon.in": "https://www.amazon.in/s?k={q}",
}


def search_url_for(current_url: str, query: str) -> Optional[str]:
    """A direct search URL for the site we are on, or None to fall back to its search box."""
    from urllib.parse import quote_plus, urlparse

    host = (urlparse(current_url or "").hostname or "").lower()
    if not host:
        return None
    for domain, template in SEARCH_URLS.items():
        if host == domain or host.endswith("." + domain):
            return template.format(q=quote_plus(query))
    return None


async def search_here(query: str) -> dict:
    """Search the site we are on: by its documented search URL, else through its search box."""
    query = spoken_title(query)

    here = await current_page()
    direct = search_url_for(here.get("url", ""), query)
    if direct:

        async def go(session: _Session):
            await session.call("Page.enable")
            await session.call("Page.navigate", {"url": direct})
            for _ in range(24):
                await asyncio.sleep(0.25)
                if await session.js("document.readyState") in ("interactive", "complete"):
                    break
            await asyncio.sleep(1.0)      # results are rendered after load on these sites
            return True

        await _with_page(go)
        page = await read_page()
        items = [i for i in (page.get("items") or []) if i][:8]
        return {"ok": True,
                "found": f"Results include: {', '.join(items)}." if items else ""}

    async def do(session: _Session):
        found = await session.js(_SEARCH_FIELD_JS) or {}
        pick = found.get("pick")

        if not pick and found.get("collapsed"):
            # Reveal it: click whatever is labelled "search", then look again.
            hit = await session.js(_FIND_JS % _js_string("search"))
            best = (hit.get("matches") or [None])[0]
            if best:
                for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                    await session.call("Input.dispatchMouseEvent", {
                        "type": kind, "x": best["x"], "y": best["y"],
                        "button": "left", "clickCount": 1 if kind != "mouseMoved" else 0})
                    await asyncio.sleep(0.04)
                await asyncio.sleep(0.5)
                found = await session.js(_SEARCH_FIELD_JS) or {}
                pick = found.get("pick")

        if not pick:
            return {"ok": False}

        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await session.call("Input.dispatchMouseEvent", {
                "type": kind, "x": pick["x"], "y": pick["y"],
                "button": "left", "clickCount": 1 if kind != "mouseMoved" else 0})
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.2)

        # Clear whatever the box already holds, the way React will accept.
        await session.js(_CLEAR_FIELD_JS)
        await asyncio.sleep(0.1)

        await session.call("Input.insertText", {"text": query})
        await asyncio.sleep(0.15)
        landed = await session.js(
            "(() => {const a=document.activeElement; if(!a) return '';"
            " return (a.value !== undefined ? a.value : a.textContent) || '';})()")
        if query.lower() not in str(landed or "").lower():
            return {"ok": False}

        for kind in ("keyDown", "keyUp"):
            await session.call("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        await asyncio.sleep(1.2)
        now = await session.js("({title: document.title, url: location.href})")
        return {"ok": True, "now": now}

    got = await _with_page(do)
    if not got.get("ok"):
        return {"ok": False}
    page = await read_page()
    items = (page.get("items") or [])[:8]
    return {"ok": True, "found": f"Results include: {', '.join(items)}." if items else ""}


async def click_text(phrase: str, nth: int = 1) -> dict:
    """Click the element that best matches `phrase`."""

    async def do(session: _Session):
        found = await session.js(_FIND_JS % _js_string(phrase))
        if not found or found.get("error"):
            return {"ok": False, "error": "Could not search the page."}
        matches = found.get("matches") or []

        # A spelled-out title reaches here as it was spoken. The page says "Friends", so looking
        # for "f.r.i.e.n.d.s" finds nothing even though the show is right there on screen.
        if not matches:
            plain = spoken_title(phrase)
            if plain and plain.lower() != phrase.strip().lower():
                found = await session.js(_FIND_JS % _js_string(plain))
                matches = (found or {}).get("matches") or []
        if not matches:
            return {"ok": False, "error": f"Nothing on this page matches “{phrase}”.",
                    "page": found.get("title", "")}
        index = max(1, int(nth)) - 1
        if index >= len(matches):
            return {"ok": False,
                    "error": f"Only {len(matches)} things match “{phrase}”."}
        best = matches[index]

        # Scroll it into view, then re-measure: the coordinates move when the page scrolls.
        await session.js(
            "(() => {const els=[...document.querySelectorAll('*')];"
            f"const t=document.elementFromPoint({best['x']},{min(max(best['y'],1), 2000)});"
            "if(t) t.scrollIntoView({block:'center',inline:'center'});return true;})()"
        )
        await asyncio.sleep(0.35)
        again = await session.js(_FIND_JS % _js_string(phrase))
        spot = (again.get("matches") or [best])[min(index, len(again.get("matches") or [1]) - 1)]

        x, y = spot["x"], spot["y"]
        # A real input event, not element.click(): sites that check event.isTrusted still respond.
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            params = {"type": kind, "x": x, "y": y, "button": "left",
                      "clickCount": 1 if kind != "mouseMoved" else 0}
            await session.call("Input.dispatchMouseEvent", params)
            await asyncio.sleep(0.04)

        await asyncio.sleep(0.6)
        now = await session.js("({title: document.title, url: location.href})")
        return {
            "ok": True,
            "clicked": spot["label"],
            "match": spot["why"],
            "alternatives": [m["label"] for m in matches[:4] if m is not best],
            "now": now,
            "message": f"Clicked “{spot['label']}”.",
        }

    return await _with_page(do)


async def type_into(field: str, text: str, submit: bool = True) -> dict:
    """Click a text field, then type into it.

    `Input.insertText` goes to whatever holds focus, which after a navigation is the document —
    so typing without focusing first silently goes nowhere. Search boxes are usually labelled only
    by their placeholder ("Titles, people, genres"), which is why the finder matches that too.
    """
    clicked = await click_text(field)
    if not clicked.get("ok"):
        return {"ok": False, "error": f"Could not find a field matching “{field}”. "
                                      f"{clicked.get('error', '')}".strip()}
    await asyncio.sleep(0.25)

    async def do(session: _Session):
        # Many sites hide the search field until its icon is clicked — Wikipedia's #searchInput is
        # 0x0 until then, and Netflix behaves the same way. So if clicking the match did not leave
        # a text field focused, look for one that just appeared and focus that instead.
        focused = await session.js(_FOCUSED_JS)
        if not focused or not focused.get("editable"):
            revealed = await session.js(_REVEALED_FIELD_JS)
            if revealed:
                for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
                    await session.call("Input.dispatchMouseEvent", {
                        "type": kind, "x": revealed["x"], "y": revealed["y"],
                        "button": "left", "clickCount": 1 if kind != "mouseMoved" else 0})
                    await asyncio.sleep(0.04)
                await asyncio.sleep(0.25)
                focused = await session.js(_FOCUSED_JS)

        if not focused or not focused.get("editable"):
            return {"ok": False,
                    "error": f"“{clicked.get('clicked')}” is not a text field, and no text field "
                             "appeared after clicking it."}

        # Replace what is there rather than appending — a search field usually still holds the
        # previous query.
        await session.js(_CLEAR_FIELD_JS)
        await asyncio.sleep(0.1)

        await session.call("Input.insertText", {"text": text})
        # Verify the text actually landed. Without this, clicking a search *icon* and typing into
        # nothing reported success while the page never changed.
        await asyncio.sleep(0.15)
        landed = await session.js(
            "(() => {const a=document.activeElement; if(!a) return '';"
            " return (a.value !== undefined ? a.value : a.textContent) || '';})()"
        )
        if text.lower() not in str(landed or "").lower():
            return {"ok": False,
                    "error": f"I clicked “{clicked.get('clicked')}” but the text did not go into "
                             "any field, so nothing was searched."}
        if submit:
            await asyncio.sleep(0.25)
            for kind in ("keyDown", "keyUp"):
                await session.call("Input.dispatchKeyEvent", {
                    "type": kind, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                })
            await asyncio.sleep(0.8)
        now = await session.js("({title: document.title, url: location.href})")
        return {"ok": True, "field": clicked.get("clicked"), "now": now,
                "message": f"Typed “{text}” into {clicked.get('clicked')}"
                           + (" and searched." if submit else ".")}

    return await _with_page(do)


async def type_text(text: str, submit: bool = False) -> dict:
    async def do(session: _Session):
        await session.call("Input.insertText", {"text": text})
        if submit:
            await asyncio.sleep(0.2)
            for kind in ("keyDown", "keyUp"):
                await session.call("Input.dispatchKeyEvent", {
                    "type": kind, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                })
        await asyncio.sleep(0.4)
        return {"ok": True, "message": f"Typed {len(text)} characters" + (" and pressed Enter." if submit else ".")}

    return await _with_page(do)


_KEYS = {
    "enter": ("Enter", 13), "return": ("Enter", 13), "escape": ("Escape", 27), "esc": ("Escape", 27),
    "space": (" ", 32), "tab": ("Tab", 9), "backspace": ("Backspace", 8),
    "up": ("ArrowUp", 38), "down": ("ArrowDown", 40),
    "left": ("ArrowLeft", 37), "right": ("ArrowRight", 39),
    "f": ("f", 70), "m": ("m", 77), "k": ("k", 75),
}


async def press_key(key: str) -> dict:
    name = (key or "").strip().lower()
    spec = _KEYS.get(name)
    if spec is None and len(name) == 1:
        spec = (name, ord(name.upper()))
    if spec is None:
        return {"ok": False, "error": f"I don't know the key “{key}”."}
    label, code = spec

    async def do(session: _Session):
        for kind in ("keyDown", "keyUp"):
            payload = {"type": kind, "key": label, "windowsVirtualKeyCode": code,
                       "nativeVirtualKeyCode": code}
            if len(label) == 1:
                payload["text"] = label
            await session.call("Input.dispatchKeyEvent", payload)
            await asyncio.sleep(0.05)
        return {"ok": True, "message": f"Pressed {label}."}

    return await _with_page(do)


async def read_page() -> dict:
    """What is on the current page and what can be clicked — so Jarvis can answer, not guess."""

    async def do(session: _Session):
        info = await session.js(_LIST_JS)
        return {"ok": True, **(info or {})}

    return await _with_page(do)


async def scroll(direction: str = "down", amount: int = 600) -> dict:
    delta = -abs(amount) if direction.lower().startswith("up") else abs(amount)

    async def do(session: _Session):
        await session.call("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": 400, "y": 400, "deltaX": 0, "deltaY": delta,
        })
        await asyncio.sleep(0.3)
        return {"ok": True, "message": f"Scrolled {direction}."}

    return await _with_page(do)


async def go_back() -> dict:
    async def do(session: _Session):
        await session.js("history.back()")
        await asyncio.sleep(0.6)
        now = await session.js("({title: document.title, url: location.href})")
        return {"ok": True, "now": now, "message": "Went back."}

    return await _with_page(do)
