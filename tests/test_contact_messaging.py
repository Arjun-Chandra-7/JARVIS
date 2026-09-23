"""Who "Papa" is, and what "Message Papa on WhatsApp: I'll be home by eight" does.

The address books here are shaped like the real one: a contact saved as exactly "Papa", others
that merely contain the word ("Papa Pizza Corner", "Vikram Sethi Papa Office"), local numbers
without a country code, and a WhatsApp bridge that had been labelling chats with the owner's name.
Nothing here reaches a real bridge.
"""
import asyncio
import json

import pytest

from jarvis.config import CONFIG
from jarvis.integrations import contacts, phone_contacts, whatsapp
from jarvis import message_command

PHONE_BOOK = [
    {"name": "Papa", "number": "+919810000001"},
    {"name": "Papa Pizza Corner", "number": "4045550100"},
    {"name": "Vikram Sethi Papa Office", "number": "+919810000002"},
    {"name": "Papa  Ranchi", "number": "9810000003"},
    {"name": "Mummy", "number": "+919810000004"},
    {"name": "Mumma", "number": "+919810000005"},
    {"name": "Nikhil Painter", "number": "9810000006"},
    {"name": "Nikhil Jain", "number": "9810000007"},
    {"name": "Rohit", "number": "09810000008"},
    {"name": "Kabir Dad", "number": "+919810000009"},
]


@pytest.fixture
def world(monkeypatch, tmp_path):
    saved: dict = {}
    sent: list = []
    bridge_book: list = []
    monkeypatch.setattr(CONFIG, "vault_path", tmp_path)
    monkeypatch.setattr(contacts, "_load", lambda: saved)
    monkeypatch.setattr(contacts, "_save", lambda data: None)
    monkeypatch.setattr(contacts, "_write_note", lambda entry: None)
    monkeypatch.setattr(phone_contacts, "all_contacts", lambda: PHONE_BOOK)
    monkeypatch.setattr(whatsapp, "status", lambda: True)
    monkeypatch.setattr(whatsapp, "resolve",
                        lambda name: [c for c in bridge_book if name.lower() in c["name"].lower()])

    def fake_send(to, text):
        sent.append((to, text))
        return {"ok": True, "jid": f"{to.split('@')[0]}@s.whatsapp.net", "id": "ABC"}

    monkeypatch.setattr(whatsapp, "send", fake_send)
    monkeypatch.delenv("JARVIS_DRY_RUN_SENDS", raising=False)
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "never")
    from jarvis.approvals import MANAGER
    MANAGER.clear()
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    return {"saved": saved, "sent": sent, "bridge": bridge_book, "vault": tmp_path}


# --------------------------------------------------------------------------- resolution

@pytest.mark.parametrize("said", ["Papa", "papa", "Papa on WhatsApp", "papa ko", "my papa", "Papa's",
                                  "Dad", "daddy", "father", "पापा"])
def test_papa_resolves_to_the_one_contact_saved_as_papa(world, said):
    res = contacts.resolve(said)
    assert res.ok, res.question()
    assert res.best.name == "Papa"
    assert res.best.number == "919810000001"


def test_containing_the_word_is_not_being_the_person(world):
    names = {c.name for c in contacts.resolve("Papa").candidates if c.score >= contacts.AUTO_SELECT}
    assert names == {"Papa"}


def test_a_saved_alias_beats_the_address_book(world):
    contacts.remember("Rajesh Chandra", "+919811111111", aliases=["Papa"], relationship="father")
    res = contacts.resolve("papa")
    assert res.status == "ambiguous"
    # Once the owner says who Papa is, the phone's own "Papa" entry with a different number is a
    # second person, so the answer must be a question rather than a pick.
    assert {c.name for c in res.candidates} >= {"Rajesh Chandra"}


def test_alias_on_the_same_number_is_one_person(world):
    contacts.remember("Rajesh Chandra", "+919810000001", aliases=["Papa"])
    res = contacts.resolve("papa")
    assert res.ok and res.best.number == "919810000001"


def test_fragment_never_auto_selects(world):
    contacts.remember("Hanuman", "+919812222222")
    assert contacts.lookup("man") is None
    assert not contacts.resolve("man").ok


