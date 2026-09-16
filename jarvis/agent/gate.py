"""Decide what the person wants before reaching for anything that changes the world.

From the log, a normal evening of talking to Jarvis:

    you> See you, daddy.              jarvis> Message sent to Daddy.
    you> Say Daddy.                   jarvis> Your machine is running smoothly.
    you> dictation, go Don.           jarvis> Image generated. Ready.
    you> generate an image of a       jarvis> Image generated. Ready.

Nobody asked for a message to be sent. A small model handed eighty tools and a sentence it does
not understand will pick one, because picking one is what the shape of the turn suggests, and
"See you" is close enough to "send" for a 3B model in a hurry. The cost is not a bad answer, it
is an action nobody asked for.

Two gates, both narrow.

The first: when the turn does not ask for anything to be done, the tools that change the world
are not on the table at all. Reading tools stay, so "what's on my calendar" still works — the
problem was never questions, it was that chat could reach the send button.

The second: a sentence that stops mid-phrase — "generate an image of a" — is not a request, it
is the first half of one, and the answer is to ask rather than to guess the rest.

Read-only is an allow-list rather than a deny-list on purpose. Forgetting to list a reading tool
costs nothing worse than "I can't do that while we're chatting"; forgetting to list a writing one
sends a message to somebody.
"""

from __future__ import annotations

import re

# Tools that only look. Anything not named here is treated as capable of changing something.
READ_ONLY = {
    "analyze_image", "bluetooth_scan", "browser_read", "capture_screen", "catch_up",
    "check_coding_tasks", "check_pa_status", "contact_context", "conversation_search",
    "deep_research", "desktop_read", "find_contact", "get_activity_recordings",
    "get_brightness", "google_agenda", "google_email_check", "google_email_read",
    "google_tasks_list", "instagram_dms", "linkedin_networking", "linkedin_pending_drafts",
    "linkedin_read_draft", "linkedin_stats", "linkedin_top_ideas", "list_apps",
    "list_automations", "list_dir", "read_clipboard", "read_file", "read_project", "recall",
    "system_stats", "web_fetch", "web_search", "whatsapp_inbox", "whatsapp_scan",
    "who_is_around", "wifi_scan",
}


def is_read_only(tool_name: str) -> bool:
    return tool_name in READ_ONLY


# A sentence that stops before it has said what it is about. "Generate an image of a" ends on an
# article; "send a message to" ends on a preposition. Whisper produces these constantly when a
# phrase is clipped at the end of a capture window, and they are the requests most likely to be
# completed by a model's imagination rather than by the person.
_STOPS_MID_PHRASE = re.compile(
    r"""(?ix)\b(?:
        a | an | the | of | to | for | with | about | into | onto | from | at | by | on | in |
        and | or | but | that | this | my | your | some | any | it's | its |
        me | him | her | them | us
    )\s*$""")

# Two words or fewer is usually a greeting or a false start, not a request to do something.
_TOO_SHORT_TO_ACT_ON = 2


def looks_unfinished(text: str) -> bool:
    """True when the words stop before the request does."""
    said = (text or "").strip().rstrip(".!?,;:").strip()
    if not said:
        return False
    return bool(_STOPS_MID_PHRASE.search(said))


def wants_something_done(text: str) -> bool:
    """Whether this turn asks for an action, as opposed to being conversation."""
    from .action_claims import asks_for_an_action

    said = (text or "").strip()
    if not said or looks_unfinished(said):
        return False
    if len(said.split()) <= _TOO_SHORT_TO_ACT_ON and not _CLEARLY_A_COMMAND.match(said):
        return False
    return asks_for_an_action(said)


# Short but unmistakable: "open netflix", "stop", "louder". Kept separate so the two-word rule
# above does not throw away the commands people actually say in two words.
_CLEARLY_A_COMMAND = re.compile(
    r"""(?ix)^\s*(?:
        open | launch | start | play | watch | stop | pause | resume | close | quit |
        mute | unmute | louder | quieter | brighter | dimmer | next | previous |
        screenshot | sleep | wake | dictate | dictation | draw | undo | cancel
    )\b""")


# Talking, as opposed to asking for something. "Say Daddy" was answered with "Your machine is
# running smoothly" — the model had a system-stats tool in front of it and a sentence it could
# not place, so it read out the machine's health. With nothing on the table it has to use words.
_SMALL_TALK = re.compile(
    r"""(?ix)^\s*(?:
        (?:hi|hey|hello|yo|sup|hola)\b |
        good\s+(?:morning|afternoon|evening|night) |
        (?:thanks|thank\s+you|cheers|ta)\b |
        (?:bye|goodbye|see\s+you|see\s+ya|later|goodnight|night)\b |
        (?:how\s+(?:are|r)\s+(?:you|u)|how'?s\s+it\s+going|what'?s\s+up|you\s+(?:ok|alright|there))\b |
        (?:love\s+you|miss\s+you|i'?m\s+(?:back|home|tired|bored|sad|happy))\b |
        (?:nice|cool|great|awesome|lol|haha|nevermind|never\s+mind|forget\s+it)\b |
        (?:who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|are\s+you\s+(?:real|alive))\b |
        (?:sorry|my\s+bad|oops)\b |
        (?:ok(?:ay)?|yeah|yes|no|nope|yep|sure|right)\s*$
    )""")


def is_small_talk(text: str) -> bool:
    """True when the turn is conversation and nothing else."""
    said = (text or "").strip()
    # Long sentences that merely open with a greeting are requests: "hey jarvis, open netflix".
    return bool(_SMALL_TALK.match(said)) and len(said.split()) <= 8


def allowed(schemas: list[dict], user_text: str) -> list[dict]:
    """The tools this turn may use.

    Everything when something was asked for; only the ones that look, when not; and nothing at
    all when the turn is plainly conversation, because a model holding a tool will find a reason
    to use it.
    """
    if wants_something_done(user_text):
        return schemas
    if is_small_talk(user_text):
        return []
    return [s for s in schemas if is_read_only(s.get("function", {}).get("name", ""))]


def ask_for_the_rest(text: str) -> str:
    """What to say to a sentence that stopped halfway."""
    said = (text or "").strip().rstrip(".!?")
    return f"You trailed off after “{said[-40:].strip()}”, sir — what was the rest?"
