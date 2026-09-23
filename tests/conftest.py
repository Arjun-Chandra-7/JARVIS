import pytest


@pytest.fixture(autouse=True)
def _no_leftover_approvals():
    """The approval manager is process-wide; a test must never inherit another's pending action."""
    from jarvis.approvals import MANAGER
    MANAGER.clear()
    yield
    MANAGER.clear()


@pytest.fixture(autouse=True)
def _no_real_youtube(monkeypatch):
    """No test fetches captions from YouTube or finds a real browser tab, and no transcript is
    remembered from one test to the next."""
    from jarvis import screen_context
    from jarvis.screen import page, youtube
    youtube._CACHE.clear()
    monkeypatch.setattr(youtube, "ytdlp_transcript", lambda *_a, **_k: ([], ""))
    monkeypatch.setattr(page, "video_page", lambda: page.active_page())
    monkeypatch.setattr(screen_context, "active_window", lambda: ("", ""))
    monkeypatch.setattr(screen_context, "front_tab", lambda *_a, **_k: None)
    monkeypatch.setattr(screen_context, "read_app", lambda *_a, **_k: ("", ""))
    yield
    youtube._CACHE.clear()


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch, tmp_path_factory):
    """No test talks to a model provider, and none writes the real provider-health breaker file.

    A fixture that patched the wrong function once sent test prompts to a live model; this makes
    that fail loudly instead."""
    def refuse(*_a, **_k):
        raise RuntimeError("network model call attempted in a test")

    monkeypatch.setattr("jarvis.llm._ask", refuse)
    import os
    if "JARVIS_STATE_DIR" not in os.environ:
        monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path_factory.mktemp("state")))
