import pytest


@pytest.fixture(autouse=True)
def _no_leftover_approvals():
    """The approval manager is process-wide; a test must never inherit another's pending action.
    Nor the conversation's current session: a test that spoke as "voice" left every later tool
    proposing under "voice" while the next test answered as "local"."""
    from jarvis import context
    from jarvis.approvals import MANAGER
    MANAGER.clear()
    context.set_current("local")
    yield
    MANAGER.clear()
    context.set_current("local")


@pytest.fixture(autouse=True)
def _no_real_study_state(monkeypatch, tmp_path_factory):
    """The Study Companion never reads or writes the real study folder, and no test inherits
    another's companion, quiz, renderer or screen reader."""
    monkeypatch.setenv("JARVIS_STUDY_DIR", str(tmp_path_factory.mktemp("study")))
    from jarvis import study_live
    study_live.use()
    yield
    study_live.use()


@pytest.fixture(autouse=True)
def _no_real_history(monkeypatch, tmp_path):
    """No test writes to the real conversation history. They did: the HUD's history file held
    dozens of turns from test runs — "Sent to the number ending 0001 on WhatsApp", made-up
    requests — mixed in with the person's own, and the HUD restores that file on reload."""
    from jarvis import hud_state
    monkeypatch.setattr(hud_state, "_HISTORY", tmp_path / "hud-history.jsonl")
    monkeypatch.setattr(hud_state, "_STATE_DIR", tmp_path)
    hud_state._RECENT.clear()


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

    # The Daily Brain: off unless a test turns it on (tests/brain does, with a fake server), its
    # settings and keys never the real ones, and every request through its adapters refused.
    import httpx
    from jarvis.brain import adapters, daily

    def no_network(request):
        raise RuntimeError(f"network model call attempted in a test: {request.url.host}")

    monkeypatch.setenv("JARVIS_DAILY_BRAIN", "0")
    monkeypatch.setenv("JARVIS_KEY_BACKEND", "memory")
    monkeypatch.setenv("JARVIS_BRAIN_CONFIG", str(tmp_path_factory.mktemp("brain") / "brain.json"))
    monkeypatch.setitem(adapters.TRANSPORT, "transport", httpx.MockTransport(no_network))
    monkeypatch.setitem(daily._BRAIN, "b", None)


@pytest.fixture(autouse=True)
def _no_real_away_mode(monkeypatch, tmp_path_factory):
    """Away mode in a test never touches the real vault, the real WhatsApp bridge or the desktop.

    Activating it starts the backend loop, which polls the live bridge on localhost — the one the
    owner's own Jarvis uses. A test must never be one poll away from replying to a real person."""
    from jarvis.away_mode import daemon, escalation, session
    root = tmp_path_factory.mktemp("away")
    import hashlib
    monkeypatch.setattr(session, "state_dir",
                        lambda config: root / hashlib.sha256(str(getattr(config, "vault_path", "")).encode()).hexdigest()[:10])
    monkeypatch.setattr(daemon, "start", lambda config: None)
    monkeypatch.setattr(escalation, "desktop", lambda *a, **k: True)
    monkeypatch.setattr(escalation, "phone_ping", lambda *a, **k: True)
    monkeypatch.delenv("JARVIS_DRY_RUN_SENDS", raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_real_settings_or_repairs(monkeypatch, tmp_path_factory):
    """Settings changes in a test never reach the running overlay or voice, and no test starts a
    repair worker, a coding agent or a systemd unit, or writes to the real repairs directory.

    Tests that exercise a component's report write it themselves; tests that run a repair
    pipeline build one explicitly with a recipe editor and stand-in services."""
    # Always a fresh state dir, even when the environment names one: a repair's sandbox sets
    # JARVIS_STATE_DIR, and without this every test inside it shared one job store.
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path_factory.mktemp("state")))
    run_dir = tmp_path_factory.mktemp("run")
    monkeypatch.setenv("JARVIS_RUNTIME_DIR", str(run_dir))   # not XDG_RUNTIME_DIR: audio needs the real one
    monkeypatch.setenv("JARVIS_SETTINGS_EMIT", "0")
    from jarvis.settings import runtime as settings_runtime

    monkeypatch.setitem(settings_runtime.DEFAULT_TIMEOUT, "overlay", 0.3)
    monkeypatch.setitem(settings_runtime.DEFAULT_TIMEOUT, "voice", 0.3)
    monkeypatch.setenv("JARVIS_REPAIR_ROOT", str(tmp_path_factory.mktemp("repairs")))
    monkeypatch.setenv("JARVIS_REPAIR_EDITOR", "none")
    monkeypatch.setenv("JARVIS_REPAIR_LAUNCH", "process")
    monkeypatch.delenv("JARVIS_SELF_REPAIR_DISABLED", raising=False)
    from jarvis.settings import live

    monkeypatch.setitem(live._state, "speed", None)
    launched: list = []
    monkeypatch.setattr("jarvis.selfrepair.command._launch", lambda job_id, action: launched.append((job_id, action)))
    yield launched
