"""Milestone C: local contact/conversation intelligence + structured away debrief."""
import asyncio
from types import SimpleNamespace

import pytest

from jarvis.memory import contacts_index as ci


def cfg(tmp_path):
    return SimpleNamespace(vault_path=tmp_path, user_name="sir", brain="chatgpt")


JID = "919000000001@s.whatsapp.net"


def _msgs(*pairs, base=1_700_000_000_000):
    out = []
    for i, (frm, text) in enumerate(pairs):
        out.append({"from": JID, "name": "Maya", "text": text, "ts": base + i * 1000,
                    "fromMe": frm == "me", "id": f"m{i}"})
    return out


def test_normalize_drops_groups_newsletters_and_sorts():
    raw = [
        {"from": "x@g.us", "text": "group", "ts": 5},
        {"from": "y@newsletter", "text": "news", "ts": 6},
        {"from": JID, "name": "Maya", "text": "later", "ts": 30},
        {"from": JID, "name": "Maya", "text": "", "ts": 20},
        {"from": JID, "name": "Maya", "text": "first", "ts": 10},
    ]
    norm = ci.normalize(raw)
    assert [m["text"] for m in norm] == ["first", "later"]


def test_ingest_is_incremental_and_caps_recent(tmp_path):
    config = cfg(tmp_path)
    first = ci.ingest(config, fetch=lambda n: _msgs(("them", "hi"), ("me", "hey"), ("them", "project friday?")))
    assert first["new"] == 3 and first["contacts_touched"] == 1
    again = ci.ingest(config, fetch=lambda n: _msgs(("them", "hi"), ("me", "hey"), ("them", "project friday?")))
    assert again["new"] == 0                       # nothing new -> cursor held
    more = ci.ingest(config, fetch=lambda n: _msgs(
        ("them", "hi"), ("me", "hey"), ("them", "project friday?"), ("them", "ping")))
    assert more["new"] == 1
    rec = ci._load(config)[JID]
    assert rec["msg_count"] == 4 and rec["incoming_count"] == 3
    assert len(rec["recent"]) <= ci._RECENT_CAP


def test_rollup_heuristic_extracts_subjects_and_commitments(tmp_path):
    config = cfg(tmp_path)
    ci.ingest(config, fetch=lambda n: _msgs(
        ("them", "can you send the physics project"),
        ("me", "sure"),
        ("them", "need the physics notes by friday deadline"),
        ("them", "also the physics lab report?"),
    ))
    s = ci.roll_up(config, JID)
    assert "physics" in s["recurring_subjects"]
    assert any("friday" in c.lower() or "deadline" in c.lower() for c in s["commitments"])
    assert s["confidence"] == "low" and "heuristic over" in s["provenance"]


def test_rollup_uses_injected_llm(tmp_path):
    config = cfg(tmp_path)
    ci.ingest(config, fetch=lambda n: _msgs(("them", "hi"), ("them", "about the trip")))
    s = ci.roll_up(config, JID, llm=lambda prompt: "Maya asked about the weekend trip.")
    assert s["summary"] == "Maya asked about the weekend trip."
    assert s["confidence"] == "medium" and s["provenance"].startswith("llm over")


def test_recall_is_compact_and_carries_provenance(tmp_path):
    config = cfg(tmp_path)
    ci.ingest(config, fetch=lambda n: _msgs(
        ("them", "sending the report"), ("me", "ok"), ("them", "call me tomorrow at 5pm")))
    text = ci.recall(config, "Maya")
    assert "Maya" in text and "derived:" in text and "confidence" in text
    assert "919000000001@s.whatsapp.net" not in text        # never leak the raw jid line


def test_search_requires_authorization(tmp_path):
    config = cfg(tmp_path)
    ci.ingest(config, fetch=lambda n: _msgs(("them", "the venue is Koramangala"), ("me", "noted")))
    assert ci.search(config, "venue", authorized=False) == []
    hits = ci.search(config, "venue koramangala", authorized=True)
    assert hits and "Koramangala" in hits[0]["text"]


def test_note_reply_records_both_sides(tmp_path):
    config = cfg(tmp_path)
    ci.note_reply(config, JID, "Maya", "you around?", "He's away, I'm his assistant.")
    rec = ci._load(config)[JID]
    assert rec["incoming_count"] == 1 and rec["msg_count"] == 2
    assert rec["recent"][-1]["from_me"] is True


def test_away_debrief_is_structured_per_person(tmp_path):
    from jarvis.agent import away, pa_daemon
    config = cfg(tmp_path)
    away.set_away("out", config)
    away.record_event(config, {"id": "1", "type": "whatsapp", "sender": "Maya", "jid": JID,
                               "text": "can you send me the project?", "reply": "Which project?", "status": "sent"})
    away.record_event(config, {"id": "2", "type": "whatsapp", "sender": "Maya", "jid": JID,
                               "text": "the physics one, need it by friday", "reply": "", "status": "not_sent"})
    away.record_event(config, {"id": "3", "type": "call", "sender": "Dad", "jid": "999", "text": "Incoming call"})
    brief = pa_daemon.debrief(config)
    assert "• Maya (2 msgs)" in brief
    assert "wanted: can you send me the project?" in brief
    assert "Jarvis replied (sent): Which project?" in brief
    assert "→ needs you:" in brief
    assert "Call from Dad" in brief
