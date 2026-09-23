#!/usr/bin/python3
"""Keep hold of the focused text field and read or write it on request. System Python (PyGObject).

Walking an application's tree to find the focused field took too long in a browser with a large
page, and could find the wrong one. Instead this listens for focus changes and keeps the field
that last received focus, so "where is the cursor" is answered from a live handle.

Protocol: one JSON object per line on stdin, one JSON reply per line on stdout.

    {"op": "ping"}
    {"op": "focus"}                          role, label, caret, selection — never a secret's text
    {"op": "insert", "text": ..., "replace_selection": true}
    {"op": "delete", "start": s, "end": e, "expect": "..."}   only if that text is still there
    {"op": "select", "start": s, "end": e}
    {"op": "read", "start": s, "end": e}

Every write is verified by reading the field back. Password fields, and fields labelled as OTP,
PIN, token or key, are refused before anything is read or written.
"""
from __future__ import annotations

import json
import re
import sys
import threading

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi, GLib  # noqa: E402

SECRET_ROLES = {"password text"}
SECRET = re.compile(r"(?i)password|passcode|\botp\b|one[- ]time|verification\s+code|\bpin\b|\bcvv\b|"
                    r"\bcvc\b|secret|token|api\s*key|private\s+key|passphrase|2fa|two[- ]factor")
TEXT_ROLES = {"entry", "text", "paragraph", "document text", "document web", "terminal", "password text",
              "combo box", "editbar", "section", "document frame", "rich text", "spin button"}

_current = {"acc": None, "window": None}


def _safe(call, default=None):
    try:
        return call()
    except Exception:
        return default


def _states(acc) -> set:
    names = set()
    st = _safe(acc.get_state_set)
    if st is None:
        return names
    for n in ("focused", "editable", "multi_line", "single_line", "showing", "visible", "active",
              "selectable_text"):
        kind = getattr(Atspi.StateType, n.upper(), None)
        if kind is not None and _safe(lambda k=kind: st.contains(k), False):
            names.add(n)
    return names


def _window_of(acc):
    node, hops = acc, 0
    while node is not None and hops < 60:
        role = _safe(node.get_role_name, "") or ""
        if role in {"frame", "window", "dialog"}:
            return _safe(node.get_name, "") or ""
        node, hops = _safe(node.get_parent), hops + 1
    return ""


def _is_secret(acc) -> bool:
    role = _safe(acc.get_role_name, "") or ""
    name = _safe(acc.get_name, "") or ""
    desc = _safe(acc.get_description, "") or ""
    return role in SECRET_ROLES or bool(SECRET.search(f"{name} {desc}"))


def _on_focus(event):
    if getattr(event, "detail1", 0) == 1 and event.source is not None:
        _current["acc"] = event.source


def _on_window(event):
    # GNOME on Wayland will not say which window has focus, and a browser can keep reporting
    # its window "active" after another took over. The last window activated is the truth.
    if event.source is not None:
        _current["window"] = event.source
        _current["acc"] = None


def _focused_in(win):
    stack, seen = [win], 0
    while stack and seen < 4000:
        node = stack.pop()
        seen += 1
        if "focused" in _states(node):
            return node
        for i in range(min(300, _safe(node.get_child_count, 0) or 0)):
            child = _safe(lambda i=i: node.get_child_at_index(i))
            if child is not None:
                stack.append(child)
    return None


def _find_focused():
    """No focus event seen yet: the last activated window first, then any active one."""
    if _current["window"] is not None:
        found = _focused_in(_current["window"])
        if found is not None:
            return found
    desktop = Atspi.get_desktop(0)
    for a in range(min(100, _safe(desktop.get_child_count, 0) or 0)):
        app = _safe(lambda: desktop.get_child_at_index(a))
        if app is None:
            continue
        for w in range(min(20, _safe(app.get_child_count, 0) or 0)):
            win = _safe(lambda: app.get_child_at_index(w))
            if win is None or "active" not in _states(win):
                continue
            found = _focused_in(win)
            if found is not None:
                return found
    return None


def _focused():
    acc = _current["acc"]
    if acc is None or "focused" not in _states(acc):
        acc = _find_focused()
        _current["acc"] = acc
    return acc


