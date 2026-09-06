"""Local contact & conversation intelligence.

Pipeline: raw bridge chats -> incremental ingest -> normalized messages -> per-contact
record -> rolling summary/facts -> targeted retrieval. Nothing here sends a WhatsApp
message, and full history is never handed to a prompt — only the compact profile is.

Storage (private, git-excluded): ``<vault>/Jarvis/private/contacts/index.json``.
Derived facts keep provenance so a guess is never presented as certain.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_RECENT_CAP = 40                # normalized messages kept per contact on disk
_SUMMARIZE_EVERY = 12           # new incoming messages before the rolling summary refreshes
_STOPWORDS = {
    "the", "and", "you", "your", "for", "are", "was", "with", "that", "this", "have", "has",
    "will", "can", "just", "not", "but", "get", "got", "there", "here", "what", "when", "how",
    "hey", "okay", "yeah", "yes", "no", "ok", "please", "thanks", "thank", "about", "from",
    "they", "them", "its", "it's", "i'm", "im", "were", "our", "out", "now", "let", "know",
}
_COMMIT_RE = re.compile(
    r"\b(?:tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}\s?(?:am|pm)|\d{1,2}:\d{2}|by\s+\w+day|next\s+week|deadline|due\b|send\s+me|"
    r"call\s+me|remind|meeting|pick\s+up|drop|submit)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir(config) -> Path:
    return Path(config.vault_path) / "Jarvis" / "private" / "contacts"


def _path(config) -> Path:
    return _dir(config) / "index.json"


def _load(config) -> dict[str, Any]:
    try:
        data = json.loads(_path(config).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(config, data: dict[str, Any]) -> None:
    folder = _dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder.parent, 0o700)
    except OSError:
        pass
    tmp = _path(config).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_path(config))


def normalize(raw: list[dict]) -> list[dict[str, Any]]:
    """Bridge message dicts -> {jid, name, ts, from_me, text}. Drops groups/newsletters/empties."""
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        jid = str(m.get("from") or m.get("jid") or "")
        text = str(m.get("text") or "").strip()
        if not jid or not text or m.get("isGroup") or m.get("isNewsletter"):
            continue
        if "@g.us" in jid or "@newsletter" in jid or jid == "status@broadcast":
            continue
        ts = m.get("ts") or 0
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            ts = 0
        out.append({"jid": jid, "name": str(m.get("name") or jid)[:120], "ts": ts,
                    "from_me": bool(m.get("fromMe")), "id": str(m.get("id") or f"{jid}|{ts}"),
                    "text": text[:2000]})
    out.sort(key=lambda x: x["ts"])
    return out


def _keywords(texts: list[str], k: int = 6) -> list[str]:
    words = Counter()
    for t in texts:
        for w in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", t.lower()):
            if w not in _STOPWORDS:
                words[w] += 1
    return [w for w, _ in words.most_common(k) if words[w] > 1]


def _heuristic_summary(name: str, msgs: list[dict]) -> dict[str, Any]:
    theirs = [m["text"] for m in msgs if not m["from_me"]]
    mine = [m["text"] for m in msgs if m["from_me"]]
    subjects = _keywords(theirs + mine)
    commitments = [m["text"][:160] for m in msgs if _COMMIT_RE.search(m["text"])][-4:]
    open_qs = [t[:160] for t in theirs if t.rstrip().endswith("?")][-3:]
    last = msgs[-1] if msgs else None
    line = ""
    if theirs:
        line = f"Recent topics with {name}: " + (", ".join(subjects) if subjects else "general chat") + "."
    return {
        "summary": line,
        "recurring_subjects": subjects,
        "commitments": commitments,
        "open_questions": open_qs,
        "last_text": (last["text"][:200] if last else ""),
        "last_from_me": (last["from_me"] if last else None),
        "provenance": f"heuristic over {len(msgs)} messages on {_now()}",
        "confidence": "low",
    }


def roll_up(config, jid: str, llm: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Refresh the rolling summary for one contact. ``llm`` is optional; heuristic otherwise."""
    data = _load(config)
    rec = data.get(jid)
    if not rec:
        return {}
    msgs = rec.get("recent", [])
    summary = _heuristic_summary(rec.get("name", jid), msgs)
    if llm and msgs:
        transcript = "\n".join(f"{'me' if m['from_me'] else rec.get('name', 'them')}: {m['text']}" for m in msgs[-30:])
        try:
            raw = llm(
                "Summarise this chat in 2 sentences, then list recurring subjects and any "
                "commitments/pending actions as short bullet fragments. Be factual, no speculation.\n\n"
                + transcript
            )
            if raw and raw.strip():
                summary["summary"] = raw.strip()[:900]
                summary["provenance"] = f"llm over {len(msgs[-30:])} messages on {_now()}"
                summary["confidence"] = "medium"
        except Exception:  # noqa: BLE001 - fall back to the heuristic summary
            pass
    rec["summary"] = summary
    rec["summarized_at_count"] = rec.get("incoming_count", 0)
    _save(config, data)
    return summary


