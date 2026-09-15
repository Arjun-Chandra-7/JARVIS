"""Resolve a spoken app name to an installed application, and launch it.

`launch_app` used to need an exact binary name or `.desktop` id, so "open Opera GX" failed: there
is no `opera gx` on PATH, and the entry is `opera-gx.desktop`. Anything a person actually says —
"vs code", "opera gx", "whatsapp", "the terminal" — missed.

This reads the installed `.desktop` entries (the same list the app launcher shows) and matches
against every name an entry advertises: its display name, its generic name, its keywords, and its
executable. Matching is scored rather than first-hit, so "opera" does not beat "Opera GX" when
"opera gx" was asked for.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SEARCH_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/snapd/desktop/applications"),
]

# Things people say that do not appear in any .desktop Name.
ALIASES: dict[str, str] = {
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "terminal": "org.gnome.Terminal",
    "the terminal": "org.gnome.Terminal",
    "console": "org.gnome.Terminal",
    "files": "org.gnome.Nautilus",
    "file manager": "org.gnome.Nautilus",
    "explorer": "org.gnome.Nautilus",
    "browser": "opera-gx",
    "opera": "opera-gx",
    "settings": "gnome-control-center",
    "system settings": "gnome-control-center",
    "calculator": "org.gnome.Calculator",
    "text editor": "org.gnome.TextEditor",
    "camera": "snapshot",
    "music": "spotify",
}

_STRIP = re.compile(r"\b(app|application|program|the|please|now|up)\b")

# "F.R.I.E.N.D.S" is a spoken title, not a program. Left alone it normalises to the single letters
# "f r i e n d s", and the all-words rule below then matched almost anything — "open f.r.i.e.n.d.s"
# launched Easy Effects.
_DOTTED_ACRONYM = re.compile(r"^(?:[a-z]\.){2,}[a-z]?\.?$", re.IGNORECASE)


@dataclass
class App:
    entry_id: str          # "opera-gx" (the .desktop stem)
    name: str              # "Opera GX"
    exec_cmd: str = ""
    generic: str = ""
    keywords: list[str] = field(default_factory=list)
    path: Optional[Path] = None

    @property
    def binary(self) -> str:
        if not self.exec_cmd:
            return ""
        first = self.exec_cmd.split()[0]
        return Path(first).name


_cache: list[App] = []
_cache_at: float = 0.0


def _parse(path: Path) -> Optional[App]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Only the [Desktop Entry] section; actions below it have their own Name= lines.
    head = text.split("\n[Desktop Action", 1)[0]
    fields: dict[str, str] = {}
    for line in head.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ("Name", "Exec", "GenericName", "Keywords", "NoDisplay", "Type", "Hidden") \
                and key not in fields:
            fields[key] = value.strip()
    if fields.get("Type") not in (None, "Application"):
        return None
    if fields.get("NoDisplay", "").lower() == "true" or fields.get("Hidden", "").lower() == "true":
        return None
    name = fields.get("Name", "")
    if not name:
        return None
    return App(
        entry_id=path.stem,
        name=name,
        exec_cmd=fields.get("Exec", ""),
        generic=fields.get("GenericName", ""),
        keywords=[k for k in re.split(r"[;,]", fields.get("Keywords", "")) if k.strip()],
        path=path,
    )


def installed(refresh: bool = False) -> list[App]:
    """Every launchable application, cached for a minute."""
    global _cache, _cache_at
    if _cache and not refresh and (time.time() - _cache_at) < 60:
        return _cache
    seen: dict[str, App] = {}
    for directory in _SEARCH_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            app = _parse(path)
            # Earlier directories win: a user override shadows the system entry.
            if app and app.entry_id not in seen:
                seen[app.entry_id] = app
    _cache = list(seen.values())
    _cache_at = time.time()
    return _cache


def _normalise(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.removeprefix("open ").removeprefix("launch ").removeprefix("start ")
    t = _STRIP.sub(" ", t)
    return re.sub(r"[^a-z0-9+ ]", " ", t).strip()


def _score(app: App, query: str) -> int:
    """How well this entry answers `query`. Higher wins; 0 means no match."""
    name = _normalise(app.name)
    generic = _normalise(app.generic)
    entry = _normalise(app.entry_id.replace("-", " ").replace("_", " "))
    binary = _normalise(app.binary)
    keywords = [_normalise(k) for k in app.keywords]

    if query in (name, entry, binary):
        return 100
    if query == generic:
        return 90
    # "opera gx" must beat "opera": prefer the longer, more specific name that still matches.
    if name.startswith(query) or entry.startswith(query):
        return 80 - min(20, len(name) - len(query))
    if binary.startswith(query):
        return 72
    if query in keywords:
        return 70
    if query in name or query in entry:
        return 60 - min(20, len(name) - len(query))
    if any(query in k for k in keywords) or (generic and query in generic):
        return 45
    # Every meaningful word of the query appears somewhere in the name. Fragments shorter than
    # three characters are excluded: single letters match nearly every app name, which is how
    # "f r i e n d s" scored against "Easy Effects".
    words = [w for w in query.split() if len(w) >= 3]
    if words and all(w in f"{name} {entry} {generic}" for w in words):
        return 40
    return 0


def resolve(spoken: str) -> Optional[App]:
    """Best installed app for what the user said, or None."""
    if _DOTTED_ACRONYM.match((spoken or "").strip()):
        return None                      # a spelled-out title, not a program
    query = _normalise(spoken)
    if not query:
        return None
    apps = installed()

    alias = ALIASES.get(query)
    if alias:
        for app in apps:
            if app.entry_id == alias or _normalise(app.binary) == _normalise(alias):
                return app
        if shutil.which(alias):
            return App(entry_id=alias, name=spoken, exec_cmd=alias)

    ranked = sorted(((_score(a, query), a) for a in apps), key=lambda t: -t[0])
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    if shutil.which(query.replace(" ", "-")):
        name = query.replace(" ", "-")
        return App(entry_id=name, name=spoken, exec_cmd=name)
    return None


def candidates(spoken: str, limit: int = 5) -> list[str]:
    """Names worth suggesting when nothing matched well.

    Falls back to fuzzy similarity, because the request often arrives through speech recognition:
    "operaa" or "opera jeeks" should still suggest Opera GX rather than nothing at all.
    """
    query = _normalise(spoken)
    if not query:
        return []
    apps = installed()
    ranked = sorted(((_score(a, query), a) for a in apps), key=lambda t: -t[0])
    named = [a.name for s, a in ranked[:limit] if s > 0]
    if named:
        return named

    from difflib import SequenceMatcher

    scored = []
    for app in apps:
        best = max(
            SequenceMatcher(None, query, _normalise(app.name)).ratio(),
            SequenceMatcher(None, query, _normalise(app.entry_id)).ratio(),
        )
        # 0.6 offered "Files" for "friends" (0.67), and the model then acted on the
        # suggestion. Only near-identical spellings are worth proposing.
        if best >= 0.78:
            scored.append((best, app.name))
    scored.sort(reverse=True)
    return [name for _s, name in scored[:limit]]


def _clean_env() -> dict:
    drop = ("LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR", "GSETTINGS_SCHEMA_DIR")
    return {k: v for k, v in os.environ.items() if k not in drop}


def is_the_browser(app: App) -> bool:
    """True when this app is the browser precision control drives."""
    try:
        from .browser import BROWSER_EXE
    except Exception:  # noqa: BLE001
        return False
    wanted = Path(BROWSER_EXE).name.lower()
    return bool(wanted) and wanted in (app.entry_id.lower(), app.binary.lower())


def launch(app: App) -> bool:
    """Start an app the way the desktop would, so it gets its own session and icon."""
    # The browser is the exception: started the way the desktop starts it, it comes up without the
    # DevTools port, and the only way back to control is a restart that closes whatever tabs are
    # open by then. Jarvis was creating that situation itself — "open Opera GX" followed by "play
    # the latest X video on YouTube" ended in "say 'restart Opera with control'" — so when Jarvis
    # is the one starting the browser it starts it controllable, as docs/PRECISION.md promises.
    # A browser already running is left alone; restarting it is the user's decision.
    if is_the_browser(app):
        from ..config import CONFIG
        from . import browser

        if CONFIG.browser_control and not browser.is_running() and browser.launch():
            return True

    env = _clean_env()
    if app.path is not None:
        for argv in (
            ["gio", "launch", str(app.path)],
            ["gtk-launch", app.entry_id],
        ):
            if not shutil.which(argv[0]):
                continue
            try:
                subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                continue
    # Fall back to the Exec line with its field codes (%U, %f, …) removed.
    if app.exec_cmd:
        argv = [a for a in app.exec_cmd.split() if not a.startswith("%")]
        if argv and shutil.which(argv[0]):
            try:
                subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                return False
    return False


def looks_like_a_website(spoken: str) -> bool:
    """True when this belongs to the browser rather than the app launcher.

    "open netflix" routes here about as often as it routes to the browser, and the two tools
    should cooperate rather than compete: this one knows it cannot help and says which tool can.
    A spelled-out title ("F.R.I.E.N.D.S") is included — it is something to find on a site.
    """
    if _DOTTED_ACRONYM.match((spoken or "").strip()):
        return True
    query = _normalise(spoken)
    if not query:
        return False
    if re.search(r"\.(com|org|net|io|ai|co|in|tv|me)\b", spoken.lower()):
        return True
    try:
        from .browser import SITES

        return query in SITES or any(query.startswith(k) for k in SITES)
    except Exception:  # noqa: BLE001
        return False


def open_app(spoken: str) -> dict:
    """Resolve and launch. Returns {ok, name, message}."""
    app = resolve(spoken)
    if app is None:
        if looks_like_a_website(spoken):
            # Phrased as an instruction to the model, not advice to the user: given the softer
            # wording a 3B model relayed it verbatim ("you should use browser_open") instead of
            # making the call itself, costing the user a turn.
            return {"ok": False, "name": "", "website": True,
                    "message": f"WRONG TOOL. “{spoken}” is a website, not an installed "
                               f"application. Immediately call browser_open with "
                               f"site=\"{spoken}\". Do not reply to the user until you have."}
        near = candidates(spoken)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        return {"ok": False, "name": "", "message": f"No installed app matches “{spoken}”.{hint}"}
    if launch(app):
        return {"ok": True, "name": app.name, "entry": app.entry_id,
                "message": f"Opened {app.name}."}
    return {"ok": False, "name": app.name,
            "message": f"Found {app.name} but could not start it."}
