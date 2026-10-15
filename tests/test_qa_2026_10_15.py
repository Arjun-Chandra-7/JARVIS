"""Regressions found by two hours of hands-on use on 2026-10-15 — each failed live before its fix."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.agent import gate
from jarvis.agent.action_claims import asks_for_an_action
from jarvis.brain import understand
from jarvis.brain.request import BrainRequest


# ---------------------------------------------------------------- "you trailed off"
@pytest.mark.parametrize("said", [
    "help me", "can you hear me", "can you help me", "explain this", "stop that", "repeat that",
    "what can you do for me", "who are you and what can you do for me?", "quiz me on this",
    "turn animations back on", "switch it off", "what was the last thing I asked you about?",
    "Any new emails I should know about?", "talk to me", "call him",
])
def test_complete_sentences_are_not_called_unfinished(said):
    assert gate.looks_unfinished(said) is False


@pytest.mark.parametrize("said", ["tell her that", "send a message to", "open the", "remind me to call mom at"])
def test_clipped_sentences_still_are(said):
    assert gate.looks_unfinished(said) is True


def test_the_echo_is_cut_at_a_word():
    said = gate.ask_for_the_rest("what was the last thing I asked you about the dog and the")
    echoed = said.split("“", 1)[1].split("”", 1)[0]
    assert echoed.startswith("…") and echoed[1:].split()[0] in "what was the last thing I asked you".split()


# ---------------------------------------------------------------- routing questions
def _intent(text):
    return understand.understand(BrainRequest(text=text, source="overlay")).intent


@pytest.mark.parametrize("text", [
    "How's the battery holding up?", "Give me a system status report — CPU, memory, GPU, disk.",
    "Any new emails I should know about?", "Pull up my schedule for today.", "Catch me up — what did I miss?",
    "Who's around me right now?", "What coding agents are running right now?",
])
def test_questions_about_the_owners_machine_go_to_tools(text):
    assert _intent(text) == "action"


@pytest.mark.parametrize("text", ["how are you doing today?", "I had a great day today",
                                  "What time is it, and what's the date?"])
def test_today_alone_is_not_web_research(text):
    assert _intent(text) != "research"


def test_the_news_still_is():
    assert _intent("What's the latest news on fusion energy?") == "research"


def test_note_taking_is_asking_for_something():
    assert asks_for_an_action("Note this down: arc reactor v2 needs palladium-free core.")
    assert not asks_for_an_action("do you remember my name?")


def test_live_facts_carry_the_time_and_owner(monkeypatch):
    from jarvis.brain import daily
    monkeypatch.setenv("JARVIS_OWNER_NAME", "Arjun")
    facts = daily.live_facts("what time is it?")
    assert "Right now it is" in facts and "Arjun" in facts


def test_local_weather_is_not_a_web_search():
    from jarvis.brain import daily
    assert daily.local_weather_question("what's the weather like?", "Bangalore")
    assert daily.local_weather_question("What's the weather like in Bangalore right now?", "Bangalore")
    assert not daily.local_weather_question("weather in Delhi tomorrow", "Bangalore")


# ---------------------------------------------------------------- open / play
@pytest.mark.parametrize("text", ["Jarvis, run a full diagnostic. Status on all systems.",
                                  "run a full diagnostic", "run the tests"])
def test_run_is_not_a_launch_unless_the_thing_exists(text, monkeypatch):
    from jarvis import open_command
    monkeypatch.setattr(open_command, "_resolves", lambda target: False)
    assert open_command.parse(text) is None


def test_play_music_goes_to_youtube(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from jarvis import open_command
    from jarvis.integrations import apps
    import jarvis.youtube_command as yt

    opened = []
    monkeypatch.setattr(open_command, "_resolves", lambda target: False)
    monkeypatch.setattr(yt, "top_results", lambda q, n=1, timeout=12.0: [("https://youtube.com/watch?v=x", "Back In Black")])
    monkeypatch.setattr(apps, "open_url", lambda url: opened.append(url) or True)
    reply = asyncio.run(open_command.handle("Play some AC/DC.", SimpleNamespace(browser_control=False)))
    assert opened == ["https://youtube.com/watch?v=x"] and "Back In Black" in reply


# ---------------------------------------------------------------- screen follow-ups
def test_screen_follow_up_needs_to_point_back():
    from jarvis import video_command as v
    v._LAST_SCREEN["qa"] = {"at": time.time(), "question": "q", "answer": "a",
                            "material": "Google results: full diagnostic. Status on all systems. Nicola"}
    assert v.follows_explanation("why is that?", "qa")
    assert v.follows_explanation("who is Nicola?", "qa")
    assert not v.follows_explanation("What coding agents are running right now?", "qa")
    assert not v.follows_explanation("Is Do Not Disturb on?", "qa")
    assert not v.follows_explanation("Catch me up — what did I miss?", "qa")


# ---------------------------------------------------------------- reminders and timers
IST = timezone(timedelta(hours=5, minutes=30))


def test_reminder_time_as_the_model_writes_it_is_read():
    from jarvis.jobs.reminders import _due, parse_due
    now = datetime(2026, 10, 15, 21, 40, tzinfo=IST)
    assert parse_due("2026-10-15 21:35 IST", now) == datetime(2026, 10, 15, 21, 35, tzinfo=IST)
    assert _due({"due": "2026-10-15 21:35 IST"}, now)
    assert not _due({"due": "2026-10-15 22:35 IST"}, now)
    assert parse_due("not a time", now) is None


def test_an_unreadable_reminder_time_is_refused(tmp_path):
    from jarvis.jobs import reminders
    with pytest.raises(ValueError):
        reminders.add(tmp_path, "next tuesday-ish", "x")
    item = reminders.add(tmp_path, "2026-10-15 21:35 IST", "Check the oven")
    assert item["due"].startswith("2026-10-15T21:35")


def test_a_late_reminder_says_when_it_was_due(tmp_path, monkeypatch):
    from jarvis.jobs import reminders
    said = []
    monkeypatch.setattr(reminders, "_fire", said.append)
    (tmp_path / "Jarvis").mkdir()
    reminders._save(tmp_path, [{"id": 1, "due": "2000-01-01 09:00 IST", "text": "Check the oven", "fired": False}])
    reminders.check_now(tmp_path)
    assert said and "missed" in said[0] and "Check the oven" in said[0]


def test_announcements_cross_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    from jarvis.jobs import announce_spool as s
    s.post("Timer finished: tea")
    s.post("stale", now=time.time() - 3600)
    assert s.take() == ["Timer finished: tea"]
    assert s.take() == []


# ---------------------------------------------------------------- researcher
def test_researcher_seen_list_keeps_its_order(tmp_path, monkeypatch):
    from jarvis.agent import ai_researcher as r
    monkeypatch.setattr(r, "_SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(r, "_SEEN_KEEP", 3)
    r._save_seen(dict.fromkeys(["a", "b", "c", "d", "e"]))
    assert json.loads((tmp_path / "seen.json").read_text()) == ["c", "d", "e"]


def test_researcher_writes_a_few_reports_per_cycle(tmp_path, monkeypatch):
    from jarvis.agent import ai_researcher as r
    monkeypatch.setattr(r, "_SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(r, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(r, "DAILY_DIR", tmp_path / "reports" / "daily")
    monkeypatch.setattr(r, "_REPORT_SPACING_S", 0)
    items = [{"id": f"p{i}", "title": f"P{i}", "source": "Hugging Face Daily", "score": 9} for i in range(10)]
    monkeypatch.setattr(r, "_fetch_arxiv_papers", lambda: [])
    monkeypatch.setattr(r, "_fetch_huggingface_daily", lambda: items)
    wrote = []
    monkeypatch.setattr(r, "_write_important_report", lambda item: wrote.append(item["id"]))
    r._research_cycle()
    assert wrote == ["p0", "p1", "p2"]
    r._research_cycle()
    assert wrote == ["p0", "p1", "p2", "p3", "p4", "p5"]          # the rest wait, none repeat


# ---------------------------------------------------------------- the rest
def test_wifi_current_survives_several_access_points(monkeypatch):
    from jarvis import nearby
    out = "Home:89:WPA2: \nHome:85:WPA2: \nHome:69:WPA2:*\nOther:70:WPA2: \n"
    monkeypatch.setattr(nearby, "_run", lambda cmd, t: out)
    rows = {d["ssid"]: d for d in nearby._wifi()}
    assert rows["Home"]["current"] is True and rows["Home"]["signal"] == 89
    assert rows["Other"]["current"] is False


def test_bluetooth_is_never_switched_on_by_a_scan(monkeypatch):
    from jarvis import nearby
    ran = []
    monkeypatch.setattr(nearby, "_run", lambda cmd, t: ran.append(cmd) or "")
    nearby._bt()
    assert not any(c[:2] in (["rfkill", "unblock"], ["bluetoothctl", "power"]) for c in ran)
    assert not any("scan" in c for c in ran)


def test_claude_code_plumbing_is_not_a_coding_job():
    from jarvis.integrations.coding_jobs import _NOT_A_JOB
    assert _NOT_A_JOB.search("/home/u/.local/bin/claude daemon run --json-path x")
    assert _NOT_A_JOB.search("claude bg-pty-host --bg-pty-host /tmp/x.sock 200 50 -- claude")
    assert _NOT_A_JOB.search("claude bg-spare --bg-spare /tmp/x.sock")
    assert not _NOT_A_JOB.search("claude")


def test_vault_commit_failure_is_reported(tmp_path):
    import subprocess

    from jarvis.memory import vault
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "note.md").write_text("x")
    lock = tmp_path / ".git" / "index.lock"
    lock.write_text("")
    assert vault.git_autocommit(tmp_path, "m") is False          # a fresh lock is respected
    old = time.time() - 7200
    import os
    os.utime(lock, (old, old))
    assert vault.git_autocommit(tmp_path, "m") is True           # a stale one is cleared
    assert not lock.exists()


def test_study_summary_is_words_not_a_dict():
    from jarvis.study.session import StudySession
    s = StudySession(subject="science", started_at=time.time() - 120)
    said = s.spoken_summary()
    assert "{" not in said and "science" in said


def test_speech_reads_latex_as_words():
    from jarvis.audio.speech_text import normalize
    said = normalize(r"So \[ 2400 \times 0.15 = 360 \]. [Jarvis/journal/2026-10-15.md]")
    assert "\\" not in said and "times" in said and "journal" not in said


def test_close_names_the_app_not_a_pattern():
    from jarvis import close_command as c
    assert c.parse("Close the calculator.") == "calculator"
    for not_an_app in ("close this tab", "close everything", "close the door", "close it"):
        assert c.parse(not_an_app) is None


def test_close_never_takes_the_desktop_down(monkeypatch):
    from types import SimpleNamespace

    from jarvis import close_command as c
    from jarvis.integrations import desktop_apps
    monkeypatch.setattr(desktop_apps, "resolve", lambda t: SimpleNamespace(name="Shell", binary="gnome-shell"))
    assert "won't close" in c.close("shell")