def test_two_people_with_the_name_is_a_question(world):
    res = contacts.resolve("Nikhil")
    assert res.status == "ambiguous"
    assert "Nikhil Painter" in res.question() and "Nikhil Jain" in res.question()


def test_relation_word_prefers_the_literal_contact(world):
    assert contacts.resolve("mummy").best.name == "Mummy"
    assert contacts.resolve("mumma").best.name == "Mumma"
    # "mom" is saved as neither, and two different people answer to it — ask.
    assert contacts.resolve("mom").status == "ambiguous"


def test_someone_elses_dad_is_not_dad(world):
    assert contacts.resolve("dad").best.name == "Papa"


def test_unknown_person_is_actionable(world):
    res = contacts.resolve("Zoravar")
    assert res.status == "not_found" and "number" in res.question()


def test_owner_named_chats_do_not_become_candidates(world):
    # The bridge used to name every chat the owner wrote to after the owner. Those entries are
    # cleaned in the bridge; here the resolver must at least never auto-pick among duplicates.
    world["bridge"].extend({"jid": f"91980000{i:04d}@s.whatsapp.net", "name": "Test Owner"} for i in range(5))
    assert contacts.resolve("Test Owner", whatsapp.resolve).status == "ambiguous"


# --------------------------------------------------------------------------- numbers

@pytest.mark.parametrize("raw,want", [
    ("9810000003", "919810000003"),
    ("09810000008", "919810000008"),
    ("+91 98100 00001", "919810000001"),
    ("+1 404 555 0100", "14045550100"),
    ("0014045550100", "14045550100"),
])
def test_local_numbers_get_the_home_country_code(raw, want):
    assert contacts.dialable(raw) == want


def test_masked_number_never_reads_every_digit():
    assert contacts.mask_number("+919810000001") == "ending 0001"


# --------------------------------------------------------------------------- parsing

@pytest.mark.parametrize("said,who,msg", [
    ("Message Papa on WhatsApp: I'll be home by eight.", "Papa", "I'll be home by eight."),
    ("message papa on whatsapp I'll be home by eight", None, None),   # no separator: leave it to the brain
    ("Send a WhatsApp message to Papa saying I'll be late", "Papa", "I'll be late"),
    ("WhatsApp Rohit: open YouTube tonight?", "Rohit", "open YouTube tonight?"),
    ("tell papa on whatsapp that the train is late", "papa", "the train is late"),
    ("text Mummy, \"reached safely\"", "Mummy", "reached safely"),
    ("papa ko bol dena late aaunga", "papa", "late aaunga"),
    ("papa ko whatsapp karo ki main aath baje tak aa jaunga", "papa", "main aath baje tak aa jaunga"),
    ("tell me a joke, please", None, None),
    ("open youtube", None, None),
])
def test_recipient_and_message_are_read_apart(said, who, msg):
    got = message_command.parse(said)
    if who is None:
        assert got is None
    else:
        assert got == (who, msg)


# --------------------------------------------------------------------------- sending

def _say(text):
    from jarvis.approvals import MANAGER
    settled = asyncio.run(MANAGER.answer(text))
    if settled is not None:
        return settled.message
    return asyncio.run(message_command.handle(text, CONFIG))


def test_message_papa_end_to_end(world):
    reply = _say("Message Papa on WhatsApp: I'll be home by eight.")
    assert reply == "Sent to Papa on WhatsApp."
    assert world["sent"] == [("919810000001", "I'll be home by eight.")]


def test_audit_records_the_send_without_the_message(world):
    _say("Message Papa on WhatsApp: I'll be home by eight.")
    rows = [json.loads(l) for l in (world["vault"] / "Jarvis" / "private" / "outbox.jsonl").read_text().splitlines()]
    assert rows[-1]["status"] == "sent" and rows[-1]["to"] == "Papa"
    assert "home by eight" not in json.dumps(rows) and "9810000001" not in json.dumps(rows)


def test_failure_carries_the_real_cause(world, monkeypatch):
    monkeypatch.setattr(whatsapp, "send", lambda to, text: {"ok": False, "error": "+919810000001 is not on WhatsApp"})
    reply = _say("Message Papa on WhatsApp: hi")
    assert reply.startswith("Couldn't send to Papa") and "not on WhatsApp" in reply


