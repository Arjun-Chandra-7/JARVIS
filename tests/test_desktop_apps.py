"""Resolving a spoken app name to an installed application."""
import pytest

from jarvis.integrations import desktop_apps as da


@pytest.fixture
def catalogue(monkeypatch):
    apps = [
        da.App(entry_id="opera-gx", name="Opera GX", exec_cmd="/usr/bin/opera-gx %U",
               generic="Web Browser", keywords=["Internet", "WWW", "Browser"]),
        da.App(entry_id="opera", name="Opera", exec_cmd="/usr/bin/opera %U",
               generic="Web Browser", keywords=["Internet", "Browser"]),
        da.App(entry_id="code_code", name="Visual Studio Code", exec_cmd="/usr/bin/code %F",
               generic="Text Editor", keywords=["vscode", "editor"]),
        da.App(entry_id="spotify_spotify", name="Spotify", exec_cmd="spotify %U",
               generic="Music Player"),
        da.App(entry_id="org.gnome.Nautilus", name="Files", exec_cmd="nautilus --new-window %U",
               generic="File Manager"),
        da.App(entry_id="org.gnome.Settings", name="Settings", exec_cmd="gnome-control-center"),
    ]
    monkeypatch.setattr(da, "installed", lambda refresh=False: apps)
    return apps


@pytest.mark.parametrize("spoken,expected", [
    ("opera gx", "Opera GX"),
    ("Opera GX", "Opera GX"),
    ("open opera gx", "Opera GX"),
    ("visual studio code", "Visual Studio Code"),
    ("spotify", "Spotify"),
    ("files", "Files"),
    ("settings", "Settings"),
])
def test_spoken_names_resolve(catalogue, spoken, expected):
    app = da.resolve(spoken)
    assert app is not None and app.name == expected


def test_specific_name_beats_the_shorter_one(catalogue):
    """'opera gx' must not resolve to plain Opera — both start with the same word."""
    assert da.resolve("opera gx").entry_id == "opera-gx"


def test_plain_opera_prefers_gx_by_alias(catalogue):
    """Opera GX is the browser on this machine, so the bare word should mean it."""
    assert da.resolve("opera").entry_id == "opera-gx"


def test_vs_code_alias(catalogue):
    """'vs code' appears in no .desktop Name field."""
    assert da.resolve("vs code").name == "Visual Studio Code"


def test_unknown_app_returns_none(catalogue):
    assert da.resolve("blender") is None
    assert da.resolve("") is None


def test_filler_words_are_ignored(catalogue):
    assert da.resolve("the spotify app please").name == "Spotify"


def test_candidates_suggest_near_misses(catalogue):
    assert "Opera GX" in da.candidates("oper")


# ----------------------------------------------------------------- websites vs applications
@pytest.mark.parametrize("name", ["netflix", "youtube", "github.com", "hotstar"])
def test_websites_are_recognised_as_such(name):
    assert da.looks_like_a_website(name)


@pytest.mark.parametrize("name", ["opera gx", "vs code", "settings"])
def test_applications_are_not_websites(name):
    assert not da.looks_like_a_website(name)


def test_an_installed_app_wins_over_the_same_named_website(catalogue, monkeypatch):
    """Spotify is both an installed app and a website; asked to open it, the app is meant.

    The website check is only consulted once app resolution has already failed, which is what
    keeps this unambiguous.
    """
    assert da.looks_like_a_website("spotify")          # it is also a site
    monkeypatch.setattr(da, "launch", lambda app: True)
    res = da.open_app("spotify")
    assert res["ok"] and res["name"] == "Spotify"


def test_website_request_redirects_to_the_browser(catalogue):
    """'open netflix' routes here about as often as to the browser; it must hand over, not fail."""
    res = da.open_app("netflix")
    assert not res["ok"]
    assert res.get("website")
    assert "browser_open" in res["message"]


def test_unknown_app_suggests_alternatives(catalogue):
    res = da.open_app("operaa")
    assert not res["ok"]
    assert "Opera" in res["message"]


def test_launch_is_reported_honestly(catalogue, monkeypatch):
    monkeypatch.setattr(da, "launch", lambda app: False)
    res = da.open_app("spotify")
    assert not res["ok"]
    assert "could not start" in res["message"].lower()


