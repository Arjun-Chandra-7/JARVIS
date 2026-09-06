import asyncio
from pathlib import Path
from types import SimpleNamespace

from jarvis.agent import away, pa_daemon
from jarvis.integrations.chatgpt import ChatGPTSession
from jarvis.integrations import whatsapp


def cfg(tmp_path, **extra):
    values = {"vault_path": Path(tmp_path), "user_name": "sir", "brain": "chatgpt"}
    values.update(extra)
    return SimpleNamespace(**values)


def test_away_state_and_chatgpt_fallback_are_persisted(tmp_path):
    config = cfg(tmp_path)
    away.set_away("in class", config)
    reply = asyncio.run(away.respond(config, "1555@s.whatsapp.net", "Maya", "Can Arjun call me?"))
    assert "Arjun" in reply
    assert away.is_away(config)
    assert (config.vault_path / "Jarvis/private/away-state.json").exists()
    records = away.conversation_records(config, "1555@s.whatsapp.net")
    assert records["1555@s.whatsapp.net"][0]["content"] == "Can Arjun call me?"
    assert "key details" in reply


def test_injected_responder_receives_only_bounded_sender_history(tmp_path):
    config = cfg(tmp_path)
    received = []

    async def responder(messages):
        received.extend(messages)
        return "I have noted your message."

    away.set_away(config=config)
    away.set_responder(responder)
    try:
        assert asyncio.run(away.respond(config, "maya@s.whatsapp.net", "Maya", "Please call.")) == "I have noted your message."
    finally:
        away.set_responder(None)
    assert received[0]["role"] == "system"
    assert all("tool" not in str(item).lower() for item in received[1:])


def test_chatgpt_session_error_uses_neutral_template(tmp_path):
    config = cfg(tmp_path)

    async def unavailable(messages):
        return "[chatgpt] session is not running."

    away.set_away(config=config)
    away.set_responder(unavailable)
    try:
        reply = asyncio.run(away.respond(config, "maya@s.whatsapp.net", "Maya", "Hello"))
    finally:
        away.set_responder(None)
    assert "[chatgpt]" not in reply
    assert "Arjun" in reply


def test_auto_reply_filter_rejects_own_group_newsletter_and_status_messages():
    direct = {"id": "one", "from": "1555@s.whatsapp.net", "text": "hello", "fromMe": False}
    assert pa_daemon._eligible(direct)
    for change in (
        {"fromMe": True},
        {"from": "123@g.us"},
        {"from": "abc@newsletter"},
        {"from": "status@broadcast"},
        {"isGroup": True},
    ):
        candidate = {**direct, **change}
        assert not pa_daemon._eligible(candidate)


def test_message_claim_prevents_duplicate_daemon_replies(tmp_path):
    config = cfg(tmp_path)
    assert pa_daemon._claim_message(config, "same-message")
    assert not pa_daemon._claim_message(config, "same-message")


def test_isolated_chatgpt_tab_is_closed_without_a_browser(tmp_path):
    class Page:
        def __init__(self):
            self.closed = False
        async def goto(self, url, timeout):
            self.url = url
        async def wait_for_timeout(self, delay):
            self.delay = delay
        async def close(self):
            self.closed = True

    class Context:
        def __init__(self):
            self.page = Page()
        async def new_page(self):
            return self.page

    async def run():
        session = ChatGPTSession()
        session.ctx = Context()
        async def fake_ask(page, text, timeout):
            assert page is session.ctx.page
            assert "OTHER PERSON: hello" in text
            assert timeout == 12
            return "Noted."
        session._ask_page = fake_ask
        answer = await session.ask_isolated([
            {"role": "system", "content": "No tools."},
            {"role": "user", "content": "hello"},
        ], timeout_s=12)
        assert answer == "Noted."
        assert session.ctx.page.closed
        assert "temporary-chat=true" in session.ctx.page.url
    asyncio.run(run())


def test_import_context_is_local_only(tmp_path, monkeypatch):
    config = cfg(tmp_path)
    monkeypatch.setattr(whatsapp, "chats", lambda limit: [{"id": "m1", "text": "saved"}])
    outcome = whatsapp.import_context(config)
    assert outcome["ok"] and outcome["count"] == 1
    saved = Path(outcome["path"])
    assert saved.name == "whatsapp-context.json"
    assert "saved" in saved.read_text(encoding="utf-8")


def test_imported_context_requires_explicit_local_authorization(tmp_path, monkeypatch):
    config = cfg(tmp_path)
    monkeypatch.setattr(whatsapp, "chats", lambda limit: [{"id": "m1", "name": "Maya", "text": "Project deadline Friday"}])
    whatsapp.import_context(config)
    assert whatsapp.retrieve_imported_context(config, "deadline") == []
    assert whatsapp.retrieve_imported_context(config, "deadline", authorized=True)[0]["name"] == "Maya"