def ingest(config, fetch: Callable[[int], list[dict]] | None = None, limit: int = 800,
           llm: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Incrementally fold new bridge history into the per-contact index. Safe to call often."""
    if fetch is None:
        from ..integrations.whatsapp import chats as fetch
    msgs = normalize(fetch(limit))
    data = _load(config)
    touched: set[str] = set()
    added = 0
    for m in msgs:
        jid = m["jid"]
        rec = data.setdefault(jid, {
            "jid": jid, "name": m["name"], "aliases": [], "first_seen": m["ts"],
            "last_seen": 0, "msg_count": 0, "incoming_count": 0, "cursor_ts": 0,
            "recent": [], "summary": {}, "summarized_at_count": 0,
        })
        if m["ts"] <= rec.get("cursor_ts", 0):
            continue
        rec["cursor_ts"] = m["ts"]
        rec["last_seen"] = max(rec.get("last_seen", 0), m["ts"])
        rec["msg_count"] += 1
        if not m["from_me"]:
            rec["incoming_count"] += 1
        if m["name"] and m["name"] != jid and m["name"] != rec["name"]:
            if rec["name"] in (jid, "") :
                rec["name"] = m["name"]
            elif m["name"] not in rec["aliases"]:
                rec["aliases"].append(m["name"])
        rec["recent"] = (rec["recent"] + [{k: m[k] for k in ("ts", "from_me", "text")}])[-_RECENT_CAP:]
        touched.add(jid)
        added += 1
    _save(config, data)
    rolled = []
    for jid in touched:
        rec = data.get(jid, {})
        if rec.get("incoming_count", 0) - rec.get("summarized_at_count", 0) >= _SUMMARIZE_EVERY or not rec.get("summary"):
            roll_up(config, jid, llm)
            rolled.append(jid)
    return {"messages_seen": len(msgs), "new": added, "contacts_touched": len(touched), "summarised": rolled}


def _resolve(config, name_or_jid: str) -> str | None:
    q = (name_or_jid or "").strip()
    if not q:
        return None
    data = _load(config)
    if q in data:
        return q
    low = q.lower()
    for jid, rec in data.items():
        names = [rec.get("name", "")] + list(rec.get("aliases", []))
        if any(low == n.lower() for n in names if n):
            return jid
    for jid, rec in data.items():
        names = [rec.get("name", "")] + list(rec.get("aliases", []))
        if any(low in n.lower() or n.lower() in low for n in names if n):
            return jid
    try:  # fall back to the durable vault contact store for a number/jid
        from ..integrations import contacts as vault_contacts
        hit = vault_contacts.lookup(q)
        if hit and hit.get("number"):
            digits = re.sub(r"\D", "", hit["number"])
            for jid in data:
                if digits and digits in jid:
                    return jid
    except Exception:  # noqa: BLE001
        pass
    return None


def profile(config, jid: str) -> dict[str, Any]:
    """Compact, prompt-safe profile for one contact — never the raw transcript."""
    rec = _load(config).get(jid)
    if not rec:
        return {}
    freq = ""
    span_days = max(1, (rec.get("last_seen", 0) - rec.get("first_seen", 0)) / 86_400_000)
    per_week = rec.get("msg_count", 0) / span_days * 7
    if per_week >= 20:
        freq = "very frequent"
    elif per_week >= 4:
        freq = "regular"
    elif per_week >= 1:
        freq = "occasional"
    else:
        freq = "rare"
    s = rec.get("summary", {}) or {}
    return {
        "name": rec.get("name", jid),
        "aliases": rec.get("aliases", []),
        "jid": jid,
        "messages": rec.get("msg_count", 0),
        "from_them": rec.get("incoming_count", 0),
        "frequency": freq,
        "last_interaction": (datetime.fromtimestamp(rec["last_seen"] / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
                             if rec.get("last_seen") else "unknown"),
        "recurring_subjects": s.get("recurring_subjects", []),
        "commitments": s.get("commitments", []),
        "open_questions": s.get("open_questions", []),
        "summary": s.get("summary", ""),
        "provenance": s.get("provenance", ""),
        "confidence": s.get("confidence", "low"),
    }


def recall(config, name_or_jid: str) -> str:
    """One compact block a brain/voice can use before messaging someone. Empty string if unknown."""
    jid = _resolve(config, name_or_jid)
    if not jid:
        return ""
    p = profile(config, jid)
    if not p:
        return ""
    bits = [f"{p['name']} — {p['frequency']} contact, {p['messages']} messages, last {p['last_interaction']}."]
    if p["summary"]:
        bits.append(p["summary"])
    if p["recurring_subjects"]:
        bits.append("Usually about: " + ", ".join(p["recurring_subjects"]) + ".")
    if p["commitments"]:
        bits.append("Pending/commitments: " + "; ".join(p["commitments"][:3]) + ".")
    if p["open_questions"]:
        bits.append("Recent open question: " + p["open_questions"][-1])
    bits.append(f"(derived: {p['confidence']} confidence — {p['provenance']})")
    return " ".join(bits)


def search(config, query: str, *, authorized: bool = False, limit: int = 12) -> list[dict[str, Any]]:
    """Targeted keyword search over the private normalized store. Local-owner use only."""
    if not authorized:
        return []
    terms = [t.lower() for t in re.findall(r"[a-zA-Z0-9']{2,}", query or "")][:8]
    if not terms:
        return []
    hits = []
    for jid, rec in _load(config).items():
        for m in reversed(rec.get("recent", [])):
            hay = m.get("text", "").lower()
            if all(t in hay for t in terms):
                hits.append({"name": rec.get("name", jid), "jid": jid, "from_me": m.get("from_me"),
                             "ts": m.get("ts"), "text": m.get("text", "")[:300]})
                if len(hits) >= max(1, min(int(limit), 50)):
                    return hits
    return hits


def note_reply(config, jid: str, name: str, incoming: str, reply: str) -> None:
    """Called by the away daemon so an away conversation still updates the contact record."""
    data = _load(config)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    rec = data.setdefault(jid, {
        "jid": jid, "name": name or jid, "aliases": [], "first_seen": now, "last_seen": 0,
        "msg_count": 0, "incoming_count": 0, "cursor_ts": 0, "recent": [], "summary": {},
        "summarized_at_count": 0,
    })
    for text, mine in ((incoming, False), (reply, True)):
        if not text:
            continue
        rec["recent"] = (rec["recent"] + [{"ts": now, "from_me": mine, "text": str(text)[:2000]}])[-_RECENT_CAP:]
        rec["msg_count"] += 1
        if not mine:
            rec["incoming_count"] += 1
    rec["last_seen"] = now
    rec["cursor_ts"] = max(rec.get("cursor_ts", 0), now)
    _save(config, data)
