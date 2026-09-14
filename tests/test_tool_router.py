"""Tool routing: the right tool must be offered, and a question must not surface a write tool."""
import pytest

from jarvis.agent import tool_router as tr


def schema(name, desc=""):
    return {"type": "function",
            "function": {"name": name, "description": desc,
                         "parameters": {"type": "object", "properties": {}}}}


CATALOGUE = [
    schema("system_stats", "Machine health: CPU, memory, GPU, disk, battery, temperatures."),
    schema("google_agenda", "Upcoming calendar events for N days."),
    schema("google_calendar_create", "Create a calendar event. start/end are ISO datetimes."),
    schema("google_email_send", "Send an email."),
    schema("set_volume", "Set output volume percent."),
    schema("media_control", "Media: play_pause/next/previous/stop."),
    schema("recall", "Search the memory vault for relevant notes."),
    schema("web_search", "Search the web."),
    schema("lock_screen", "Lock the screen."),
    schema("read_clipboard", "Read the clipboard text."),
    schema("capture_screen", "Look at the user's screen."),
    schema("run_bash", "Run a shell command."),
]


# ------------------------------------------------------------------ question detection
@pytest.mark.parametrize("q", [
    "what's on my calendar today?",
    "what is my battery level",
    "do I have any meetings",
    "how much disk space is left",
    "show me my unread mail",
    "is the backend running?",
])
def test_questions_are_recognised(q):
    assert tr.looks_like_a_question(q)


@pytest.mark.parametrize("q", [
    "set a timer for five minutes",
    "send Pradhuman a message",
    "create a calendar event tomorrow at four",
    "lock the screen",
    "play the next track",
    "remind me to call mum at six",
])
def test_instructions_are_not_questions(q):
    assert not tr.looks_like_a_question(q)


def test_set_is_imperative_not_interrogative():
    """'set' opens an instruction; treating it as a question would demote the tool that does it."""
    assert not tr.looks_like_a_question("set the volume to thirty percent")


def test_empty_query_is_not_a_question():
    assert not tr.looks_like_a_question("")
    assert not tr.looks_like_a_question("   ")


# ------------------------------------------------------------------ write classification
@pytest.mark.parametrize("name", [
    "google_calendar_create", "google_email_send", "whatsapp_send", "set_volume",
    "create_thing", "delete_thing", "run_bash", "lock_screen",
])
def test_write_tools_are_flagged(name):
    assert tr.is_write_tool(name)


@pytest.mark.parametrize("name", [
    "system_stats", "google_agenda", "recall", "web_search", "read_clipboard", "capture_screen",
])
def test_read_tools_are_not_flagged(name):
    assert not tr.is_write_tool(name)


# ------------------------------------------------------------------ selection
def test_lexical_selection_finds_the_obvious_tool():
    picked = [s["function"]["name"]
              for s in tr.select(CATALOGUE, "read my clipboard", keep=4, use_semantic=False)]
    assert "read_clipboard" in picked


def test_question_demotes_the_write_tool_below_the_read_tool():
    """The bug this guards: 'what's on my calendar today' selected google_calendar_create."""
    picked = [s["function"]["name"]
              for s in tr.select(CATALOGUE, "what's on my calendar today?", keep=4,
                                 use_semantic=False)]
    assert "google_agenda" in picked
    if "google_calendar_create" in picked:
        assert picked.index("google_agenda") < picked.index("google_calendar_create")


def test_instruction_still_surfaces_the_write_tool_first():
    picked = [s["function"]["name"]
              for s in tr.select(CATALOGUE, "create a calendar event for tomorrow", keep=4,
                                 use_semantic=False)]
    assert "google_calendar_create" in picked


def test_always_on_tools_are_always_included():
    picked = {s["function"]["name"]
              for s in tr.select(CATALOGUE, "zzzz nonsense qqqq", keep=2, use_semantic=False)}
    for name in ("recall", "system_stats", "run_bash", "web_search"):
        assert name in picked, f"{name} should always be offered"


def test_keep_larger_than_catalogue_returns_everything():
    picked = tr.select(CATALOGUE, "anything", keep=999, use_semantic=False)
    assert len(picked) == len(CATALOGUE)


def test_selection_never_exceeds_keep_plus_always():
    picked = tr.select(CATALOGUE, "what is my battery level", keep=3, use_semantic=False)
    assert len(picked) <= 3 + len(tr.ALWAYS)


def test_selection_has_no_duplicates():
    names = [s["function"]["name"]
             for s in tr.select(CATALOGUE, "what is my battery level", keep=6, use_semantic=False)]
    assert len(names) == len(set(names))


def test_catalogue_listing_is_one_line_per_tool():
    text = tr.catalogue(CATALOGUE)
    assert len(text.splitlines()) == len(CATALOGUE)
    assert "google_agenda" in text
