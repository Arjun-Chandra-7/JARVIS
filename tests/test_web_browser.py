"""Which browser a page opens in, and whether it can be driven once it is there.

"opera" was written into a dozen call sites, so changing browsers did not change browsers — the
LinkedIn dashboard quietly stopped appearing because the call that opened it had a browser's name
baked into it.
"""

from __future__ import annotations

import subprocess

import pytest

from jarvis.integrations import web_browser as wb


@pytest.fixture(autouse=True)
def fresh():
    wb.forget()
    yield
    wb.forget()


def fake_xdg(answer, code=0):
    class Result:
        returncode = code
        stdout = answer
        stderr = ""

    return lambda *_a, **_k: Result()


# --------------------------------------------------------------------------- choosing one
def test_an_explicit_choice_wins(monkeypatch):
    monkeypatch.setenv("JARVIS_BROWSER", "brave-browser")
    monkeypatch.setattr(subprocess, "run", fake_xdg("firefox.desktop\n"))
    assert wb.preferred() == "brave-browser"


def test_otherwise_the_desktop_default_decides(monkeypatch):
    """Where somebody says they have switched browsers."""
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_xdg("app.zen_browser.zen.desktop\n"))
    assert wb.preferred() == "app.zen_browser.zen"


def test_the_desktop_suffix_is_not_part_of_the_name(monkeypatch):
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_xdg("firefox.desktop\n"))
    assert wb.preferred() == "firefox"


def test_with_no_answer_it_looks_for_something_installed(monkeypatch):
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_xdg("", code=1))
    monkeypatch.setattr(wb.shutil, "which", lambda n: "/usr/bin/x" if n == "firefox" else None)
    assert wb.preferred() == "firefox"


def test_nothing_installed_is_an_empty_answer_not_a_guess(monkeypatch):
    """Naming a browser that is not there sends every later call somewhere that cannot work."""
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_xdg("", code=1))
    monkeypatch.setattr(wb.shutil, "which", lambda _n: None)
    assert wb.preferred() == ""


# --------------------------------------------------------------------------- what it can do
@pytest.mark.parametrize("name,expected", [
    ("app.zen_browser.zen", "firefox"),
    ("zen", "firefox"),
    ("firefox", "firefox"),
    ("librewolf", "firefox"),
    ("opera-gx", "chromium"),
    ("google-chrome", "chromium"),
    ("brave-browser", "chromium"),
    ("chromium", "chromium"),
    ("lynx", ""),
])
def test_the_family_is_recognised_through_a_flatpak_id(name, expected):
    """A flatpak id is "app.zen_browser.zen", not "zen", so the whole string has to be read."""
    assert wb.family(name) == expected


def test_both_families_can_be_driven():
    """Chromium over the DevTools protocol, Firefox over Marionette. This used to say only
    Chromium, which was true until Marionette landed."""
    assert wb.can_be_driven("google-chrome") is True
    assert wb.can_be_driven("app.zen_browser.zen") is True


def test_a_browser_from_neither_family_cannot():
    assert wb.can_be_driven("lynx") is False


def test_something_unspeakable_says_which_protocols_are_known():
    """"I can't do that" is useless; naming the two that are known tells somebody what to
    switch to."""
    said = wb.why_not_drivable("lynx")
    assert "DevTools" in said and "Marionette" in said


def test_a_drivable_browser_has_nothing_to_explain():
    assert wb.why_not_drivable("google-chrome") == ""
    assert wb.why_not_drivable("app.zen_browser.zen") == ""


def test_each_family_is_told_how_to_switch_automation_on():
    """Separate question from "can it be driven": this one is "it could, but it is off", and the
    remedy differs by family — a restart for Firefox, an extension or a port for Chromium."""
    firefox = wb.how_to_enable("app.zen_browser.zen")
    assert "Zen" in firefox and "restor" in firefox      # it keeps your tabs, which is the point
    chromium = wb.how_to_enable("google-chrome")
    assert "extension" in chromium or "debug port" in chromium
    assert wb.how_to_enable("lynx") == ""


def test_the_answer_is_cached_but_forgettable(monkeypatch):
    """Cached because it shells out; forgettable because somebody may have just switched."""
    calls = []

    def counting(*_a, **_k):
        calls.append(1)

        class Result:
            returncode = 0
            stdout = "firefox.desktop"

        return Result()

    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "run", counting)
    wb.preferred()
    wb.preferred()
    assert len(calls) == 1
    wb.forget()
    wb.preferred()
    assert len(calls) == 2
