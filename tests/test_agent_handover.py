"""Waiting for the answer, and changing agents before one runs dry."""
from __future__ import annotations

import time

from jarvis.coding import handover, roster, session, usage, watch


# ------------------------------------------------------------------ waiting for the answer
def _screen(text):
    return lambda: text


def test_nothing_is_announced_while_nothing_is_expected():
    watch.forget()
    assert watch.check(_screen("anything")) is None


def test_an_answer_is_not_announced_before_the_agent_has_started():
    watch.forget()
    watch.expect("Claude", "add a toggle")
    assert watch.check(_screen("thinking...")) is None
    watch.forget()


def test_a_screen_still_changing_is_not_finished():
    """All three agents redraw their prompt while still working, so a marker cannot be trusted —
    only the output going quiet."""
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at -= 60
    assert watch.check(_screen("step one")) is None
    assert watch.check(_screen("step two")) is None
    watch.forget()


def test_a_settled_screen_is_announced_once_and_then_forgotten():
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at -= 60
    screen = _screen("I refactored the parser into three functions and added tests for each.\n"
                     "Local: http://localhost:5173/")
    assert watch.check(screen) is None            # first sighting
    watch._waiting.last_change -= 60
    done = watch.check(screen)
    assert done is not None
    assert done.said.startswith("I refactored the parser")
    assert done.link == "http://localhost:5173/"
    assert watch.waiting_for() is None            # not announced twice
    assert watch.check(screen) is None


def test_per_prompt_completion_is_visible_to_the_process_watcher():
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at -= 60
    screen = _screen("I refactored the parser into three functions and added tests for each.")
    assert watch.check(screen) is None
    watch._waiting.last_change -= 60
    assert watch.check(screen) is not None
    assert watch.recently_finished("claude")
    assert not watch.recently_finished("codex")


def test_an_agent_still_going_after_a_quarter_of_an_hour_is_given_up_on():
    watch.forget()
    watch.expect("Claude", "work")
    watch._waiting.asked_at = time.time() - watch.GIVE_UP_AFTER_S - 1
    assert watch.check(_screen("still going")) is None
    assert watch.waiting_for() is None


# ------------------------------------------------------------------ changing agents
def test_the_agent_that_is_finishing_cannot_take_over_from_itself(monkeypatch):
    monkeypatch.setattr("jarvis.coding.quota.usable",
                        lambda *a, **k: {"claude": True, "codex": True, "agy": True})
    following = handover.next_agent("claude", "add a feature")
    assert following is not None and following[0].name == "codex"


def test_nobody_free_means_the_current_agent_keeps_going(monkeypatch):
    """Nearly empty beats nothing."""
    monkeypatch.setattr("jarvis.coding.quota.usable",
                        lambda *a, **k: {"claude": True, "codex": False, "agy": False})
    assert handover.next_agent("claude", "add a feature") is None


def test_the_agent_is_asked_to_leave_rather_than_killed():
    """Typed, so the session closes cleanly and anything it wanted to write gets written."""
    assert handover.GOODBYE["claude"] == "/exit"


def test_the_summary_question_is_short_because_there_is_little_left_to_answer_with():
    assert len(handover.SUMMARY_REQUEST) < 300
    assert "next agent" in handover.SUMMARY_REQUEST


def test_five_percent_is_the_line():
    assert usage.HAND_OVER_AT == 5.0
    assert usage.Left(5.0).nearly_out() is True
    assert usage.Left(6.0).nearly_out() is False


def test_a_conversation_keeps_its_model_across_turns():
    session.end()
    live = session.begin(roster.BY_NAME["claude"], "opus", "medium", "/tmp")
    live.touch()
    assert (session.current().model, session.current().effort) == ("opus", "medium")
    session.end()


