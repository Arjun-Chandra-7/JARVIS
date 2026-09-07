"""Cross-session conversation memory for the local/API brain."""
import pytest

from jarvis.agent.factory import make_agent
from jarvis.config import Config


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_BRAIN", "ollama")
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path / "vault"))
    yield


def test_history_is_saved_and_restored_into_a_fresh_agent():
    a = make_agent(Config(), mode="text")
    a.messages += [
        {"role": "user", "content": "we're building the mobile remote today"},
        {"role": "assistant", "content": "Understood — pairing then Tailscale."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "x", "type": "function",
                                                            "function": {"name": "recall", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "x", "content": "nothing"},
    ]
    a._save_history()

    b = make_agent(Config(), mode="text")            # simulates a restart
    texts = [m["content"] for m in b.messages if m["role"] in ("user", "assistant")]
    assert "we're building the mobile remote today" in texts
    assert "Understood — pairing then Tailscale." in texts
    assert b.messages[0]["role"] == "system"          # system prompt still first
    # tool_calls / tool messages are never persisted (they'd break the API on reload)
    assert all("tool_calls" not in m for m in b.messages)
    assert not any(m["role"] == "tool" for m in b.messages)


def test_restore_is_bounded():
    a = make_agent(Config(), mode="text")
    for i in range(40):
        a.messages.append({"role": "user", "content": f"msg {i}"})
    a._save_history()
    b = make_agent(Config(), mode="text")
    restored = [m for m in b.messages if m["role"] in ("user", "assistant")]
    assert 0 < len(restored) <= 16
    assert restored[-1]["content"] == "msg 39"        # keeps the most recent
