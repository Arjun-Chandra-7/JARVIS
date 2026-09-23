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


_REAL_AUDIO_CHANGES = (
    ("wpctl", "set-mute"), ("wpctl", "set-volume"), ("wpctl", "set-default"),
    ("playerctl", "play"), ("playerctl", "pause"), ("playerctl", "play-pause"),
    ("playerctl", "next"), ("playerctl", "previous"), ("systemctl", "restart"),
)


@pytest.fixture(autouse=True)
def _no_real_audio_changes(monkeypatch):
    """No test changes the machine's real microphone, speakers or players.

    Found the hard way: a microphone-recovery test that did not stub the mute check ran the real
    `wpctl set-mute … 0` and unmuted the owner's microphone, which they had muted. Any such
    command now fails as if refused; a test that wants to see it happen mocks subprocess itself.
    """
    import subprocess

    real_run = subprocess.run

    def guarded(args, *a, **k):
        words = [str(w) for w in (args if isinstance(args, (list, tuple)) else str(args).split())]
        for program, verb in _REAL_AUDIO_CHANGES:
            if words and words[0].endswith(program) and verb in words[1:4]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="refused in tests")
        return real_run(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", guarded)
    yield


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