def test_successful_launch_names_the_app(catalogue, monkeypatch):
    monkeypatch.setattr(da, "launch", lambda app: True)
    res = da.open_app("opera gx")
    assert res["ok"] and res["name"] == "Opera GX"


# ----------------------------------------------------------------- .desktop parsing
def test_parses_a_desktop_entry(tmp_path):
    entry = tmp_path / "thing.desktop"
    entry.write_text(
        "[Desktop Entry]\nType=Application\nName=The Thing\nExec=/usr/bin/thing %U\n"
        "GenericName=Doer\nKeywords=do;stuff;\n\n"
        "[Desktop Action New]\nName=New Window\nExec=/usr/bin/thing --new\n",
        encoding="utf-8")
    app = da._parse(entry)
    assert app is not None
    assert app.name == "The Thing"          # not "New Window" from the action below
    assert app.binary == "thing"
    assert "stuff" in app.keywords


def test_hidden_entries_are_skipped(tmp_path):
    entry = tmp_path / "hidden.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=Hidden\nNoDisplay=true\n",
                     encoding="utf-8")
    assert da._parse(entry) is None


def test_non_application_entries_are_skipped(tmp_path):
    entry = tmp_path / "link.desktop"
    entry.write_text("[Desktop Entry]\nType=Link\nName=A Link\nURL=https://x.com\n",
                     encoding="utf-8")
    assert da._parse(entry) is None


# ----------------------------------------------------------------- spoken titles
@pytest.mark.parametrize("spoken", ["f.r.i.e.n.d.s", "F.R.I.E.N.D.S", "w.a.n.d.a.v.i.s.i.o.n"])
def test_spelled_out_title_is_never_an_app(catalogue, spoken):
    """It normalises to single letters, which matched almost any name — "open f.r.i.e.n.d.s"
    launched Easy Effects."""
    assert da.resolve(spoken) is None
    assert da.looks_like_a_website(spoken)


def test_spelled_out_title_is_sent_to_the_browser(catalogue):
    res = da.open_app("f.r.i.e.n.d.s")
    assert not res["ok"] and res.get("website")
    assert "browser_open" in res["message"]


def test_single_letters_do_not_match_an_app_name(catalogue):
    """The all-words rule ignores fragments shorter than three characters."""
    assert da._score(catalogue[0], "f r i e n d s") == 0


def test_a_show_title_is_not_suggested_as_an_app(catalogue):
    """0.6 similarity offered "Files" for "friends" (0.67) and the model acted on it."""
    assert "Files" not in da.candidates("friends")


def test_close_misspellings_are_still_suggested(catalogue):
    assert "Spotify" in da.candidates("spotifyy")


# ----------------------------------------------------------------- starting the browser
@pytest.fixture
def browser_state(monkeypatch):
    """A browser that is not running, recording how it gets started."""
    from jarvis.integrations import browser

    started: list[str] = []

    def control(url="", wait_s=12.0):
        started.append("control")
        return True

    def desktop(*args, **kwargs):
        started.append("desktop")
        return object()

    monkeypatch.setattr(browser, "is_running", lambda: False)
    monkeypatch.setattr(browser, "launch", control)
    monkeypatch.setattr(da.subprocess, "Popen", desktop)
    monkeypatch.setattr(da.shutil, "which", lambda name: f"/usr/bin/{name}")
    return started


def test_opening_the_browser_starts_it_under_control(catalogue, browser_state):
    """Started the desktop's way it has no DevTools port, and regaining one costs the user
    their tabs — which is how "play the latest X on YouTube" ended in "restart Opera"."""
    assert da.launch(da.resolve("opera gx"))
    assert browser_state == ["control"]


def test_other_apps_are_started_the_desktop_way(catalogue, browser_state):
    assert da.launch(da.resolve("spotify"))
    assert browser_state == ["desktop"]


def test_a_running_browser_is_not_restarted(catalogue, browser_state, monkeypatch):
    """Restarting closes the user's tabs, so it stays their decision."""
    from jarvis.integrations import browser

    monkeypatch.setattr(browser, "is_running", lambda: True)
    assert da.launch(da.resolve("opera gx"))
    assert browser_state == ["desktop"]


def test_control_disabled_leaves_the_browser_alone(catalogue, browser_state, monkeypatch):
    from jarvis.config import CONFIG

    monkeypatch.setattr(CONFIG, "browser_control", False)
    assert da.launch(da.resolve("opera gx"))
    assert browser_state == ["desktop"]
