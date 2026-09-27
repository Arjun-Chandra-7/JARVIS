"""Voice and typed requests for 3D Studio.

Starting a model needs explicit words ("make a 3D model of this", "turn the object on my screen
into a Blender model"). Everything else — references, measurements, answers, edits, undo,
exports, status — is only claimed while a 3D project is active and was used recently, so
"undo" or "lock it" elsewhere is never taken over.
"""
from __future__ import annotations

import re
from typing import Optional

from .types import ViewKind

_START = re.compile(
    r"(?ix)(?:\b(?:make|build|create|turn|convert|reconstruct|model|generate)\b.*\b(?:3d|3-d|three[-\s]d|blender)\b"
    r"(?:\s+(?:model|version|object|mesh|copy))?"
    r"|\b(?:3d|blender)\s+model\s+(?:of|from)\b)")
_NOT_START = re.compile(r"(?i)\b(?:print(?:er)?\s+status|3d\s+printer|video|movie|glasses)\b")
_VIEW = re.compile(r"(?i)\b(?:this|that|the)?\s*(?:image|picture|drawing|screenshot|one)?\s*(?:is|as)\s+(?:the\s+)?"
                   r"(front|side|back|rear|top)\s+view")
_ANOTHER = re.compile(r"(?i)\b(?:take|capture|grab)\s+another\s+(?:reference|view|picture|capture|shot)|after\s+i\s+rotate")
_ORBIT = re.compile(r"(?i)\b(?:video|clip).*\b(?:orbit|turntable|all\s+around|360)\b|\borbit\s+reference\b")
_STATUS = re.compile(r"(?i)\b(?:how'?s|how\s+is|status\s+of|progress\s+on|where\s+are\s+we\s+with)\s+(?:the\s+|my\s+)?(?:3d\s+)?model"
                     r"|\bis\s+the\s+(?:3d\s+)?model\s+(?:ready|done|finished)|\b3d\s+(?:studio\s+)?status\b")
_CANCEL = re.compile(r"(?i)^(?:please\s+)?(?:cancel|stop|abort)\s+(?:the\s+|my\s+|this\s+)?(?:3d\s+)?(?:model|reconstruction|modell?ing)")
_PAUSE = re.compile(r"(?i)^(?:pause)\s+(?:the\s+|my\s+)?(?:3d\s+)?(?:model|reconstruction)")
_RESUME = re.compile(r"(?i)^(?:resume|continue)\s+(?:the\s+|my\s+)?(?:3d\s+)?(?:model|reconstruction)")
_DELETE_REFS = re.compile(r"(?i)\b(?:delete|remove|erase)\s+(?:the\s+|my\s+|all\s+(?:the\s+)?)?(?:reference|capture)s?\b")
_PROVIDER = re.compile(r"(?i)\b(?:use|try)\s+(?:the\s+)?(?:remote|cloud|configured)\s+(?:3d\s+)?(?:provider|generator|service|gpu)")
_BACKGROUND = re.compile(r"(?i)^(?:ignore|remove|cut\s+out)\s+the\s+background")
_ONLY = re.compile(r"(?i)\b(?:capture|use|take)\s+only\s+the\s+(?P<what>.+?)(?:,|\s+not\b|$)")
_FILE = re.compile(r"(?:(?<=\s)|^)(?P<p>(?:~|/)[^\s'\"]+\.(?:png|jpe?g|webp|bmp|tiff?|mp4|webm|mkv|mov))\b", re.I)
_WATCH = re.compile(r"(?i)\b(?:show\s+me\s+while|let\s+me\s+watch|build\s+it\s+(?:live|in\s+front\s+of\s+me))\b")
_MEASURE = re.compile(r"(?i)\b(?:is|are|it'?s|measures?)\s+(?:about\s+)?\d+(?:\.\d+)?\s*(?:mm|cm|m|millimet\w*|centimet\w*|met(?:er|re)s?|inch(?:es)?|in|ft|feet)\b")


def _studio():
    from . import studio
    return studio.get()


_HISTORY = re.compile(r"(?i)^(?:jarvis[,\s]+)?(?:undo|redo|revert)(?:\s+(?:that|it|the\s+last\s+(?:change|edit)|last\s+change))?[.!]?$"
                      r"|\b(?:go\s+back\s+to|restore|revert\s+to)\s+version\b")


def claims_history(text: str) -> bool:
    """Undo/redo/version words belong to 3D Studio only while one of its models is being edited."""
    from . import studio as studio_mod
    s = studio_mod._STUDIO
    return bool(s is not None and s.model is not None and s.recently_used(600) and _HISTORY.search((text or "").strip()))


