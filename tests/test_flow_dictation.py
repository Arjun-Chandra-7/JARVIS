"""System-wide dictation (jarvis/flow): key → microphone → text → cleanup → the focused field.

Nothing here touches a real keyboard, microphone, clipboard, accessibility bus or network: each
of those is a small fake with the same shape. Names and text are fictional.
"""
import json
import threading
import time
from types import SimpleNamespace

import pytest

from jarvis.flow import cleanup, commands, dictionary, engine as fengine, history, insert, keys, profiles
from jarvis.flow import stt as fstt
from jarvis.flow.mic import MicCoordinator, MicState, Preempted


@pytest.fixture(autouse=True)
def _private_state(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_DICTATION_HISTORY", raising=False)
    yield tmp_path


# =========================================================================== microphone

def test_one_owner_and_dictation_preempts_the_assistant():
    now = [0.0]
    mic = MicCoordinator(lease_s=5, clock=lambda: now[0])
    mic.listen_for_wake()
    assert mic.state is MicState.WAKE_LISTENING
    mic.assistant_capture()
    read = mic.guard(lambda: [0] * 512)
    assert read() == [0] * 512
    mic.request_dictation()
    with pytest.raises(Preempted):
        read()                                           # the assistant capture stops at once
    mic.begin_dictation()
    assert mic.owner == "dictation" and not mic.dictation_requested
    with pytest.raises(Preempted):
        mic.assistant_capture()                          # nobody else gets it meanwhile
    mic.end_dictation()
    assert mic.state is MicState.WAKE_LISTENING and mic.owner == "wake"


def test_an_abandoned_dictation_gives_the_microphone_back():
    now = [0.0]
    mic = MicCoordinator(lease_s=5, clock=lambda: now[0])
    mic.begin_dictation()
    assert not mic.expire()
    now[0] = 6
    assert mic.expire() and mic.owner == "wake"


# =========================================================================== keys

def _gesture(mode, now):
    seen = []
    g = keys.Gesture(mode, clock=lambda: now[0], on_start=lambda: seen.append("start"),
                     on_stop=lambda: seen.append("stop"), on_cancel=lambda: seen.append("cancel"),
                     on_confirm=lambda: seen.append("confirm"))
    return g, seen


def test_hold_to_dictate():
    now = [0.0]
    g, seen = _gesture("hold", now)
    g.key(keys.KEY_DOWN)
    now[0] = 2.0
    g.key(keys.KEY_REPEAT)                              # auto-repeat is ignored
    g.key(keys.KEY_UP)
    assert seen == ["start", "stop"] and g.state == "idle"


def test_toggle_dictation():
    now = [0.0]
    g, seen = _gesture("toggle", now)
    g.key(keys.KEY_DOWN)
    g.key(keys.KEY_UP)
    assert seen == ["start"] and g.state == "hands_free"
    now[0] = 5
    g.key(keys.KEY_DOWN)
    g.key(keys.KEY_UP)
    assert seen == ["start", "stop"] and g.state == "idle"


def test_auto_tap_is_hands_free_and_hold_is_push_to_talk():
    now = [0.0]
    g, seen = _gesture("auto", now)
    g.key(keys.KEY_DOWN)
    now[0] = 0.1
    g.key(keys.KEY_UP)
    assert g.state == "hands_free"
    g.key(keys.KEY_DOWN)
    g.key(keys.KEY_UP)
    assert seen == ["start", "stop"]
    g2, seen2 = _gesture("auto", now)
    now[0] = 10
    g2.key(keys.KEY_DOWN)
    now[0] = 12
    g2.key(keys.KEY_UP)
    assert seen2 == ["start", "stop"]


def test_escape_cancels_and_confirms_nothing():
    now = [0.0]
    g, seen = _gesture("hold", now)
    g.key(keys.KEY_DOWN)
    assert g.cancel() == "cancel" and seen == ["start", "cancel"]
    g.key(keys.KEY_UP)                                  # the release after a cancel does nothing
    assert seen == ["start", "cancel"]
    assert g.cancel() is None                           # Escape when idle is not ours


def test_a_preview_waits_for_the_key():
    now = [0.0]
    g, seen = _gesture("hold", now)
    g.await_confirmation()
    g.key(keys.KEY_DOWN)
    g.key(keys.KEY_UP)
    assert seen == ["confirm"] and g.state == "idle"


def test_one_press_seen_on_two_devices_is_one_press():
    now = [0.0]
    g, seen = _gesture("hold", now)
    watcher = keys.DictationKeys(100, g)
    watcher._deliver(100, keys.KEY_DOWN)
    watcher._deliver(100, keys.KEY_DOWN)
    assert seen == ["start"]


def test_key_conflicts_are_named():
    assert "push-to-talk" in keys.conflicts("rightctrl", "rightctrl")[0]
    assert keys.conflicts("rightalt", "rightctrl", xkb_options=[]) == []
    assert "AltGr" in keys.conflicts("rightalt", "rightctrl", xkb_options=["lv3:ralt_switch"])[0]
    assert "cancel" in " ".join(keys.conflicts("1", "rightctrl"))
    assert "not a key" in keys.conflicts("banana")[0]


# =========================================================================== cleanup

@pytest.mark.parametrize("said,profile,written", [
    ("um I think we should uh meet tomorrow", "prose", "I think we should meet tomorrow."),
    ("Buy eggs comma milk comma and bread", "prose", "Buy eggs, milk, and bread."),
    ("Tomorrow at five, actually make that six", "prose", "Tomorrow at six."),
    ("Tomorrow at five—actually make that six", "prose", "Tomorrow at six."),
    ("Tell him I'll come on Friday—no, Saturday", "prose", "Tell him I'll come on Saturday."),
    ("First point finish maths second point revise science", "prose", "1. Finish maths\n2. Revise science"),
    ("Kal maths ka homework submit karna hai", "messaging", "Kal maths ka homework submit karna hai"),
    ("आज electricity का chapter revise करना है", "prose", "आज electricity का chapter revise करना है।"),
    ("Pythagoras theorem mein hypotenuse ka square", "messaging", "Pythagoras theorem mein hypotenuse ka square"),
    ("the the meeting is at five", "prose", "The meeting is at five."),
    ("I want to— I need to go home", "prose", "I need to go home."),
    ("Send the report tomorrow. Scratch that. Send it Friday", "prose", "Send it Friday."),
    ("hello new paragraph how are you question mark", "email", "Hello\n\nHow are you?"),
    ("I will come on Friday replace Friday with Monday", "prose", "I will come on Monday."),
    ("kal chalte hain, nahi, instead parso", "messaging", "Parso chalte hain"),
    ("my email is riya at example dot com", "prose", "My email is riya@example.com."),
])
def test_cleanup(said, profile, written):
    assert cleanup.clean(said, profile).text == written


@pytest.mark.parametrize("said,profile", [
    ("I actually liked it very very much", "prose"),          # "actually" and "very very" are meant
    ("I was tired, actually I slept early", "prose"),
    ("Do you want tea? No, coffee.", "prose"),               # an answer, not a correction
    ("jaldi jaldi aao", "messaging"),
    ("bye bye see you", "messaging"),
])
def test_cleanup_does_not_change_meaning(said, profile):
    out = cleanup.clean(said, profile).text
    assert out.lower().rstrip(".") == said.lower().rstrip(".")


def test_mid_sentence_language_switching_is_kept_as_said():
    said = "Mujhe kal ka test cancel karna hai because the teacher is on leave"
    assert cleanup.clean(said, "messaging").text == said


def test_terminal_is_literal():
    out = cleanup.clean("git status dash dash short", "terminal").text
    assert out == "git status --short"
    assert cleanup.clean("ls dash la slash home", "terminal").text == "ls -la/home" or \
        cleanup.clean("ls dash la slash home", "terminal").text.startswith("ls -la")


def test_code_gets_no_capitals_or_full_stop():
    out = cleanup.clean("user underscore name equals none", "code").text
    assert out == "user_name=none"


def test_search_is_a_query():
    assert cleanup.clean("search for NCERT class ten maths solutions", "search").text == \
        "NCERT class ten maths solutions"


def test_dictionary_spellings():
    entries = [dictionary.Entry("Viralyst", ["viralist", "viral list"]), dictionary.Entry("NCERT", ["ncrt"])]
    out = cleanup.clean("the viral list launch uses ncrt examples", "prose", dictionary.replacements(entries))
    assert out.text == "The Viralyst launch uses NCERT examples."


# =========================================================================== commands

@pytest.mark.parametrize("said,kind", [
    ("new paragraph", "new_paragraph"), ("New line.", "new_line"), ("bullet list", "bullet_list"),
    ("numbered list", "numbered_list"), ("delete last word", "delete_last_word"),
    ("delete the last sentence", "delete_last_sentence"), ("undo that", "undo"),
    ("select last sentence", "select_last_sentence"), ("make this formal", "transform"),
    ("make this shorter", "transform"), ("fix grammar", "transform"),
    ("translate this to English", "transform"), ("write this in Hinglish", "transform"),
    ("cancel", "cancel"), ("rehne do", "cancel"), ("please make this formal", "transform"),
    ("can you make this sound more formal", "transform"), ("isko formal banao", "transform"),
    ("replace Friday with Monday", "replace"), ("always spell this as Viralyst", "always_spell"),
    ("add this name to my dictionary", "add_to_dictionary"), ("forget that correction", "forget_correction"),
])
def test_commands(said, kind):
    cmd = commands.parse(said)
    assert cmd is not None and cmd.kind == kind and cmd.confidence >= commands.THRESHOLD


@pytest.mark.parametrize("said", [
    "I need to delete the last sentence of my essay before class",
    "new paragraph ideas for the project are welcome",
    "we should cancel the trip if it rains",
    "undo is my favourite shortcut",
    "please make this formal dinner happen on Friday evening with everyone",
])
def test_ambiguous_phrases_stay_text(said):
    assert commands.parse(said) is None


def test_list_commands_format_deterministically():
    assert commands.as_bullets("eggs, milk, and bread") == "- Eggs\n- Milk\n- Bread"
    assert commands.as_bullets("Finish maths. Revise science.", numbered=True) == "1. Finish maths\n2. Revise science"


# =========================================================================== dictionary

def test_dictionary_is_seeded_without_contacts_and_saved_privately(_private_state):
    entries = dictionary.load()
    assert {"JARVIS", "Viralyst", "NCERT", "Pythagoras"} <= {e.written for e in entries}
    assert all(e.context in {"assistant", "project", "school", "app"} for e in entries)
    dictionary.save(dictionary.with_spelling(entries, "Aarav Test", "aarav tests"))
    path = _private_state / "dictation-dictionary.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert "Aarav Test" in [e.written for e in dictionary.load()]


def test_always_spell_finds_the_misheard_word():
    assert dictionary.closest_word("the viralist dashboard is live", "Viralyst") == "viralist"
    assert dictionary.closest_word("nothing similar here", "Viralyst") is None


def test_forget_removes_only_the_last_voice_addition():
    base = dictionary.load()
    added = dictionary.with_spelling(dictionary.with_spelling(base, "Kiaan", "kian"), "Zoya", "zoyaa")
    assert [e.written for e in dictionary.without_last_added(added)][-1] == "Kiaan"


# =========================================================================== profiles

@pytest.mark.parametrize("target,profile", [
    (profiles.Target(app="gnome-terminal-server", role="terminal"), "terminal"),
    (profiles.Target(app="Code", window="main.py - Visual Studio Code", role="entry"), "code"),
    (profiles.Target(app="Zen", window="WhatsApp — Zen Browser", role="entry", multi_line=True), "messaging"),
    (profiles.Target(app="Zen", window="Inbox - Gmail — Zen Browser", role="entry", name="Message Body",
                     multi_line=True), "email"),
    (profiles.Target(app="gnome-text-editor", window="notes.txt", role="text", multi_line=True), "document"),
    (profiles.Target(app="Zen", window="Google — Zen Browser", role="entry", name="Search"), "search"),
    (profiles.Target(app="Zen", window="Some page", role="entry", name="Comment"), "prose"),
])
def test_profiles(target, profile):
    assert profiles.profile_for(target) == profile


@pytest.mark.parametrize("target", [
    profiles.Target(role="password text"), profiles.Target(role="entry", name="Enter OTP"),
    profiles.Target(role="entry", name="API key"), profiles.Target(role="entry", name="PIN"),
    profiles.Target(role="entry", name="Verification code"),
])
def test_secret_fields_are_recognised(target):
    assert target.is_secret


# =========================================================================== insertion

class FakeBridge:
    def __init__(self, info=None, insert_reply=None, after=None):
        self.info = info or {"ok": True, "role": "text", "can_insert": True, "has_text": True, "length": 5,
                             "before": "Hello", "app": "gnome-text-editor", "window": "notes"}
        self.insert_reply = insert_reply or {"ok": True, "verified": True, "start": 5, "end": 11}
        self.after = after
        self.calls = []
        self.text = "Hello"

    def request(self, op, **args):
        self.calls.append((op, args))
        if op == "focus":
            return self.after or self.info          # ``after``: the field as it reads after a paste
        if op == "insert":
            return self.insert_reply
        if op == "read":
            return {"ok": True, "text": self.text[args["start"]:args["end"]]}
        if op == "select":
            return {"ok": True}
        return {"ok": True}


class FakeClip:
    def __init__(self, paste_ok=True):
        self.board = "user's own clipboard"
        self.paste_ok = paste_ok
        self.pasted = []

    def available(self):
        return True

    def save(self):
        from jarvis.flow.clipboard import Saved
        return Saved("text/plain", self.board.encode(), False)

    def put(self, text, mime="text/plain", data=None):
        self.board = data.decode() if data is not None else text
        return True

    def paste_keys(self, terminal=False):
        if self.paste_ok:
            self.pasted.append((self.board, terminal))
        return self.paste_ok

    def restore(self, saved):
        self.board = saved.data.decode()
        return True


def test_accessibility_insertion_is_verified():
    b = FakeBridge()
    got = insert.insert(" world", b.info, bridge=b, clip=FakeClip())
    assert got.status == "inserted" and got.verified and got.method == "atspi"


def test_password_fields_are_refused_before_anything_happens():
    b = FakeBridge(info={"ok": True, "role": "password text", "can_insert": True, "secret": True})
    clip = FakeClip()
    got = insert.insert("hunter2", b.info, bridge=b, clip=clip)
    assert got.status == "refused" and not [c for c in b.calls if c[0] == "insert"] and not clip.pasted


def test_clipboard_fallback_restores_the_clipboard():
    info = {"ok": True, "role": "text", "can_insert": False, "has_text": True, "length": 5, "before": "Hello"}
    after = {"ok": True, "has_text": True, "length": 11, "before": "Hello world", "caret": 11}
    b = FakeBridge(info=info, after=after)
    clip = FakeClip()
    got = insert.insert(" world", info, bridge=b, clip=clip)
    assert got.status == "pasted" and got.verified and clip.pasted == [(" world", False)]
    time.sleep(0.35)                                   # restored off the critical path, shortly after
    assert clip.board == "user's own clipboard"


def test_a_terminal_paste_is_verified_from_the_field_tail():
    info = {"ok": True, "role": "terminal", "can_insert": False, "has_text": True, "length": 40}
    b = FakeBridge(info=info, after={"ok": True, "role": "terminal", "has_text": True, "length": 58,
                                     "caret": -1, "before": ""})
    b.text = "user@host:~$ " + " " * 27 + "git status --short"

    def request(op, **args):
        if op == "read":
            return {"ok": True, "text": b.text[-60:]}
        return FakeBridge.request(b, op, **args)
    b.request = request
    got = insert.insert("git status --short", info, terminal=True, bridge=b, clip=FakeClip())
    assert got.status == "pasted" and got.verified


def test_shell_words():
    assert cleanup.clean("Get status dash dash short", "terminal").text == "git status --short"
    assert cleanup.clean("Ls dash la", "terminal").text == "ls -la"


def test_a_browser_paste_is_confirmed_from_the_page_itself():
    # Found live: Zen took the pasted text (the page held it) while its accessible length stayed
    # the same, so the paste read as unconfirmed. The page's own focused field is the check.
    info = {"ok": True, "role": "entry", "app": "Zen", "can_insert": False, "has_text": True, "length": 0}
    b = FakeBridge(info=info, after=dict(info))
    seen = []
    got = insert.insert("NCERT class 10 solutions", info, bridge=b, clip=FakeClip(),
                        browser_check=lambda text, app: seen.append(app) or True)
    assert got.status == "pasted" and got.verified and seen == ["Zen"]


def test_an_accessible_insert_that_changes_nothing_falls_back_to_paste():
    info = {"ok": True, "role": "entry", "app": "Zen", "can_insert": True, "has_text": True, "length": 0}
    b = FakeBridge(info=info, insert_reply={"ok": True, "verified": False, "grew": 0})
    clip = FakeClip()
    got = insert.insert("hello", info, bridge=b, clip=clip, browser_check=lambda *a: False)
    assert got.status == "pasted" and clip.pasted == [("hello", False)]


def test_clipboard_is_restored_even_when_the_paste_fails():
    info = {"ok": True, "role": "text", "can_insert": False}
    clip = FakeClip(paste_ok=False)
    got = insert.insert("words", info, bridge=FakeBridge(info=info), clip=clip)
    assert got.status == "failed" and clip.board == "user's own clipboard"


def test_a_half_worked_insertion_is_never_pasted_again():
    b = FakeBridge(insert_reply={"ok": True, "verified": False, "grew": 3, "start": 5, "end": 11})
    clip = FakeClip()
    got = insert.insert(" world", b.info, bridge=b, clip=clip)
    assert got.status == "inserted" and not got.verified and clip.pasted == []


def test_terminal_insertion_never_carries_a_newline():
    info = {"ok": True, "role": "terminal", "can_insert": False}
    clip = FakeClip()
    insert.insert("rm -rf build\n", info, terminal=True, bridge=FakeBridge(info=info), clip=clip)
    assert clip.pasted == [("rm -rf build", True)]


def test_devanagari_is_never_sent_as_keystrokes():
    info = {"ok": False}

    class NoClip(FakeClip):
        def available(self):
            return False
    typed = []
    got = insert.insert("नमस्ते", info, bridge=FakeBridge(info=info), clip=NoClip(),
                        keys=SimpleNamespace(type_text=lambda t: typed.append(t) or True))
    assert got.status == "failed" and typed == []
    got = insert.insert("hello", info, bridge=FakeBridge(info=info), clip=NoClip(),
                        keys=SimpleNamespace(type_text=lambda t: typed.append(t) or True))
    assert got.status == "typed" and not got.verified


# =========================================================================== speech to text

def test_the_fallback_is_used_when_the_primary_times_out():
    def slow(*a):
        raise TimeoutError("groq")
    table = {"groq": slow, "local": lambda *a: ("hello there", "en")}
    heard = fstt.transcribe(b"\0" * 32000, providers=["groq", "local"], table=table)
    assert heard.text == "hello there" and heard.provider == "local" and heard.failures == ["groq: TimeoutError"]


def test_the_vocabulary_echoed_back_on_silence_is_not_a_transcript():
    table = {"groq": lambda *a: ("JARVIS, Viralyst, NCERT", "en"), "local": lambda *a: ("", "")}
    heard = fstt.transcribe(b"\0" * 32000, vocabulary="JARVIS, Viralyst, NCERT, Pythagoras",
                            providers=["groq", "local"], table=table)
    assert heard.text == "" and "echoed" in heard.failures[0]


# =========================================================================== the engine

def _engine(bridge=None, text="um I think we should uh meet tomorrow", clip=None, complete=None, fail=False):
    events = []

    def stt_fn(pcm, rate, vocab):
        if fail:
            return fstt.Heard(failures=["groq: ConnectError", "local: empty"])
        return fstt.Heard(text=text, provider="groq")
    eng = fengine.DictationEngine(emit=lambda s, **d: events.append((s, d)), bridge=bridge or FakeBridge(),
                                  stt_fn=stt_fn, clip=clip or FakeClip(), complete=complete)
    eng.inserter = lambda t, info, terminal=False: insert.insert(t, info, terminal=terminal, bridge=eng.bridge,
                                                                  clip=eng.clip)
    return eng, events


PCM = b"\x10\x00" * 16000          # one second of (quiet) audio


def test_end_to_end_into_a_field(_private_state):
    eng, events = _engine()
    out = eng.finish(PCM)
    assert out["status"] == "inserted" and out["text"] == " I think we should meet tomorrow."
    assert [s for s, _ in events] == ["processing", "inserting", "inserted"]
    row = history.last()
    assert row["raw"] == "um I think we should uh meet tomorrow" and row["status"] == "inserted"
    assert not list(_private_state.rglob("*.wav"))                 # audio never written


def test_dry_run_goes_everywhere_but_the_field(_private_state, monkeypatch):
    monkeypatch.setenv("JARVIS_DICTATION_DRY_RUN", "1")
    clip = FakeClip()
    eng, events = _engine(clip=clip)
    out = eng.finish(PCM)
    assert out["status"] == "dry_run" and out["text"].strip() == "I think we should meet tomorrow."
    assert not [c for c in eng.bridge.calls if c[0] == "insert"] and clip.pasted == []
    assert history.last()["status"] == "dry_run"


def test_a_password_field_keeps_nothing_and_is_not_even_transcribed(_private_state):
    b = FakeBridge(info={"ok": True, "role": "password text", "secret": True, "can_insert": True})
    heard = []
    eng, events = _engine(bridge=b, text="my secret words")
    eng.stt_fn = lambda *a: heard.append(a) or fstt.Heard(text="my secret words")
    assert eng.finish(PCM)["status"] == "refused"
    assert heard == [] and history.last() is None and events[-1][0] == "refused"


def test_hindi_script_follows_the_field(monkeypatch):
    monkeypatch.delenv("JARVIS_DICTATION_HINDI_SCRIPT", raising=False)
    assert fstt.hindi_script("messaging") == "roman" and fstt.hindi_script("document") == "devanagari"
    assert fstt.ROMAN_HINGLISH_HINT in fstt.prompt_for("JARVIS", "roman")
    monkeypatch.setenv("JARVIS_DICTATION_HINDI_SCRIPT", "devanagari")
    assert fstt.hindi_script("messaging") == "devanagari"


def test_terminal_text_is_previewed_and_needs_the_key(_private_state):
    info = {"ok": True, "role": "terminal", "app": "gnome-terminal-server", "can_insert": False}
    b = FakeBridge(info=info)
    clip = FakeClip()
    eng, events = _engine(bridge=b, text="git log dash dash oneline", clip=clip)
    out = eng.finish(PCM)
    assert out["status"] == "preview" and clip.pasted == []
    assert events[-1][0] == "preview" and events[-1][1]["preview"] == "git log --oneline"
    eng.confirm_pending()
    assert clip.pasted == [("git log --oneline", True)]           # pasted, never followed by Enter


def test_a_discarded_preview_inserts_nothing(_private_state):
    info = {"ok": True, "role": "terminal", "app": "kgx"}
    clip = FakeClip()
    eng, _ = _engine(bridge=FakeBridge(info=info), text="shutdown now", clip=clip)
    eng.finish(PCM)
    eng.discard_pending()
    assert eng.confirm_pending()["status"] == "none" and clip.pasted == []
    assert history.last()["status"] == "discarded"


def test_cancelled_capture_inserts_nothing(_private_state):
    eng, events = _engine()
    stop, cancel = threading.Event(), threading.Event()
    frames = iter([[1000] * 512] * 3)

    def read():
        try:
            return next(frames)
        except StopIteration:
            cancel.set()
            return [0] * 512
    assert eng.capture(read, stop, cancel) is None
    assert eng.finish(None)["status"] == "cancelled" and history.last() is None


def test_capture_measures_the_key_to_recording_time():
    now = [100.0]
    eng = fengine.DictationEngine(clock=lambda: now[0])
    stop, cancel = threading.Event(), threading.Event()
    count = [0]

    def read():
        count[0] += 1
        now[0] += 0.032
        if count[0] > 20:
            stop.set()
        return [2000] * 512
    pcm = eng.capture(read, stop, cancel, key_at=99.95)
    assert pcm and eng.metrics["key_to_recording_ms"] == pytest.approx(82.0, abs=1)


def test_the_first_word_before_the_key_is_kept_and_metrics_are_per_session():
    eng = fengine.DictationEngine()
    eng.metrics = {"release_to_transcript_s": 9.9}           # from an earlier dictation
    stop, cancel = threading.Event(), threading.Event()
    stop.set()
    pcm = eng.capture(lambda: [0] * 512, stop, cancel, preroll=[[3000] * 512] * 8)
    assert len(pcm) == 8 * 512 * 2 and eng.metrics["preroll_ms"] == 256
    assert "release_to_transcript_s" not in eng.metrics


def test_a_space_between_sentences_when_the_field_hides_its_text(_private_state):
    # Found live in GNOME Text Editor: the second sentence went in as "tomorrow.Buy eggs".
    info = {"ok": True, "role": "text", "app": "gnome-text-editor", "can_insert": True, "has_text": True,
            "length": 59, "before": ""}
    eng, _ = _engine(bridge=FakeBridge(info=info), text="buy eggs, milk and bread")
    eng.last = {"text": "Please remember to revise the electricity chapter tomorrow."}
    assert eng.handle_text("buy eggs, milk and bread", info)["text"].startswith(" Buy eggs")


def test_speech_recognition_offline_is_said(_private_state):
    eng, events = _engine(fail=True)
    assert eng.finish(PCM)["status"] == "no_text" and events[-1][0] in {"offline", "error"}


def test_failed_insertion_keeps_the_text_for_later(_private_state):
    info = {"ok": True, "role": "text", "can_insert": False}
    eng, events = _engine(bridge=FakeBridge(info=info), clip=FakeClip(paste_ok=False))
    out = eng.finish(PCM)
    assert out["status"] == "failed" and "paste last dictation" in events[-1][1]["message"]
    assert history.last()["cleaned"].strip() == "I think we should meet tomorrow."
    eng.clip.paste_ok = True
    assert eng.handle_text("paste last dictation", info)["status"].startswith("pasted")


def test_retry_does_not_insert_twice(_private_state):
    eng, _ = _engine()
    eng.finish(PCM)
    assert eng.handle_text("retry", eng.bridge.info)["status"] == "already_inserted"


def test_edit_commands_act_on_what_was_just_dictated(_private_state):
    b = FakeBridge()
    b.text = "Hello I will come on Friday. See you there."
    b.insert_reply = {"ok": True, "verified": True, "start": 5, "end": 5 + len(" I will come on Friday. See you there.")}
    eng, _ = _engine(bridge=b, text="I will come on Friday new paragraph See you there")
    eng.last = {"text": b.text[5:], "start": 5, "end": len(b.text), "verified": True}
    out = eng.handle_text("replace Friday with Monday", b.info)
    assert out["status"] == "edited" and "Monday" in out["text"]
    b.text = "Hello" + out["text"]
    eng.last.update(end=len(b.text))
    out = eng.handle_text("delete the last sentence", b.info)
    assert out["status"] == "edited" and "See you there" not in out["text"]


def test_transform_uses_the_model_only_for_explicit_edits(_private_state):
    asked = []
    eng, _ = _engine(complete=lambda system, text: asked.append(text) or "I shall arrive on Friday.")
    eng.last = {"text": "i'll come friday", "start": -1, "end": -1, "verified": False}
    out = eng.handle_text("make this formal", eng.bridge.info)
    assert asked == ["i'll come friday"] and out["text"] == "I shall arrive on Friday."
    assert out["status"] == "copied"                   # unaddressable field: on the clipboard instead


def test_dictionary_changes_wait_for_confirmation(_private_state):
    eng, events = _engine()
    eng.last = {"text": "the viralist dashboard", "start": -1, "end": -1}
    assert eng.handle_text("always spell this as Viralyst", eng.bridge.info)["status"] == "confirm"
    assert not (_private_state / "dictation-dictionary.json").exists()
    eng.confirm_pending()
    viral = [e for e in dictionary.load() if e.written == "Viralyst"][0]
    assert "viralist" in viral.spoken


# =========================================================================== history

def test_history_is_private_bounded_and_clearable(_private_state, monkeypatch):
    history.add(app="a", profile="prose", raw="r", cleaned="c", status="inserted")
    path = _private_state / "dictation-history.jsonl"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    monkeypatch.setenv("JARVIS_DICTATION_HISTORY_HOURS", "0")
    time.sleep(0.01)
    assert history.last() is None
    monkeypatch.setenv("JARVIS_DICTATION_HISTORY_HOURS", "24")
    history.add(app="a", profile="prose", raw="r", cleaned="c", status="inserted")
    assert history.clear() == 1 and not path.exists()


def test_history_is_cleared_and_discarded_by_voice(_private_state):
    eng, _ = _engine()
    eng.finish(PCM)
    eng.finish(PCM)
    assert eng.handle_text("discard the last transcript", eng.bridge.info)["status"] == "discarded"
    assert len(history.prune()) == 1
    assert eng.handle_text("clear dictation history", eng.bridge.info) == {"status": "cleared", "count": 1}
    assert history.last() is None and eng.last is None


def test_history_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("JARVIS_DICTATION_HISTORY", "off")
    assert history.add(app="a", profile="p", raw="r", cleaned="c", status="s") is None
    assert history.last() is None


# =========================================================================== the voice process

def _fake_session(flow, coord):
    from collections import deque

    from jarvis.audio.voice_session import VoiceSession
    events = []
    s = SimpleNamespace(_flow=flow, _coord=coord, _speaking=threading.Event(), sample_rate=16000,
                        _dict_stop=threading.Event(), _dict_cancel=threading.Event(), _dict_key_at=0.0,
                        _gesture=keys.Gesture("hold"), mic=SimpleNamespace(read=lambda: [0] * 512),
                        on_event=lambda k, t="": events.append((k, t)), _mic_lock=threading.Lock(),
                        _dict_busy=threading.Lock(), _preroll=deque(maxlen=8), _spoke_at=0.0)
    s._dictation_event = lambda state, **d: events.append(("dictation", state))
    return s, events, VoiceSession


def test_dictation_records_at_once_while_the_assistant_is_busy():
    # Found live: a false wake from a lecture playing aloud kept the voice loop thinking for
    # 4.7 s, and the dictation waited behind it until the words were gone.
    started = []

    class Flow:
        metrics: dict = {}

        def capture(self, read, stop, cancel, **k):
            started.append(time.monotonic())
            read()
            return b"\0" * 32000

        def finish(self, *a, **k):
            return {"status": "inserted"}

    coord = MicCoordinator()
    coord.assistant_capture()                          # the assistant "owns" a turn, reading nothing
    s, events, VS = _fake_session(Flow(), coord)
    t0 = time.monotonic()
    VS._dictation_worker(s)
    assert started and started[0] - t0 < 0.1
    assert coord.owner == "wake" and not s._mic_lock.locked() and not s._dict_busy.locked()


def test_the_wake_listener_waits_while_dictation_holds_the_microphone():
    from jarvis.audio.voice_session import VoiceSession
    lock = threading.Lock()
    reads = []
    s = SimpleNamespace(_mic_lock=lock, mic=SimpleNamespace(read=lambda: reads.append(1) or [0]))
    lock.acquire()
    t = threading.Thread(target=VoiceSession._read_frame, args=(s,), daemon=True)
    t.start()
    time.sleep(0.1)
    assert reads == []                                 # blocked behind dictation
    lock.release()
    t.join(1)
    assert reads == [1]


def test_only_one_dictation_at_a_time():
    class Flow:
        metrics: dict = {}

        def capture(self, *a, **k):
            raise AssertionError("second dictation must not start")

    coord = MicCoordinator()
    s, events, VS = _fake_session(Flow(), coord)
    s._dict_busy.acquire()
    VS._dictation_worker(s)                            # returns at once
    assert not events


def test_dictation_gives_the_microphone_back_even_when_it_crashes():
    import asyncio

    class Broken:
        metrics: dict = {}

        def capture(self, *a, **k):
            return b"\0" * 32000

        def finish(self, *a, **k):
            raise RuntimeError("service restarted mid-processing")

    coord = MicCoordinator()
    s, events, VS = _fake_session(Broken(), coord)
    VS._dictation_worker(s)
    assert coord.owner == "wake" and ("dictation", "error") in events


def test_dictation_audio_never_reaches_the_assistant():
    import asyncio

    class Flow:
        metrics = {"key_to_recording_ms": 40}

        def capture(self, *a, **k):
            return b"\0" * 32000

        def finish(self, *a, **k):
            return {"status": "inserted", "text": "open the door and delete everything"}

    coord = MicCoordinator()
    s, events, VS = _fake_session(Flow(), coord)
    VS._dictation_worker(s)
    timing = [t for k, t in events if k == "timing"][0]
    assert "open the door" not in timing and "status=inserted" in timing
    assert coord.owner == "wake"


def test_the_journal_never_gets_dictated_text(capsys, monkeypatch):
    import jarvis.__main__ as main
    monkeypatch.setattr(main, "_push_to_hud", lambda *a, **k: None)
    main._voice_event("dictation", json.dumps({"state": "preview", "preview": "rm -rf ~/private-notes"}))
    main._voice_event("dictation", json.dumps({"state": "level", "level": 0.4}))
    out = capsys.readouterr().out
    assert out.strip() == "dictation preview" and "private-notes" not in out
