import pytest


@pytest.fixture(autouse=True)
def _no_leftover_approvals():
    """The approval manager is process-wide; a test must never inherit another's pending action."""
    from jarvis.approvals import MANAGER
    MANAGER.clear()
    yield
    MANAGER.clear()


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
