"""The live failure: "Okay, now message <number> with country code plus nine one. Bye."

What happened on the machine: the recogniser wrote "… with the country code plus 911. Bye.", the
voice loop saw "code " in it and sent it to the coding dialogue, the message parser found no
"that/saying/:" and gave up, and the model answered "Yes." Nothing was held, nothing was asked.

These go through /chat — the endpoint the voice loop and the web overlay both use — with a brain
that does what the real ones do first (``commands.handle``) and records whether the model would
have been reached. All numbers are fictional; nothing reaches a bridge.
"""
import pytest
from fastapi.testclient import TestClient

from jarvis import message_command, phone_numbers, route_log, webserver
from jarvis.approvals import MANAGER
from jarvis.audio.conversation import END, ACT, ConversationSession, split_closing
from jarvis.audio.voice_session import wants_coding_agent
from jarvis.config import CONFIG
from jarvis.dedupe import Deduper
from jarvis.integrations import contacts, phone_contacts, whatsapp

NUMBER = "9000000001"            # fictional
E164 = "+919000000001"
LIVE = "ok now message 9000000001 with the country code plus 911. Bye."


class Brain:
    """Stands in for GroqAgent/ChatGPTAgent: deterministic layer first, then 'the model'."""
    def __init__(self):
        self.model_turns: list[str] = []
        self.command_session = "voice"

    async def send(self, text):
        from jarvis.commands import handle
        direct = await handle(text, CONFIG, self.command_session)
        if direct is not None:
            return direct
        self.model_turns.append(text)
        return "Yes."


@pytest.fixture
def live(monkeypatch, tmp_path):
    sent: list = []
    monkeypatch.setattr(CONFIG, "vault_path", tmp_path)
    monkeypatch.setattr(contacts, "_load", lambda: {})
    monkeypatch.setattr(contacts, "_save", lambda data: None)
    monkeypatch.setattr(contacts, "_write_note", lambda entry: None)
    monkeypatch.setattr(phone_contacts, "all_contacts", lambda: [{"name": "Papa", "number": "+919000000009"}])
    monkeypatch.setattr(whatsapp, "status", lambda: True)
    monkeypatch.setattr(whatsapp, "resolve", lambda name: [])

    def fake_send(to, text):
        sent.append((to, text))
        return {"ok": True, "jid": f"{to.split('@')[0]}@s.whatsapp.net", "id": "ABC"}

    monkeypatch.setattr(whatsapp, "send", fake_send)
    monkeypatch.delenv("JARVIS_DRY_RUN_SENDS", raising=False)
    monkeypatch.setenv("JARVIS_SEND_APPROVAL", "new")
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("jarvis.power.asleep", lambda: False)
    monkeypatch.setattr("jarvis.context._CURRENT", "local")   # restored after: it is process-wide
    message_command.clear_drafts()
    route_log.RECENT.clear()
    monkeypatch.setattr("jarvis.dedupe.CHAT", Deduper(window_s=0))
    brain = Brain()
    monkeypatch.setitem(webserver._agent, "a", brain)
    client = TestClient(webserver.app)

    def say(text, event_id=""):
        r = client.post("/chat", json={"message": text, "session_id": "voice", "event_id": event_id})
        return r.json()["reply"]

    return {"say": say, "sent": sent, "brain": brain, "dir": tmp_path}


# --------------------------------------------------------------------------- numbers

@pytest.mark.parametrize("said", [
    "9000000001", "+919000000001", "919000000001", "+91 90000 00001", "+91-90000-00001",
    "09000000001", "(+91) 9000 000 001", "+9119000000001", "9119000000001",
])
def test_every_way_of_writing_an_indian_mobile_is_one_number(said):
    got = phone_numbers.normalize(said)
    assert got.ok and got.e164 == E164


@pytest.mark.parametrize("said", [
    "message 9000000001 with country code plus nine one",
    "message 9000000001 with the country code plus ninety-one",
    "message 9000000001 using country code nine one",
    "message 9000000001 with country code +91",
    "message 9000000001 with the country code plus 911",
    "message +91 9000000001",
    "message 91 9000000001",
    "message 9000000001",
])
def test_spoken_country_codes_normalise_identically(said):
    req = message_command.read(said)
    assert req.number == E164 and req.body == "" and not req.problem


def test_never_a_plus_911_prefix():
    for said in ["+9119000000001", "plus 911 9000000001", "9119000000001"]:
        got = phone_numbers.normalize(said)
        assert got.e164 == E164 and not got.e164.startswith("+911")
    assert all(phone_numbers.is_e164(phone_numbers.normalize(s).e164) for s in ["9000000001", "+14155550100"])


