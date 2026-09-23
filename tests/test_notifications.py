"""Notifications said once, briefly, by name — and not at all when the owner asked for quiet."""
import pytest

from jarvis import notifications as n


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    # No real address book: numbers resolve only through this map.
    monkeypatch.setattr(n, "_name_for_number", lambda raw: {"919810000001": "Papa"}.get(
        __import__("re").sub(r"\D", "", raw)[-12:], ""))
    monkeypatch.setattr(n.speakable_sender, "__defaults__", (n._name_for_number,))


# --------------------------------------------------------------------------- senders

@pytest.mark.parametrize("raw,want", [
    ("+91 98100 00001", "Papa"),
    ("+91 98765 43210", "an unknown number"),
    ("919876543210@s.whatsapp.net", "an unknown number"),
    ("arjun.chandra_07", "arjun chandra"),
    ("@the_real_rohit_k", "the real rohit"),
    ("Instagram: Arjun (3 messages)", "Arjun"),
    ("WhatsApp · Papa", "Papa"),
    ("~ Neha", "Neha"),
    ("Papa @ Family Group", "Papa"),
    ("Papa ❤️", "Papa"),
    ("", "someone"),
])
def test_senders_are_said_as_names(raw, want):
    assert n.speakable_sender(raw) == want


def test_no_digits_are_ever_spoken_for_a_number():
    said = n.speakable_sender("+1 (404) 555-0100")
    assert not any(ch.isdigit() for ch in said)


# --------------------------------------------------------------------------- text

def test_links_and_codes_are_not_read():
    t = n.speakable_text("Your order AB12CD34EF56 shipped: https://track.example.com/x?utm=abc track it")
    assert "http" not in t and "AB12CD34EF56" not in t and "utm" not in t
    assert "a link" in t


def test_long_messages_are_cut_short():
    t = n.speakable_text(" ".join(["word"] * 60))
    assert len(t.split()) <= 26


def test_hinglish_text_is_left_alone():
    assert n.speakable_text("bhai kal milna hai kya 😂") == "bhai kal milna hai kya"


# --------------------------------------------------------------------------- bursts

def _ig(text, sender="arjun.chandra_07", thread="t1"):
    return n.NotificationEvent(app="Instagram", sender=sender, text=text, thread=thread)


def test_seven_messages_from_one_person_are_one_announcement():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, clock=clock)
    for i in range(7):
        agg.add(_ig(f"message {i} about the project"), rules={})
        clock.t += 1
        assert agg.due() == []           # still talking: nothing said yet
    clock.t += 5
    out = agg.due()
    assert len(out) == 1
    assert out[0].text.startswith("Seven new Instagram messages from arjun chandra.")
    assert "message 6 about the project" in out[0].text
    assert agg.due() == []


def test_a_single_message_is_said_plainly():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, clock=clock)
    agg.add(n.NotificationEvent("WhatsApp", "+91 98100 00001", "I'll be late"), rules={})
    clock.t += 6
    assert [a.text for a in agg.due()] == ["WhatsApp from Papa: I'll be late"]


def test_a_chat_that_never_stops_is_still_heard():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, max_wait_s=20, clock=clock)
    for _ in range(30):
        agg.add(_ig("spam " + str(clock.t)), rules={})
        clock.t += 1
        if agg.due():
            break
    assert clock.t - 1000.0 <= 21


def test_two_conversations_are_two_announcements():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, clock=clock)
    agg.add(_ig("hi", sender="neha", thread="a"), rules={})
    agg.add(n.NotificationEvent("WhatsApp", "Papa", "call me when free"), rules={"only_family": False})
    clock.t += 6
    assert len(agg.due()) >= 1


def test_duplicate_from_two_sources_is_said_once():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, clock=clock)
    assert agg.add(n.NotificationEvent("WhatsApp", "Papa", "dinner?"), rules={}) == n.ANNOUNCE
    assert agg.add(n.NotificationEvent("WhatsApp", "Papa", "dinner?"), rules={}) == n.SUPPRESS
    clock.t += 6
    assert len(agg.due()[0].events) == 1


def test_urgent_is_not_held_back():
    clock = Clock()
    agg = n.Aggregator(quiet_s=5, clock=clock)
    assert agg.add(n.NotificationEvent("WhatsApp", "Papa", "urgent, call me"), rules={}) == n.URGENT
    out = agg.due()
    assert out and out[0].priority == n.URGENT


# --------------------------------------------------------------------------- the owner's rules

def test_muted_app_goes_to_the_summary_for_a_while(monkeypatch):
    n.mute("app", "Instagram", seconds=7200)
    clock = Clock()
    agg = n.Aggregator(clock=clock)
    assert agg.add(_ig("hello")) == n.SUMMARY
    clock.t += 30
    assert agg.due() == []
    assert "Instagram" in agg.summary()


def test_mute_expires(monkeypatch):
    n.mute("app", "Instagram", seconds=10)
    real = n.time.time
    monkeypatch.setattr(n.time, "time", lambda: real() + 11)
    assert n.classify(_ig("hello")) == n.ANNOUNCE


def test_muted_group(monkeypatch):
    n.mute("thread", "family-group")
    assert n.classify(n.NotificationEvent("WhatsApp", "Neha", "hi", thread="family-group")) == n.SUMMARY