def test_bridge_down_is_said_plainly(world, monkeypatch):
    monkeypatch.setattr(whatsapp, "status", lambda: False)
    assert "isn't connected" in _say("Message Papa on WhatsApp: hi")
    assert not world["sent"]


def test_unacknowledged_send_is_not_success(world, monkeypatch):
    monkeypatch.setattr(whatsapp, "send", lambda to, text: {"ok": True})
    assert "Couldn't send" in _say("Message Papa on WhatsApp: hi")


def test_ambiguous_recipient_sends_nothing(world):
    reply = _say("Message Nikhil on WhatsApp: see you at five")
    assert reply.startswith("Which Nikhil") and not world["sent"]


def test_dry_run_shows_everything_and_sends_nothing(world, monkeypatch):
    monkeypatch.setenv("JARVIS_DRY_RUN_SENDS", "1")
    res = whatsapp.smart_send("Papa", "I'll be home by eight.")
    assert res["status"] == "dry_run" and not world["sent"]
    p = res["preview"]
    assert (p["recipient"], p["platform"], p["message"]) == ("Papa", "WhatsApp", "I'll be home by eight.")
    assert "0001" in p["address"] and "9810000001" not in p["address"]


def test_new_recipient_needs_approval_then_send_it(world, monkeypatch):
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "new")
    reply = _say("Message Papa on WhatsApp: I'll be home by eight.")
    assert "to confirm" in reply.lower() and not world["sent"]
    assert _say("send it") == "Sent to Papa on WhatsApp."
    assert world["sent"] == [("919810000001", "I'll be home by eight.")]
    # Second time Papa is known, so no preview.
    assert _say("Message Papa on WhatsApp: leaving now") == "Sent to Papa on WhatsApp."


def test_held_message_can_be_cancelled(world, monkeypatch):
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "always")
    _say("Message Papa on WhatsApp: hi")
    assert "Cancelled" in _say("nhi rehne de")
    assert _say("send it") is None
    assert not world["sent"]


def test_bare_ok_does_not_send_a_held_message(world, monkeypatch):
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "always")
    _say("Message Papa on WhatsApp: hi")
    assert _say("ok") is None
    assert not world["sent"]


def test_known_chat_skips_approval(world, monkeypatch):
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "new")
    world["bridge"].append({"jid": "919810000001@s.whatsapp.net", "name": "Papa"})
    assert _say("Message Papa on WhatsApp: hi") == "Sent to Papa on WhatsApp."
    assert world["sent"][0][0] == "919810000001@s.whatsapp.net"


def test_deterministic_layer_owns_message_requests(world):
    from jarvis import commands
    names = [n for n, _ in commands.deterministic_handlers()]
    assert names.index("message") < names.index("open")
    reply = asyncio.run(commands.handle("Jarvis, message Papa on WhatsApp: open the door", CONFIG))
    assert reply == "Sent to Papa on WhatsApp."
    assert world["sent"] == [("919810000001", "open the door")]


def test_a_profile_name_does_not_compete_with_a_saved_name(world):
    # Real case: the phone has Papa, and the bridge knows Papa's chat *and* a stranger whose own
    # WhatsApp profile name is "Papa". The saved one wins; the stranger is not even offered.
    world["bridge"].extend([{"jid": "919810000001@s.whatsapp.net", "name": "Papa"},
                            {"jid": "919800000077@s.whatsapp.net", "name": "Papa"}])
    res = contacts.resolve("Papa", whatsapp.resolve)
    assert res.ok and res.best.number == "919810000001"
    assert res.best.jid == "919810000001@s.whatsapp.net"


def test_bridge_alone_can_still_resolve(world, monkeypatch):
    monkeypatch.setattr(phone_contacts, "all_contacts", lambda: [])
    world["bridge"].append({"jid": "919877777777@s.whatsapp.net", "name": "Kabir"})
    res = contacts.resolve("Kabir", whatsapp.resolve)
    assert res.ok and res.best.jid.startswith("919877777777")


def test_bridge_keeps_saved_names_over_profile_names():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "whatsapp" / "wa_service.js").read_text()
    assert "if (!fromBook && savedNames.has(jid)) return;" in js
