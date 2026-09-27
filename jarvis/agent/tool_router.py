"""Choose which tools to show the model for a given request.

Why
---
Jarvis registers ~76 tools and handed every one of them to the model on every turn. Measured on
this laptop against a 17-command set (see docs/upgrade/RESEARCH.md):

    qwen2.5:3b, all 76 tools   11/17 correct, 1.48 s median   <- shipped behaviour
    qwen2.5:3b, ~10 tools      15/17 correct, 0.87 s median
    qwen3.5:4b, all 76 tools    5/17 correct, 17.15 s median
    qwen3.5:4b, ~18 tools      15/17 correct, 13.73 s median

Tool count, not model size, was the dominant term: the 4B model went from 29 % to 88 % purely by
seeing fewer choices. Shortlisting makes the small fast model both more accurate *and* quicker,
because it stops re-reading thousands of tokens of schema it will not use.

How
---
Rank by semantic similarity through Ollama (already installed for vault
recall), fall back to lexical overlap when embeddings are unavailable, and always union in a small
always-on set so common actions can never be routed away. When nothing scores well the model is
also given `find_tools`, so it can ask for the rest of the catalogue instead of guessing — a
discovery path rather than a dead end.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from pathlib import Path
from typing import Iterable, Optional

CACHE_DIR = Path(
    os.environ.get("JARVIS_STATE_DIR", str(Path.home() / ".local" / "share" / "jarvis"))
)

# Always offered, whatever the request looks like. These are the things a user asks for most and
# the ones whose absence is most confusing ("why can't you just tell me the time//battery?").
ALWAYS = (
    "recall",
    "system_stats",
    "run_bash",
    "web_search",
)

# Hand-written cues for tools whose name alone does not match how people ask for them. Keeps the
# router honest without needing a bigger embedding model.
ALIASES: dict[str, str] = {
    "recall": "what do you know about me, remember, memory, my notes, past decisions, who am i",
    # --- Added after measuring which tools the router never even shortlisted. Each of these
    # describes itself in two or three words, and two or three words share no vocabulary with a
    # spoken sentence: "generate image" and "make me a picture of a samurai" have not one word
    # in common. What belongs here is how a thing is actually asked for — the alias is the only
    # place that knowledge can live, because the description has to stay short for the model
    # that reads it.
    "generate_image": "make me a picture, draw me something, create an image, a wallpaper, "
                      "an illustration, a painting, a drawing, generate art, picture of",
    "check_coding_tasks": "is claude done, has codex finished, how is the agent getting on, "
                          "is it still working, coding job status, what is the agent doing, "
                          "did the build finish yet",
    "who_is_around": "is anyone else here, who else is in the room, am i alone, "
                     "is somebody with me, anyone nearby, who is present",
    "conversation_search": "what did i say about, what did we decide, did i mention, "
                           "earlier you told me, last week we talked about, find in our chat, "
                           "what was said before",
    "whatsapp_inbox": "what did someone say, any messages from, who has messaged me, "
                      "unread whatsapp, my texts, did anyone reply, messages waiting",
    "google_calendar_create": "schedule a meeting, book a slot, put it in my calendar, "
                              "set up an appointment, add an event, pencil in, "
                              "make time on friday, block out an hour",
    "system_stats": "battery level, cpu usage, memory, temperature, disk space, how is the machine",
    "google_agenda": "calendar, schedule today, what's on today, next meeting, appointments, "
                     "do i have anything on, what am i doing tomorrow, is my morning free, "
                     "what does my day look like, anything this afternoon",
    "catch_up": "what did i miss, summarise my messages and mail, brief me, unread",
    "capture_screen": "look at my screen, what's on screen, screenshot, read this error, "
                      "observe a desktop app or game before and after acting, see the basket",
    "set_timer": "timer, countdown, remind me in n minutes, alarm for n minutes",
    "draw_picture": "draw, sketch, draw me, drawing of, whiteboard, draw it on screen, show me a drawing",
    "set_reminder": "remind me at a time, reminder, don't let me forget, at six pm",
    "media_control": "pause music, play, next track, skip song, resume playback",
    "set_volume": "volume, louder, quieter, mute level",
    "set_brightness": "screen brightness, dim the screen, brighter",
    "do_not_disturb": "silence notifications, dnd, focus mode, stop interrupting",
    "lock_screen": "lock the computer, lock screen, secure my laptop",
    "phone_mirror": "mirror my phone, show my phone, scrcpy, phone on screen",
    "open_url": "open a website, go to, browse to, visit",
    # Deliberately no application names here: "open opera gx" means launch the app, and listing
    # browser names in this alias made the router rank opening a *website* called "opera gx" first.
    "browser_open": "open a website, go to a web page, netflix youtube github hotstar, "
                    "put something on to watch, browse to a url, visit a site online",
    "browser_click": "select the profile of someone on netflix, choose a viewing profile, "
                     "click, select, choose, pick, tap, press the button on the page, "
                     "open the show, play the movie, choose the episode, click on that",
    # Pinned to LinkedIn itself: on "select the profile of Arjun" (a Netflix profile) the model
    # reached for this, because it was the only offered tool whose text contained "profile".
    "linkedin_open_profile": "my own public linkedin profile page, my linkedin url, "
                             "show my linkedin profile to someone",
    "browser_type": "search for on this site, type into the search box, look up on netflix, "
                    "find the show, enter into the field",
    "browser_read": "what is on the page, what can i click, what does the screen show, "
                    "read the page, what are my options",
    "browser_key": "press enter escape space, play pause the video, fullscreen, mute the video",
    "browser_scroll": "scroll the page down up",
    "browser_back": "go back to the previous page",
    "browser_enable_control": "restart zen with control, restart the browser with control, "
                              "restart opera with control, enable browser control, "
                              "let jarvis click in the browser",
    "open_app": "open zen, open opera gx, open vs code, open spotify, open settings, open the terminal, "
                "launch an installed application or program on this computer, start an app",
    "list_apps": "what apps do i have installed, which applications, list programs",
    # Scoped to native windows: inside a web page browser_click is exact, this one guesses from a
    # screenshot, and the model reached for it for page clicks when both were offered.
    "find_and_click": "click a button in a native desktop application window, not a web page, "
                      "click the hustle playlist in spotify or click something in vs code "
                      "or a settings window by reading the active app and screen",
    "desktop_read": "read native app controls like a dom, what is visible in spotify, "
                    "read the active desktop window and its clickable labels",
    "mouse_drag": "drag the mouse, pull and release, throw or shoot a ball towards a basket, "
                  "draw a line, move a slider in a desktop app or game",
    "find_and_drag": "visually drag a ball towards a basket, shoot a basket in a game, "
                     "find two targets and drag between them in a desktop app",
    "hold_mouse": "hold mouse button, charge a shot, press and release the mouse in a game",
    "hold_keys": "hold a keyboard key, move in a game, keep pressing space or wasd for a moment",
    "read_clipboard": "clipboard, what did i copy, copied text",
    "whatsapp_send": "message someone on whatsapp, text them, send a whatsapp",
    # Pinned to messaging. "select the profile of Arjun" (a Netflix profile) landed here because
    # Arjun is also a contact name and this was the only offered tool that looks a person up.
    "find_contact": "look up someone's phone number before messaging them, whatsapp contact "
                    "details, which number do I have for this person",
    "message_person": "tell someone, message them about, let them know, text them, drop them a line, "
                      "send a message to, whatsapp them, say to, write to",
    "web_search": "search the web, look up, find online, what is",
    "deep_research": "research thoroughly, deep dive, detailed report on",
}

_lock = threading.Lock()
_vectors: dict[str, list[float]] = {}
_vector_model = ""
_embed_ok: Optional[bool] = None


def _doc_for(schema: dict) -> str:
    """The text a tool is matched against: its name, its description, and any alias cues."""
    fn = schema.get("function", {})
    name = fn.get("name", "")
    parts = [name.replace("_", " ")]
    desc = fn.get("description")
    if desc:
        parts.append(desc)
    if name in ALIASES:
        parts.append(ALIASES[name])
    return " — ".join(parts)


def _fingerprint(schemas: list[dict], model: str) -> str:
    """Cache key over everything that changes what a tool's vector should be.

    The alias *text* has to be in here, not just its keys: editing the words a tool is matched
    against must rebuild the index, or the cache quietly serves vectors for the old wording and
    the edit appears to do nothing.
    """
    names = sorted(s.get("function", {}).get("name", "") for s in schemas)
    blob = json.dumps({"m": model, "n": names, "a": ALIASES}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _cache_path(fp: str) -> Path:
    return CACHE_DIR / f"tool-index-{fp}.json"


# --------------------------------------------------------------------------- lexical fallback
_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


_STOP = {
    "the", "and", "for", "you", "can", "get", "please", "jarvis", "what", "whats",
    "how", "now", "with", "this", "that", "from", "into", "are", "was", "his", "her",
}


def lexical_scores(schemas: list[dict], query: str) -> dict[str, float]:
    q = _words(query) - _STOP
    if not q:
        return {}
    out = {}
    for s in schemas:
        name = s.get("function", {}).get("name", "")
        doc = _words(_doc_for(s))
        if not doc:
            continue
        hits = len(q & doc)
        # Normalise by query length so a long tool description cannot win by sheer size.
        out[name] = hits / len(q)
    return out


# --------------------------------------------------------------------------- semantic
# Tools that change something. A question should not be answered by one of these: asked "what's
# on my calendar today", the model picked google_calendar_create over google_agenda, because both
# embed close to "calendar". Ranking write tools below read tools for a question removes the whole
# class of mistake, and it is the safe direction to be wrong in.
_WRITE_PREFIXES = (
    "create_", "add_", "send_", "delete_", "remove_", "write_", "set_", "start_", "stop_",
    "enable_", "disable_", "toggle_", "launch_", "open_", "place_", "message_", "trigger_",
    "join_", "process_", "control_", "remember_", "log_",
)
_WRITE_NAMES = {
    "google_calendar_create", "google_email_send", "google_tasks_add", "google_tasks_complete",
    "whatsapp_send", "type_text", "press_keys", "mouse_click", "mouse_move", "scroll_page",
    "find_and_click", "find_and_drag", "mouse_drag", "hold_mouse", "hold_keys", "lock_screen",
    "do_not_disturb", "media_control", "run_bash",
}

_QUESTION_START = (
    "what", "whats", "what's", "who", "when", "where", "which", "why", "how", "is", "are",
    "do", "does", "did", "can", "could", "any", "show", "tell", "list", "read", "check",
)
_IMPERATIVE_WRITE = (
    "create", "add", "send", "make", "schedule", "book", "set", "delete", "remove", "turn",
    "start", "stop", "open", "launch", "play", "pause", "lock", "mute", "message", "text",
    "remind", "write", "put", "mirror", "search", "look",
)


def is_write_tool(name: str) -> bool:
    return name in _WRITE_NAMES or name.startswith(_WRITE_PREFIXES)


def looks_like_a_question(query: str) -> bool:
    """True when the user is asking rather than instructing.

    'set a timer for five minutes' starts with an imperative write verb, so it is not a question
    even though 'set' also appears in _QUESTION_START-adjacent phrasing.
    """
    words = _WORD.findall(query.lower())
    if not words:
        return False
    if words[0] in _IMPERATIVE_WRITE:
        return False
    if query.strip().endswith("?"):
        return True
    return words[0] in _QUESTION_START


def _cosine(a: Iterable[float], b: Iterable[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _model_name(model: str) -> str:
    from ..memory import embedding_model

    return model or embedding_model.name()


def build_index(schemas: list[dict], model: str = "") -> bool:
    """Embed every tool once and cache to disk. Returns False when embeddings are unavailable.

    The cache key covers the tool names, the alias table and the model, so editing any of them
    rebuilds rather than silently serving stale vectors.
    """
    global _vectors, _vector_model, _embed_ok
    from ..memory import embeddings

    # Resolved before the fingerprint, which is computed from it: the cache key has to name the
    # model actually used, or switching models serves vectors from the old one.
    model = _model_name(model)
    fp = _fingerprint(schemas, model)
    path = _cache_path(fp)
    with _lock:
        if _vectors and _vector_model == model:
            return True
        if path.exists():
            try:
                blob = json.loads(path.read_text())
                if blob.get("model") == model and blob.get("vectors"):
                    _vectors = blob["vectors"]
                    _vector_model = model
                    _embed_ok = True
                    return True
            except (OSError, json.JSONDecodeError):
                pass

    if not embeddings.available():
        _embed_ok = False
        return False

    vectors: dict[str, list[float]] = {}
    for s in schemas:
        name = s.get("function", {}).get("name", "")
        if not name:
            continue
        vec = embeddings.embed(_doc_for(s), model=model)
        if vec is None:
            _embed_ok = False
            return False
        vectors[name] = vec

    with _lock:
        _vectors = vectors
        _vector_model = model
        _embed_ok = True
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"model": model, "vectors": vectors}))
    except OSError:
        pass
    return True


def semantic_scores(query: str, model: str = "") -> dict[str, float]:
    from ..memory import embeddings

    if not _vectors:
        return {}
    qvec = embeddings.embed(query, model=model)
    if qvec is None:
        return {}
    return {name: _cosine(qvec, vec) for name, vec in _vectors.items()}


# --------------------------------------------------------------------------- the router
def select(
    schemas: list[dict],
    query: str,
    *,
    keep: int = 10,
    always: Iterable[str] = ALWAYS,
    use_semantic: bool = True,
    model: str = "",
) -> list[dict]:
    """Return the tools worth showing the model for `query`, most relevant first.

    Order is deliberate: models attend more to what comes first, and the always-on tools are
    appended rather than prepended so a strong semantic match still leads.
    """
    model = _model_name(model)
    if keep <= 0 or keep >= len(schemas):
        return schemas

    scores: dict[str, float] = {}
    if use_semantic and _vectors:
        scores = semantic_scores(query, model=model)
    if not scores:
        scores = lexical_scores(schemas, query)

    if looks_like_a_question(query):
        scores = {
            name: (score * 0.55 if is_write_tool(name) else score)
            for name, score in scores.items()
        }

    by_name = {s.get("function", {}).get("name", ""): s for s in schemas}
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])

    chosen: list[str] = []
    for name, _score in ranked:
        if name in by_name and name not in chosen:
            chosen.append(name)
        if len(chosen) >= keep:
            break

    for name in always:
        if name in by_name and name not in chosen:
            chosen.append(name)

    return [by_name[n] for n in chosen if n in by_name]


def catalogue(schemas: list[dict]) -> str:
    """One line per tool — what `find_tools` hands back when the shortlist missed."""
    lines = []
    for s in sorted(schemas, key=lambda s: s.get("function", {}).get("name", "")):
        fn = s.get("function", {})
        name = fn.get("name", "")
        desc = (fn.get("description") or ALIASES.get(name, "")).split(".")[0][:90]
        lines.append(f"{name}: {desc}" if desc else name)
    return "\n".join(lines)