def wants(text: str) -> bool:
    return bool(_START.search(text or "")) and not _NOT_START.search(text or "")


def _start_how(text: str) -> tuple[str, list]:
    files = [m.group("p") for m in _FILE.finditer(text)]
    if files:
        return "file", files
    if re.search(r"(?i)\bclipboard\b", text):
        return "clipboard", []
    if re.search(r"(?i)\b(?:this|the|active|current|focused)\s+window\b", text):
        return "window", []
    return "auto", []


async def handle(text: str, config=None) -> Optional[str]:
    import asyncio

    t = (text or "").strip()
    if not t:
        return None
    if wants(t):
        s = _studio()
        how, files = _start_how(t)
        s.cfg.watch = bool(_WATCH.search(t))
        view = ViewKind.UNKNOWN
        vm = _VIEW.search(t)
        if vm:
            view = ViewKind({"rear": "back"}.get(vm.group(1).lower(), vm.group(1).lower()))
        from . import calibration
        known = {}
        dim = calibration.parse_known_dimension(re.sub(r"(?i)\b3[\s-]?d\b", "", t))
        if dim and dim[2] is not None:
            known[dim[0] if dim[0] != "size" else "width"] = dim[1]
        from . import studio as studio_mod
        s.window = studio_mod.focused_window
        reply = await asyncio.to_thread(s.start, t, how=how, paths=files, view=view, known=known)
        only = _ONLY.search(t)
        if only and how == "auto":
            reply += (f" I can't pick out the {only.group('what')} by name, so I'll crop to the main object on "
                      "screen — if that's wrong, show me the area.")
        return reply

    from . import studio as studio_mod
    s = studio_mod._STUDIO
    if s is None or not (s.recently_used() or (s.job is not None and s.job.state.value == "needs_input")):
        return None

    if _STATUS.search(t):
        return s.status()
    if _CANCEL.search(t):
        return s.cancel()
    if _PAUSE.search(t):
        return s.pause()
    if _RESUME.search(t):
        return s.resume()
    if _DELETE_REFS.search(t):
        return await asyncio.to_thread(s.delete_references)
    if _BACKGROUND.search(t):
        return "I already separate the object from its background before modelling; nothing else from the screen is used."
    if _PROVIDER.search(t):
        return await asyncio.to_thread(propose_provider, s)
    vm = _VIEW.search(t)
    if vm:
        view = ViewKind({"rear": "back"}.get(vm.group(1).lower(), vm.group(1).lower()))
        files = [m.group("p") for m in _FILE.finditer(t)]
        return await asyncio.to_thread(s.add_reference, how="file" if files else "auto",
                                       path=files[0] if files else None, view=view)
    if _ORBIT.search(t):
        return ("I can't read a video that's playing inside another app. Save it as a file and tell me its path, "
                "or show me a few angles and say \"take another reference\" after each turn.")
    if _ANOTHER.search(t):
        return await asyncio.to_thread(s.add_reference, how="auto", view=ViewKind.PERSPECTIVE)
    edited = await asyncio.to_thread(s.edit, t)
    if edited is not None:
        return edited
    if _MEASURE.search(t) or re.search(r"(?i)\b(?:just\s+estimate|estimate\s+it|millimet|centimet|inches)\b", t):
        return await asyncio.to_thread(s.tell, t)
    return None


def propose_provider(s) -> str:
    """Ask before any reference leaves the machine — naming the provider and host."""
    from . import generative, resources
    from .. import approvals
    from urllib.parse import urlparse

    p, reasons = generative.choose(resources.free_vram_gb())
    if p is None:
        return ("There's no usable image-to-3D provider: " + "; ".join(reasons) +
                ". I'll keep building from the geometry I can measure.")
    ref = next((r for r in s.refs if r.path and not r.deleted), None)
    if ref is None:
        return "There's no reference to send."
    host = urlparse(p.url).hostname or p.name

    def _go():
        return s.pool.submit(s.run_generative, p, ref, host).result()

    action = approvals.MANAGER.propose("upload", f"send the reference image to {p.name} ({host}) to generate a 3D mesh",
                                       {"app": p.name, "to": host, "platform": "3d-provider"}, _go,
                                       required=(host.split(".")[0].lower(),), phrase=f"yes, send it to {host}")
    return (f"That would send your reference picture to {host}. Say \"yes, send it to {host}\" to approve, "
            "or \"cancel\".")
