"""The specialists, and what each one is for.

One brain answered everything, which meant one system prompt trying to be a scheduler, a coder, a
research assistant and a friend at once, and one temperature for all of it. The local 3B model in
particular is much better at a narrow job than a broad one — the same model told it is only doing
one thing does that thing noticeably better.

What a specialist is, concretely: a name, the kinds of request it takes, the instruction it works
under, which model it prefers, how much room it needs, and how freely it is allowed to write. It
is *not* fifteen sets of weights. This machine has 957 MiB of video memory free with Ollama
already resident, so fifteen resident models is not a thing that can exist here; they share a
small pool, and what differs is the job each is given. The specialisation that matters for output
quality is the instruction and the tool set, not a separate download per role.

Adding one is adding an entry here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Specialist:
    name: str
    does: str                       # one line, shown when Jarvis says who handled something
    cues: tuple[str, ...]           # words that suggest this is the one
    instruction: str                # the system prompt it works under
    temperature: float = 0.3
    # None means "whatever the configured brain is". A specialist names a model only when the
    # difference actually matters for its job.
    model: Optional[str] = None
    tools: tuple[str, ...] = ()     # tool names worth putting in front of it, beyond the router's
    needs_vision: bool = False


ROSTER: tuple[Specialist, ...] = (
    Specialist(
        name="desk",
        does="short commands about this machine",
        cues=("open", "close", "launch", "volume", "brightness", "mute", "screenshot",
              "screen", "click", "type", "window", "app"),
        instruction=(
            "You carry out one action on this computer and report what happened. Use a tool or "
            "say plainly that you cannot. Never describe what you are about to do."
        ),
        temperature=0.0,
        tools=("open_app", "set_volume", "set_brightness", "find_and_click", "type_text"),
    ),
    Specialist(
        name="scribe",
        does="writing and rewriting text",
        cues=("write", "draft", "rewrite", "reword", "summarise", "summarize", "shorten",
              "expand", "proofread", "translate", "email", "message", "reply"),
        instruction=(
            "You write. Produce the text asked for and nothing else — no preamble, no offer to "
            "revise, no explanation of your choices. Match the register of the request."
        ),
        temperature=0.7,
    ),
    Specialist(
        name="coder",
        does="reading and writing code",
        cues=("code", "function", "bug", "error", "stack", "trace", "compile", "test",
              "refactor", "python", "javascript", "typescript", "rust", "sql", "regex"),
        instruction=(
            "You answer about code. Show the change, not a description of it. State assumptions "
            "once. If the question needs a file you have not seen, say which."
        ),
        temperature=0.1,
        tools=("read_file", "write_file", "list_dir", "read_project", "run_bash"),
    ),
    Specialist(
        name="researcher",
        does="questions that need looking things up",
        cues=("search", "look up", "find out", "who is", "what is", "when did", "latest",
              "news", "research", "compare", "price", "review"),
        instruction=(
            "You answer from sources. Search before answering anything you are not certain of, "
            "and say where the answer came from. Never present a guess as a finding."
        ),
        temperature=0.2,
        tools=("web_search", "deep_research", "browser_open"),
    ),
    Specialist(
        name="scheduler",
        does="time, calendar and reminders",
        cues=("calendar", "meeting", "schedule", "remind", "reminder", "timer", "alarm",
              "tomorrow", "today", "appointment", "free", "busy"),
        instruction=(
            "You handle time. Resolve every relative date against the current date before acting, "
            "and repeat the absolute date back so a mistake is visible."
        ),
        temperature=0.0,
        tools=("calendar_today", "set_reminder", "set_timer", "calendar_add"),
    ),
    Specialist(
        name="messenger",
        does="messages, mail and contacts",
        cues=("whatsapp", "message", "text", "send", "mail", "email", "gmail", "inbox",
              "contact", "call", "telegram"),
        instruction=(
            "You handle correspondence. Confirm who before sending anything, quote the message "
            "back, and never send on an assumed recipient."
        ),
        temperature=0.2,
        tools=("send_whatsapp", "send_email", "find_contact", "read_mail"),
    ),
    Specialist(
        name="librarian",
        does="what Jarvis already knows",
        cues=("remember", "remind me what", "did i", "my notes", "memory", "recall",
              "last time", "we discussed", "earlier"),
        instruction=(
            "You answer from what has been recorded. Quote it. If it is not there, say so rather "
            "than reconstructing it from likelihood."
        ),
        temperature=0.0,
        tools=("recall", "conversation_search", "memory_search"),
    ),
    Specialist(
        name="analyst",
        does="numbers, data and system state",
        cues=("how much", "how many", "usage", "battery", "disk", "memory", "cpu", "gpu",
              "stats", "average", "total", "percent"),
        instruction=(
            "You report figures. Give the number, its units and where it came from. Do not round "
            "away a difference that matters."
        ),
        temperature=0.0,
        tools=("system_stats", "get_battery", "run_bash"),
    ),
    Specialist(
        name="navigator",
        does="the web, in the browser",
        cues=("website", "page", "browser", "tab", "youtube", "netflix", "spotify",
              "scroll", "link", "url", "site"),
        instruction=(
            "You drive the browser. Act on the page in front of you, check that the page changed, "
            "and report what it says now."
        ),
        temperature=0.1,
        tools=("browser_open", "browser_click", "browser_read", "browser_type"),
    ),
    Specialist(
        name="artist",
        does="making pictures",
        cues=("draw", "sketch", "paint", "picture", "image", "generate", "illustration",
              "logo", "wallpaper", "render"),
        instruction=(
            "You make images. Turn the request into one clear visual description before "
            "generating, and say what you made."
        ),
        temperature=0.8,
        tools=("generate_image", "draw_on_canvas"),
    ),
    Specialist(
        name="watcher",
        does="what is on the screen right now",
        cues=("on my screen", "see", "look at", "reading", "what does it say", "this window"),
        instruction=(
            "You describe what is actually visible. Read the text before interpreting it, and "
            "separate what is on the screen from what you infer about it."
        ),
        temperature=0.1,
        needs_vision=True,
        tools=("capture_screen", "read_screen"),
    ),
    Specialist(
        name="planner",
        does="requests that are several steps",
        cues=("then", "after that", "first", "and then", "steps", "plan", "organise",
              "organize", "workflow", "every day"),
        instruction=(
            "You break a request into the fewest steps that do the job, each one checkable. "
            "Do not invent steps the request did not ask for."
        ),
        temperature=0.2,
    ),
    Specialist(
        name="tutor",
        does="explaining things",
        cues=("explain", "how does", "why does", "what does it mean", "difference between",
              "teach", "understand", "simply"),
        instruction=(
            "You explain. Start from what the asker already said they know. One idea per "
            "sentence. No analogies that need explaining themselves."
        ),
        temperature=0.4,
    ),
    Specialist(
        name="companion",
        does="ordinary conversation",
        cues=("how are you", "thanks", "hello", "good morning", "joke", "chat", "your day",
              "feel", "opinion"),
        instruction=(
            "You are talking, not executing. Be brief and human. Do not offer to perform tasks "
            "that were not asked for, and do not end every reply with a question."
        ),
        temperature=0.7,
    ),
    Specialist(
        name="guardian",
        does="anything that changes or deletes something",
        cues=("delete", "remove", "uninstall", "shutdown", "reboot", "kill", "format",
              "overwrite", "revoke", "reset"),
        instruction=(
            "You handle destructive requests. Say exactly what would be changed and what could "
            "not be undone, and do nothing until it has been confirmed in the same words."
        ),
        temperature=0.0,
    ),
)

BY_NAME = {s.name: s for s in ROSTER}
DEFAULT = BY_NAME["companion"]
