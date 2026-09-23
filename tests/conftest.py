import pytest


@pytest.fixture(autouse=True)
def _no_leftover_approvals():
    """The approval manager is process-wide; a test must never inherit another's pending action."""
    from jarvis.approvals import MANAGER
    MANAGER.clear()
    yield
    MANAGER.clear()