def _describe(acc) -> dict:
    role = _safe(acc.get_role_name, "") or ""
    states = _states(acc)
    app = _safe(lambda: acc.get_application().get_name(), "") or ""
    info = {"ok": True, "app": app, "window": _window_of(acc), "role": role,
            "name": (_safe(acc.get_name, "") or "")[:120], "states": sorted(states),
            "editable": "editable" in states, "multi_line": "multi_line" in states,
            "secret": _is_secret(acc), "has_text": False, "can_insert": False}
    if info["secret"]:
        return info                                       # nothing about its contents
    text = _safe(acc.get_text_iface)
    if text is not None:
        count = _safe(text.get_character_count, 0) or 0
        caret = _safe(text.get_caret_offset, -1)
        sel = None
        if (_safe(text.get_n_selections, 0) or 0) > 0:
            r = _safe(lambda: text.get_selection(0))
            if r is not None and r.end_offset > r.start_offset:
                sel = [r.start_offset, r.end_offset]
        info.update(has_text=True, length=count, caret=caret, selection=sel,
                    before=_safe(lambda: text.get_text(max(0, caret - 300), caret), "") if caret >= 0 else "",
                    selected=_safe(lambda: text.get_text(sel[0], sel[1]), "")[:4000] if sel else "")
    info["can_insert"] = _safe(acc.get_editable_text_iface) is not None and "editable" in states
    return info


def _insert(acc, text: str, replace_selection: bool) -> dict:
    if _is_secret(acc):
        return {"ok": False, "reason": "secret field"}
    editable = _safe(acc.get_editable_text_iface)
    tiface = _safe(acc.get_text_iface)
    if editable is None or tiface is None or "editable" not in _states(acc):
        return {"ok": False, "reason": "field does not accept accessible text insertion"}
    before = _safe(tiface.get_character_count, 0) or 0
    caret = _safe(tiface.get_caret_offset, -1)
    if caret is None or caret < 0:
        caret = before
    removed = 0
    if replace_selection and (_safe(tiface.get_n_selections, 0) or 0) > 0:
        r = _safe(lambda: tiface.get_selection(0))
        if r is not None and r.end_offset > r.start_offset:
            if not _safe(lambda: editable.delete_text(r.start_offset, r.end_offset), False):
                return {"ok": False, "reason": "could not replace the selection"}
            removed = r.end_offset - r.start_offset
            caret = r.start_offset
    ok = _safe(lambda: editable.insert_text(caret, text, len(text.encode("utf-8"))), False)
    after = _safe(tiface.get_character_count, 0) or 0
    landed = _safe(lambda: tiface.get_text(caret, caret + len(text)), "")
    exact = after == before - removed + len(text)
    # GTK 4 text views report the new length but give back "" for any range: the length growing
    # by exactly the inserted text is then the evidence, and is reported as such.
    by_length = bool(ok) and exact and landed == "" and after > 0
    verified = (bool(ok) and landed == text and exact) or by_length
    if verified:
        _safe(lambda: tiface.set_caret_offset(caret + len(text)))
    return {"ok": bool(ok), "verified": verified, "how": "length" if by_length else "read back",
            "start": caret, "end": caret + len(text), "grew": after - before + removed}


def handle(req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"ok": True}
    acc = _focused()
    if acc is None:
        return {"ok": False, "reason": "no focused field"}
    if op == "focus":
        return _describe(acc)
    if _is_secret(acc):
        return {"ok": False, "reason": "secret field"}
    tiface = _safe(acc.get_text_iface)
    if op == "insert":
        return _insert(acc, str(req.get("text", "")), bool(req.get("replace_selection", True)))
    if tiface is None:
        return {"ok": False, "reason": "field has no text"}
    s, e = int(req.get("start", 0)), int(req.get("end", 0))
    if op == "read":
        return {"ok": True, "text": _safe(lambda: tiface.get_text(s, e), "")}
    if op == "delete":
        current = _safe(lambda: tiface.get_text(s, e), None)
        if current != req.get("expect"):
            return {"ok": False, "reason": "that text is no longer there"}
        editable = _safe(acc.get_editable_text_iface)
        ok = editable is not None and _safe(lambda: editable.delete_text(s, e), False)
        return {"ok": bool(ok), "verified": bool(ok) and _safe(lambda: tiface.get_text(s, s + (e - s)), "") != current}
    if op == "select":
        ok = _safe(lambda: tiface.set_selection(0, s, e), False) or _safe(lambda: tiface.add_selection(s, e), False)
        return {"ok": bool(ok)}
    return {"ok": False, "reason": f"unknown op {op}"}


def _serve():
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        done = threading.Event()
        box = {}

        def run():
            try:
                box["r"] = handle(req)
            except Exception as exc:  # noqa: BLE001
                box["r"] = {"ok": False, "reason": type(exc).__name__}
            done.set()
            return False

        GLib.idle_add(run)
        done.wait(10)
        sys.stdout.write(json.dumps(box.get("r", {"ok": False, "reason": "timeout"}), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    loop.quit()


if __name__ == "__main__":
    Atspi.init()
    listener = Atspi.EventListener.new(_on_focus)
    listener.register("object:state-changed:focused")
    windows = Atspi.EventListener.new(_on_window)
    windows.register("window:activate")
    loop = GLib.MainLoop()
    threading.Thread(target=_serve, daemon=True).start()
    loop.run()
