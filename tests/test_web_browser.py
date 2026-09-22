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


def test_only_chromium_can_be_driven():
    """Firefox dropped its partial CDP support for WebDriver BiDi, which is a different protocol
    and not one anything here speaks."""
    assert wb.can_be_driven("google-chrome") is True
    assert wb.can_be_driven("app.zen_browser.zen") is False


def test_an_undrivable_browser_says_why_and_names_itself():
    """"I can't do that" is useless; "Zen is Firefox-family" tells somebody what to change."""
    said = wb.why_not_drivable("app.zen_browser.zen")
    assert "Zen" in said
    assert "Firefox" in said
    assert "Chromium" in said


def test_a_drivable_browser_has_nothing_to_explain():
    assert wb.why_not_drivable("google-chrome") == ""


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