def test_antigravity_aliases_match_in_recently_finished():
    watch.forget()
    watch.expect("Antigravity", "refactor")
    watch._waiting.asked_at -= 60
    screen = _screen("I fixed the async race condition in the loop.")
    assert watch.check(screen) is None
    watch._waiting.last_change -= 60
    done = watch.check(screen)
    assert done is not None
    # Both "agy" (from presence) and "Antigravity" (spoken) match
    assert watch.recently_finished("agy")
    assert watch.recently_finished("Antigravity")
    assert watch.recently_finished("gemini")
    assert not watch.recently_finished("claude")
    watch.forget()


def test_expect_resets_recently_finished():
    watch.forget()
    watch.expect("Claude", "prompt 1")
    watch._waiting.asked_at -= 60
    screen = _screen("Done with task one for today.")
    watch.check(screen)
    watch._waiting.last_change -= 60
    assert watch.check(screen) is not None
    assert watch.recently_finished("claude")

    # Expecting next prompt clears old recently_finished
    watch.expect("Claude", "prompt 2")
    assert not watch.recently_finished("claude")
    watch.forget()


def test_prompt_watcher_waits_while_agent_is_asking():
    watch.forget()
    watch.expect("Claude", "delete test folder")
    watch._waiting.asked_at -= 60
    asking_screen = _screen("Delete directory? (y/n)")
    assert watch.check(asking_screen) is None
    watch._waiting.last_change -= 60
    # Even after quiet interval, asking prompt must not finish
    assert watch.check(asking_screen) is None
    assert watch.waiting_for() == "Claude"

    # User answers and agent finishes
    done_screen = _screen("Directory deleted successfully and tests cleaned up.")
    assert watch.check(done_screen) is None
    watch._waiting.last_change -= 60
    done = watch.check(done_screen)
    assert done is not None
    assert "Directory deleted" in done.said
    watch.forget()


def test_voice_session_watchers_guarantee_single_announcement_antigravity(monkeypatch):
    import asyncio
    from jarvis.audio.voice_session import VoiceSession
    from jarvis.coding import presence

    async def run():
        class FakeSession:
            def __init__(self):
                self.spoken = []
                self.events = []

            def _speak(self, text: str, force: bool = False):
                self.spoken.append(text)

            def on_event(self, kind: str, text: str = ""):
                self.events.append((kind, text))

            _watch_coding_agents = VoiceSession._watch_coding_agents
            _watch_coding_terminal = VoiceSession._watch_coding_terminal

        session = FakeSession()
        watch.forget()
        presence.reset()

        # Fast-forward asyncio.sleep without recursion
        real_sleep = asyncio.sleep

        async def fast_sleep(_s):
            await real_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        monkeypatch.setattr("jarvis.coding.vscode.active_window", lambda: "1")
        monkeypatch.setattr("jarvis.coding.vscode.window", lambda: "2")

        # Jarvis expects Antigravity
        screen = "The refactoring was completed and all units pass."
        monkeypatch.setattr("jarvis.coding.terminal.tail", lambda _n: screen)
        watch.expect("Antigravity", "refactor")
        watch._waiting.asked_at -= 60

        # Presence watcher sees agy transition: running -> done
        states = [
            presence.State(kind="running", agent="agy"),
            presence.State(kind="done", agent="agy", completion_id="agy:1:1"),
        ]
        monkeypatch.setattr(presence, "look", lambda *a, **k: states.pop(0) if states else presence.State(kind="done", agent="agy", completion_id="agy:1:1"))

        agent_task = asyncio.create_task(session._watch_coding_agents())
        await real_sleep(0.01)
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

        # _watch_coding_agents must have suppressed its speech because Antigravity was expected
        assert session.spoken == []

        # Now terminal watcher runs
        watch._waiting.last_seen = screen
        watch._waiting.last_change -= 60

        term_task = asyncio.create_task(session._watch_coding_terminal())
        await real_sleep(0.01)
        term_task.cancel()
        try:
            await term_task
        except asyncio.CancelledError:
            pass

        # Terminal watcher delivered the spoken answer once as "Antigravity says: ..."
        assert len(session.spoken) == 1
        assert session.spoken[0].startswith("Antigravity says:")
        watch.forget()
        presence.reset()

    asyncio.run(run())


