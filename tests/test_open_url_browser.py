"""Pages open in the browser that was chosen, even when it is a flatpak and not a program on PATH.

Found live: the preferred browser was Zen, "app.zen_browser.zen" — a flatpak id — `which` found
nothing for it, and open_url fell through to opera-gx. Every page Jarvis opened went to Opera.
"""
from jarvis.integrations import apps


def _machine(monkeypatch, on_path):
    spawned = []
    monkeypatch.setattr(apps.shutil, "which", lambda name: f"/usr/bin/{name}" if name in on_path else None)
    monkeypatch.setattr(apps, "_spawn", lambda argv: spawned.append(argv) or True)
    return spawned


def test_a_flatpak_default_browser_is_used_not_opera(monkeypatch):
    spawned = _machine(monkeypatch, {"flatpak", "opera-gx", "opera", "xdg-open"})
    assert apps.open_url("https://www.youtube.com", browser="app.zen_browser.zen")
    assert spawned == [["flatpak", "run", "app.zen_browser.zen", "https://www.youtube.com"]]


def test_an_unknown_browser_goes_to_the_desktop_default_before_opera(monkeypatch):
    spawned = _machine(monkeypatch, {"opera-gx", "xdg-open"})
    apps.open_url("https://example.com", browser="something-else")
    assert spawned[0][0] == "/usr/bin/xdg-open"
