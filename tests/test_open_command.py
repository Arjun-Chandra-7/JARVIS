"""«open X» is resolved without the model. Parsing decides what that layer claims, so it is
pinned here: too greedy and it hijacks ordinary conversation, too shy and nothing improves.
"""
import pytest

from jarvis import open_command as oc


@pytest.mark.parametrize("text,target", [
    ("open netflix", "netflix"),
    ("Open Netflix", "Netflix"),   # case preserved; site lookup lowercases
    ("open netflix.", "netflix"),
    ("open opera gx", "opera gx"),
    ("launch vs code", "vs code"),
    ("start spotify", "spotify"),
    ("play friends", "friends"),
    ("watch the office", "office"),
    ("put on netflix", "netflix"),
    ("open github.com", "github.com"),
    ("please open netflix now", "netflix"),
    ("open friends for me", "friends"),
])
def test_open_requests_are_recognised(text, target):
    assert oc.parse(text) == target


@pytest.mark.parametrize("text", [
    "what is my battery level",
    "how are you",
    "send Pradhuman a message",
    "what's on my calendar",
    "set the volume to 30",
    "",
    "   ",
])
def test_other_requests_are_left_to_the_model(text):
    assert oc.parse(text) is None


@pytest.mark.parametrize("text", [
    "open the door",
    "open a file",
    "open the window",
    "open my eyes",
    "open a new tab",
    "open the conversation",
    "open the pull request",
])
def test_figures_of_speech_are_not_launches(text):
    """These read as "open X" but launching something would be wrong."""
    assert oc.parse(text) is None


@pytest.mark.parametrize("text", ["open it", "open that", "play this", "open them"])
def test_pronouns_need_context_so_go_to_the_model(text):
    assert oc.parse(text) is None


def test_on_site_phrasing_is_kept_whole_for_the_runner():
    """"open Friends on Netflix" is split at run time, where the site table is available."""
    assert oc.parse("open Friends on Netflix") == "Friends on Netflix"


# ----------------------------------------------------------------- resolution order
class FakeApp:
    name = "Opera GX"


def test_an_installed_app_is_preferred(monkeypatch):
    import asyncio

    from jarvis.integrations import desktop_apps

    monkeypatch.setattr(desktop_apps, "resolve", lambda t: FakeApp())
    monkeypatch.setattr(desktop_apps, "launch", lambda app: True)
    out = asyncio.run(oc.run("opera gx", _config()))
    assert out == "Opened Opera GX."


def test_a_failed_launch_is_reported_honestly(monkeypatch):
    import asyncio

    from jarvis.integrations import desktop_apps

    monkeypatch.setattr(desktop_apps, "resolve", lambda t: FakeApp())
    monkeypatch.setattr(desktop_apps, "launch", lambda app: False)
    out = asyncio.run(oc.run("opera gx", _config()))
    assert "couldn't start" in out


def _config():
    class C:
        browser_control = False
    return C()


def test_without_browser_control_it_still_opens_a_url(monkeypatch):
    import asyncio

    from jarvis.integrations import apps, desktop_apps

    monkeypatch.setattr(desktop_apps, "resolve", lambda t: None)
    seen = {}
    monkeypatch.setattr(apps, "open_url", lambda url, **k: seen.setdefault("url", url) or url)
    out = asyncio.run(oc.run("netflix", _config()))
    assert "netflix" in seen["url"]
    assert "Opened" in out


def test_handle_returns_none_for_unrelated_text():
    import asyncio

    assert asyncio.run(oc.handle("what is my battery level", _config())) is None


# ----------------------------------------------------------------- imperfect speech
@pytest.mark.parametrize("text,target", [
    ("HR was open Netflix", "Netflix"),      # mis-heard wake word in front of the command
    ("uh open netflix", "netflix"),
    ("and then open spotify", "spotify"),
])
def test_a_run_up_before_the_verb_is_recovered(text, target):
    assert oc.parse_loose(text) == target


def test_the_loose_path_only_acts_on_something_real():
    """A garbled sentence must never launch something at random."""
    assert not oc._resolves("door")
    assert not oc._resolves("blahblah")
    assert oc._resolves("netflix")


def test_a_negated_sentence_is_not_launched():
    import asyncio

    loose = oc.parse_loose("I don't want to open the door")
    assert loose == "door"
    assert not oc._resolves(loose)       # so handle() declines it
    assert asyncio.run(oc.handle("I don't want to open the door", _config())) is None


# ----------------------------------------------------- the verb glued to the next word
@pytest.mark.parametrize("text,target", [
    ("HR was OpenNet Flix.", "Net Flix"),   # verbatim from a real transcript
    ("OpenNet Flix", "Net Flix"),
    ("OpenSpotify", "Spotify"),
    ("PlayFriends", "Friends"),
])
def test_a_glued_verb_is_split(text, target):
    assert (oc.parse_loose(text) or oc.parse(text)) == target


def test_ungluing_leaves_ordinary_words_alone():
    """Only a verb directly against a capitalised word is split, so nothing else moves."""
    assert oc.unglue("open Netflix") == "open Netflix"
    assert oc.unglue("reopen the file") == "reopen the file"
    assert oc.unglue("OpenAI") == "OpenAI"      # not Capital+lowercase, so untouched