def test_voice_session_terminal_fallback_speech_when_no_prose(monkeypatch):
    import asyncio
    from jarvis.audio.voice_session import VoiceSession

    async def run():
        class FakeSession:
            def __init__(self):
                self.spoken = []
                self.events = []

            def _speak(self, text: str, force: bool = False):
                self.spoken.append(text)

            def on_event(self, kind: str, text: str = ""):
                self.events.append((kind, text))

            _watch_coding_terminal = VoiceSession._watch_coding_terminal

        session = FakeSession()
        watch.forget()
        real_sleep = asyncio.sleep

        async def fast_sleep(_s):
            await real_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        monkeypatch.setattr("jarvis.coding.vscode.active_window", lambda: "1")
        monkeypatch.setattr("jarvis.coding.vscode.window", lambda: "2")

        watch.expect("Claude", "run tests")
        watch._waiting.asked_at -= 60

        # Screen has no 6-word sentences (spoken_answer returns None) and no URL link
        screen = "OK\n12 passed"
        monkeypatch.setattr("jarvis.coding.terminal.tail", lambda _n: screen)
        watch._waiting.last_seen = screen
        watch._waiting.last_change -= 60

        task = asyncio.create_task(session._watch_coding_terminal())
        await real_sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert session.spoken == ["Claude has finished, sir."]
        watch.forget()

    asyncio.run(run())


def test_voice_session_terminal_watcher_finishes_before_agent_watcher(monkeypatch):
    import asyncio
    from jarvis.audio.voice_session import VoiceSession
    from jarvis.coding import presence

    async def run():
        class FakeSession:
            def __init__(self):
                self.spoken = []
                self.events = []

            def _speak(self, text: str, force: bool = False):
                self.spoken.append(text)

            def on_event(self, kind: str, text: str = ""):
                self.events.append((kind, text))

            _watch_coding_agents = VoiceSession._watch_coding_agents
            _watch_coding_terminal = VoiceSession._watch_coding_terminal

        session = FakeSession()
        watch.forget()
        presence.reset()
        real_sleep = asyncio.sleep

        async def fast_sleep(_s):
            await real_sleep(0.001)

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)
        monkeypatch.setattr("jarvis.coding.vscode.active_window", lambda: "1")
        monkeypatch.setattr("jarvis.coding.vscode.window", lambda: "2")

        watch.expect("Claude", "task")
        watch._waiting.asked_at -= 60
        screen = "The requested modifications have been implemented in the codebase."
        monkeypatch.setattr("jarvis.coding.terminal.tail", lambda _n: screen)
        watch._waiting.last_seen = screen
        watch._waiting.last_change -= 60

        # Terminal watcher runs FIRST
        term_task = asyncio.create_task(session._watch_coding_terminal())
        await real_sleep(0.01)
        term_task.cancel()
        try:
            await term_task
        except asyncio.CancelledError:
            pass

        assert len(session.spoken) == 1
        assert session.spoken[0].startswith("Claude says:")

        # Now agent watcher runs SECOND (transitions running -> done)
        states = [
            presence.State(kind="running", agent="claude"),
            presence.State(kind="done", agent="claude", completion_id="claude:1:1"),
        ]
        monkeypatch.setattr(presence, "look", lambda *a, **k: states.pop(0) if states else presence.State(kind="done", agent="claude", completion_id="claude:1:1"))

        agent_task = asyncio.create_task(session._watch_coding_agents())
        await real_sleep(0.01)
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

        # Agent watcher sees recently_finished("claude") and does NOT speak again
        assert len(session.spoken) == 1
        watch.forget()
        presence.reset()

    asyncio.run(run())