def test_not_a_mobile_number_is_asked_about_not_guessed():
    req = message_command.read("message 12345678 with the country code plus 91")
    assert req.problem and "ending 5678" in req.problem and not req.number
    assert phone_numbers.normalize("5000000001").status == "invalid"


# --------------------------------------------------------------------------- the live sentence

def test_the_live_sentence_asks_what_to_send(live):
    reply = live["say"](LIVE)
    assert reply.endswith("What should I send to this number?")
    assert "ending 0001" in reply and NUMBER not in reply
    assert live["brain"].model_turns == [] and live["sent"] == [] and MANAGER.pending("voice") == []
    row = [r for r in route_log.RECENT if r.get("intent") == "message.compose"][-1]
    assert row["action"] == "ask_body"


def test_a_new_message_request_never_approves_the_held_one(live):
    live["say"](f"Message {NUMBER}: hello")
    live["say"]("Send 'Bye' to 9000000002 on WhatsApp")
    assert live["sent"] == [] and len(MANAGER.pending("voice")) == 2


def test_bye_is_not_the_message(live):
    live["say"](LIVE)
    assert not any(a.details.get("message") == "Bye" for a in MANAGER.pending("voice"))
    req = message_command.read(LIVE)
    assert req.body == "" and req.closing


def test_the_answer_becomes_the_message_and_waits_for_yes(live):
    live["say"](LIVE)
    reply = live["say"]("tell him I'll be late")
    assert reply.startswith("Ready to send a WhatsApp to the number ending 0001")
    assert live["sent"] == []
    (action,) = MANAGER.pending("voice")
    assert action.details["message"] == "I'll be late"


def test_explicit_bye_body(live):
    live["say"](LIVE)
    live["say"]("Send 'Bye' to this number on WhatsApp")
    (action,) = MANAGER.pending("voice")
    assert action.details["message"] == "Bye"
    assert "Which number" in live["say"]("Send 'Bye' to this number on WhatsApp")


def test_action_then_bye_keeps_the_action(live):
    reply = live["say"](f"Message {NUMBER} that I will be late, then bye")
    assert reply.startswith("Ready to send")
    (action,) = MANAGER.pending("voice")
    assert action.details["message"] == "I will be late"
    assert live["brain"].model_turns == []


def test_pure_bye_ends_the_session(live):
    assert live["say"]("Bye.") == "Alright, sir."
    assert live["brain"].model_turns == []
    s = ConversationSession(window_s=8)
    s.wake()
    s.replied("Done.")
    assert s.judge("bye") == END and s.judge("stop listening") == END


def test_approval_survives_the_conversation_closing(live):
    session = ConversationSession(window_s=8)
    session.wake()
    reply = live["say"](f"Message {NUMBER} that I will be late, then bye")
    _, closing = split_closing(f"Message {NUMBER} that I will be late, then bye")
    assert closing and not reply.rstrip().endswith("?")   # the voice loop closes here
    session.end()
    session.wake()                                          # "Jarvis, …" later
    assert session.judge("yes") == ACT
    assert live["say"]("yes") == "Sent to the number ending 0001 on WhatsApp."
    assert live["sent"] == [("919000000001", "I will be late")]


@pytest.mark.parametrize("confirm", ["yes", "send it", "confirm", "haan", "bhej do", "kar do"])
def test_bare_confirmation_with_one_action(live, confirm):
    live["say"](f"Message {NUMBER}: hello")
    assert live["say"](confirm).startswith("Sent to the number ending 0001")
    assert len(live["sent"]) == 1


def test_bare_yes_with_two_actions_asks_which(live):
    live["say"](f"Message {NUMBER}: hello")
    live["say"]("Message 9000000002: hi there")
    reply = live["say"]("yes")
    assert reply.startswith("Which one") and live["sent"] == []


def test_bare_yes_with_nothing_waiting(live):
    assert live["say"]("send it") == "There's nothing waiting to be sent or confirmed right now."
    live["say"]("yes")                     # conversation: the model answers it, nothing runs
    assert live["brain"].model_turns == ["yes"] and live["sent"] == []


def test_bridge_rejection_is_reported(live, monkeypatch):
    live["say"](f"Message {NUMBER}: hello")
    monkeypatch.setattr(whatsapp, "send", lambda to, text: {"ok": False, "error": "number is not on WhatsApp"})
    reply = live["say"]("yes")
    assert reply.startswith("Couldn't send to the number ending 0001") and "not on WhatsApp" in reply


