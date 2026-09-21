"""Study blocking and clean coding setup commands."""

from __future__ import annotations

from jarvis.modes import coding_setup, study


def test_study_blocks_distractions_and_youtube_shorts():
    assert study._looks_distracting("https://www.netflix.com/watch/42", "Film")
    assert study._looks_distracting("https://www.instagram.com/reels/", "Instagram")
    assert study._looks_distracting("https://www.youtube.com/shorts/abc", "CBSE Class 10")
    assert study._looks_distracting("https://www.youtube.com/watch?v=abc", "Comedy clip - YouTube")
    assert not study._looks_distracting("https://www.youtube.com/watch?v=abc", "CBSE Class 10 Maths Chapter 1 - YouTube")
    assert not study._looks_distracting("https://notyoutube.com/watch?v=abc", "Comedy")


def test_study_waits_for_video_title_before_deciding(monkeypatch):
    study._pending_video_titles.clear()
    clock = [100.0]
    monkeypatch.setattr(study.time, "monotonic", lambda: clock[0])
    url = "https://www.youtube.com/watch?v=new"
    assert not study._looks_distracting(url, "YouTube")
    clock[0] = 109.0
    assert study._looks_distracting(url, "YouTube")
    assert not study._looks_distracting(url, "NCERT Science Lecture - YouTube")


def test_study_mode_is_shared_between_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    study.stop()
    started = study.start()
    study._on = None  # another process has no in-memory Session
    assert study.on()
    assert study.session().started == started.started
    assert study.stop() is not None
    assert not study.on()


def test_coding_setup_is_an_explicit_command():
    assert coding_setup.asked_to_start("Jarvis, open my coding setup")
    assert not coding_setup.asked_to_start("What is my coding setup?")


def test_passive_coding_watch_never_copies_terminal_or_speaks(monkeypatch):
    import asyncio
    from jarvis.audio.voice_session import VoiceSession
    from jarvis.coding import presence, terminal

    class Session:
        _watch_coding_agents = VoiceSession._watch_coding_agents

        def __init__(self):
            self.spoken = []
            self.events = []

        def _speak(self, words):
            self.spoken.append(words)

        def on_event(self, kind, text):
            self.events.append((kind, text))

    async def run():
        real_sleep = asyncio.sleep
        async def one_tick(_seconds):
            await real_sleep(0)
        monkeypatch.setattr(asyncio, "sleep", one_tick)
        monkeypatch.setattr(terminal, "tail", lambda *_: (_ for _ in ()).throw(
            AssertionError("passive watcher copied the terminal")))
        monkeypatch.setattr(presence, "look", lambda *args: presence.State(
            kind="done", agent="claude", completion_id="claude:1:1"))
        monkeypatch.setattr(presence, "drain_events", lambda: [presence.Event(
            kind="done", agent="claude", completion_id="claude:1:1")])
        watched = Session()
        task = asyncio.create_task(watched._watch_coding_agents())
        await real_sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert watched.spoken == []
        assert watched.events

    asyncio.run(run())


def test_claude_stop_hook_records_one_event_per_assistant_message(monkeypatch, tmp_path):
    import json
    from jarvis.coding import claude_stop

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"id": "msg-1"}}) + "\n")
    payload = {"session_id": "session-1", "prompt_id": "prompt-1",
               "hook_event_name": "Stop", "transcript_path": str(transcript)}
    assert claude_stop.record(payload)
    assert not claude_stop.record(payload)
    events, position = claude_stop.read_since(0)
    assert [event["id"] for event in events] == ["session-1:prompt-1"]
    assert claude_stop.read_since(position)[0] == []
    payload["prompt_id"] = "prompt-2"
    assert claude_stop.record(payload)
    payload["prompt_id"] = "prompt-3"
    payload["background_tasks"] = [{"id": "task-1", "status": "running"}]
    assert not claude_stop.record(payload)


def test_study_opens_opera_and_submits_exam_prompt(monkeypatch):
    import asyncio
    from jarvis.modes import study_chat

    calls = []
    class Page:
        async def call(self, method, params=None):
            calls.append((method, params))

        async def js(self, script):
            return len(study_chat.PROMPT_FILE.read_text()) if "textContent" in script else True

    class Socket:
        async def close(self):
            pass

    async def targets():
        return [{"id": "1", "url": "https://chatgpt.com/"}]

    async def connect(_target):
        return Socket()

    monkeypatch.setattr(study_chat.apps, "open_url", lambda *a, **k: "https://chatgpt.com/")
    monkeypatch.setattr(study_chat.browser, "_targets", targets)
    monkeypatch.setattr(study_chat.browser, "_connect", connect)
    monkeypatch.setattr(study_chat.browser, "_Session", lambda _ws: Page())
    assert asyncio.run(study_chat.open_and_prime())
    assert any(method == "Input.insertText" and "NCERT" in params["text"]
               for method, params in calls)


def test_voice_announces_one_real_claude_stop(monkeypatch, tmp_path):
    import asyncio
    from jarvis.audio.voice_session import VoiceSession
    from jarvis.coding import claude_stop

    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path))

    class Session:
        _watch_claude_stops = VoiceSession._watch_claude_stops

        def __init__(self):
            self.spoken = []

        def _speak(self, text):
            self.spoken.append(text)

        def on_event(self, _kind, _text):
            pass

    async def run():
        real_sleep = asyncio.sleep
        async def fast_sleep(_seconds):
            await real_sleep(0.001)
        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        watched = Session()
        task = asyncio.create_task(watched._watch_claude_stops())
        await real_sleep(0)
        assert claude_stop.record({"session_id": "s", "prompt_id": "p", "hook_event_name": "Stop"})
        await real_sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert watched.spoken == ["Claude has finished, sir."]

    asyncio.run(run())