def test_only_family_interrupts():
    n.set_only_family(True)
    assert n.classify(n.NotificationEvent("WhatsApp", "Rohit", "hey")) == n.SUMMARY
    assert n.classify(n.NotificationEvent("WhatsApp", "Papa", "hey")) == n.ANNOUNCE


def test_busy_holds_ordinary_messages_but_not_urgent_ones():
    assert n.classify(n.NotificationEvent("WhatsApp", "Rohit", "hey"), busy=True, rules={}) == n.SUMMARY
    assert n.classify(n.NotificationEvent("WhatsApp", "Rohit", "emergency, call me"), busy=True, rules={}) == n.URGENT


def test_summary_groups_by_app_and_person():
    agg = n.Aggregator()
    for _ in range(3):
        agg.digest.append(_ig("x"))
    agg.digest.append(n.NotificationEvent("WhatsApp", "Papa", "y"))
    s = agg.summary()
    assert "Three on Instagram, from arjun chandra (3)." in s and "One on WhatsApp, from Papa." in s
    assert agg.summary() == "Nothing new while you were busy."


def test_calls_never_read_digits():
    assert n.call_announcement("", "+91 98765 43210", "ringing") == "Incoming call from an unknown number."
    assert n.call_announcement("", "+91 98100 00001", "missedCall") == "You missed a call from Papa."


# --------------------------------------------------------------------------- spoken controls

from jarvis import notification_command as cmd  # noqa: E402


def test_dont_announce_instagram_for_two_hours(monkeypatch):
    reply = cmd.handle("Don't announce Instagram for two hours")
    assert "Instagram" in reply and "2 hours" in reply
    until = n.load_rules()["muted_apps"]["instagram"]
    assert 7100 < until - n.time.time() <= 7200
    assert n.classify(_ig("hi")) == n.SUMMARY
    assert "again" in cmd.handle("unmute instagram")
    assert n.classify(_ig("hi")) == n.ANNOUNCE


def test_stop_reading_this_group_mutes_the_last_conversation():
    agg = n.Aggregator(quiet_s=0, shared=True)
    agg.add(n.NotificationEvent("WhatsApp", "Neha @ Cousins", "party!", thread="cousins@g.us"), rules={})
    agg.due()
    assert "that conversation" in cmd.handle("Stop reading messages from this group")
    assert n.classify(n.NotificationEvent("WhatsApp", "Someone", "hi", thread="cousins@g.us")) == n.SUMMARY


def test_stop_reading_messages_from_a_person():
    cmd.handle("stop reading messages from Rohit")
    assert n.classify(n.NotificationEvent("WhatsApp", "Rohit", "hey")) == n.SUMMARY


def test_only_family_by_voice():
    assert "Only family" in cmd.handle("Only interrupt me for family")
    assert n.load_rules()["only_family"] is True
    cmd.handle("interrupt me for everyone again")
    assert n.load_rules()["only_family"] is False


def test_summary_and_read_again_work_across_processes():
    voice = n.Aggregator(quiet_s=0, shared=True)
    n.mute("app", "Instagram")
    for i in range(3):
        voice.add(_ig(f"x{i}"))
    voice.add(n.NotificationEvent("WhatsApp", "Papa", "dinner at 8"), rules={})
    voice.due()
    # The web process answers these from disk.
    assert cmd.handle("Read that again") == "WhatsApp from Papa: dinner at 8"
    assert cmd.handle("Summarize my notifications") == "Three on Instagram, from arjun chandra (3)."
    assert cmd.handle("summarise my notifications") == "Nothing new while you were busy."


def test_ordinary_requests_are_not_taken():
    for said in ["open instagram", "read my email", "message Papa on WhatsApp: hi", "stop"]:
        assert cmd.handle(said) is None


# --------------------------------------------------------------------------- the voice session wiring

def test_voice_session_says_a_burst_once(monkeypatch):
    import asyncio
    import types

    from jarvis.agent import away
    from jarvis.audio.voice_session import VoiceSession
    from jarvis.config import Config

    monkeypatch.setattr(away, "is_away", lambda config: False)
    monkeypatch.setattr("jarvis.preferences.notifications_enabled", lambda: True)
    spoken = []
    clock = Clock()
    fake = types.SimpleNamespace(
        config=Config(), _kc=None, _dictating=False, on_event=lambda *a: None,
        _speak=lambda text, force=False: spoken.append(text),
        _notices=n.Aggregator(quiet_s=5, clock=clock), _events=None)

    async def drive():
        fake._events = asyncio.Queue()
        for i in range(7):
            await VoiceSession._handle_phone_event(
                fake, {"type": "message", "app": "Instagram", "title": "arjun.chandra_07",
                       "text": f"about the project, part {i}", "id": "thread-1"}, None)
        assert spoken == []
        clock.t += 6
        for a in fake._notices.due():
            await VoiceSession._handle_phone_event(fake, {"type": "announce", "text": a.text}, None)
        await VoiceSession._handle_phone_event(
            fake, {"type": "call", "name": "", "number": "+91 98765 43210", "event": "ringing"}, None)

    asyncio.run(drive())
    assert spoken[0].startswith("Seven new Instagram messages from arjun chandra.")
    assert spoken[1] == "Incoming call from an unknown number."
    assert len(spoken) == 2