def test_provider_down_after_approval(live, monkeypatch):
    live["say"](f"Message {NUMBER}: hello")
    monkeypatch.setattr(whatsapp, "status", lambda: False)
    assert "isn't connected" in live["say"]("yes")
    assert live["sent"] == []


def test_sent_only_when_the_bridge_names_the_chat(live, monkeypatch):
    live["say"](f"Message {NUMBER}: hello")
    monkeypatch.setattr(whatsapp, "send", lambda to, text: {"ok": True})
    assert "Couldn't send" in live["say"]("yes")


def test_dry_run_sends_nothing(live, monkeypatch):
    monkeypatch.setenv("JARVIS_DRY_RUN_SENDS", "1")
    live["say"](LIVE)
    live["say"]("say hello from the test")
    reply = live["say"]("yes")
    assert reply.startswith("Dry run: would send to the number ending 0001") and live["sent"] == []


def test_logs_carry_no_number_and_no_message(live):
    live["say"](LIVE)
    live["say"]("tell him the secret plan")
    live["say"]("yes")
    for name in ("route-events.jsonl", "approvals.jsonl"):
        text = (live["dir"] / name).read_text()
        assert NUMBER not in text and "secret plan" not in text
    outbox = (live["dir"] / "Jarvis" / "private" / "outbox.jsonl").read_text()
    assert NUMBER not in outbox and "secret plan" not in outbox


# --------------------------------------------------------------------------- the voice side

@pytest.mark.parametrize("said,coding", [
    (LIVE, False),
    ("message 9000000001 with country code plus 91", False),
    ("what is the pin code of Ranchi", False),
    ("send me the OTP code", False),
    ("code a snake game", True),
    ("fix the code in main.py", True),
    ("open antigravity", True),
])
def test_country_code_is_not_a_coding_request(said, coding):
    assert wants_coding_agent(said, editor_in_front=True) is coding


@pytest.mark.parametrize("said,rest,closing", [
    (LIVE, "ok now message 9000000001 with the country code plus 911", True),
    ("Bye.", "", True),
    ("Send 'Bye' to Papa", "Send 'Bye' to Papa", False),
    ("say goodbye to her", "say goodbye to her", False),
    ("open YouTube, that's all", "open YouTube", True),
])
def test_goodbye_is_split_from_the_request(said, rest, closing):
    assert split_closing(said) == (rest, closing)


def test_no_goodbye_inside_words():
    # Whole-word matching only: "bypass", "byebye.com" and "bass" are not goodbyes.
    for said in ["open bypass settings", "play the bass", "go to goodbyes.com"]:
        assert split_closing(said) == (said, False)


def test_never_mind_answers_a_held_action():
    s = ConversationSession(window_s=8)
    s.wake()
    s.replied('Ready to send a WhatsApp. Say "yes" to confirm or "cancel".')
    assert s.judge("never mind") == ACT      # the approval manager cancels it
    s.replied("Done.")
    assert s.judge("never mind") == END


def test_one_utterance_runs_once(live):
    live["say"](f"Message {NUMBER}: hello", event_id="evt-1")
    live["say"](f"Message {NUMBER}: hello", event_id="evt-1")
    assert len(MANAGER.pending("voice")) == 1
    assert [r for r in route_log.RECENT if r.get("intent") == "duplicate"]


def test_same_words_twice_quickly_is_one_event_but_a_later_repeat_is_not():
    now = [100.0]
    d = Deduper(window_s=1.5, clock=lambda: now[0])
    d.done("Jarvis, pause", "Paused.")
    assert d.seen("pause") == "Paused."
    now[0] += 5
    assert d.seen("pause") is None


def test_the_journal_never_gets_the_whole_number(capsys, monkeypatch, tmp_path):
    import jarvis.__main__ as main
    from jarvis.audio import voice_log
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_push_to_hud", lambda *a, **k: None)
    main._voice_event("transcript", LIVE)
    main._voice_event("heard", "message +91 90000 00001")
    out = capsys.readouterr().out
    # By default the journal gets a word count, not the words — the number least of all.
    assert NUMBER not in out and "90000 00001" not in out and "0001" not in out
    assert "(4 words)" in out
    # With diagnostics on, the words are shown and the number is still masked.
    voice_log.enable_diagnostics(5)
    main._voice_event("heard", "message +91 90000 00001")
    out = capsys.readouterr().out
    assert "message" in out and "90000 00001" not in out and NUMBER not in out
