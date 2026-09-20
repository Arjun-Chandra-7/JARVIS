"""What gets said to Jarvis, and which tool should answer it.

The instrument for the router. With a hundred tools, routing is the thing that silently regresses
— a new tool's description overlaps an old one, the shortlist quietly stops containing the right
answer, and nothing fails. A test suite that never asserts on routing cannot see that happen.

Deliberately no LLM in the loop. 2026 work on LLM-as-judge found no judge uniformly reliable,
rankings shifting by up to fourteen places across benchmarks, and test-retest failures from
paraphrase alone. A labelled list and an exact assertion has none of those problems and costs
nothing to run.

The phrasing matters as much as the labels. These are written the way speech actually arrives —
clipped, hinglish-inflected, sometimes mistranscribed — because that is what the router sees.
Several are taken from the real history and failure journal rather than invented.
"""

from __future__ import annotations

# (what was said, the tool that should handle it)
#
# One tool per line, and only where there genuinely is one right answer. An utterance with two
# defensible tools teaches the router nothing and makes the measurement worse, so it is left out
# rather than labelled with a guess.
CORPUS: list[tuple[str, str]] = [
    # ---- screen and vision
    ("read my screen", "capture_screen"),
    ("what's on my screen right now", "capture_screen"),
    ("take a screenshot", "capture_screen"),
    ("what does this error on screen say", "capture_screen"),
    ("click the play button on my screen", "find_and_click"),
    ("click on the outline icon in vs code", "find_and_click"),
    ("press the sign in button", "find_and_click"),

    # ---- browser
    ("open youtube", "browser_open"),
    ("open netflix", "browser_open"),
    ("go to github dot com", "browser_open"),
    ("scroll down on this page", "browser_scroll"),
    ("scroll to the bottom", "browser_scroll"),
    ("what does this page say", "browser_read"),
    ("read this article to me", "browser_read"),

    # ---- apps and desktop
    ("open vs code", "open_app"),
    ("launch spotify", "open_app"),
    ("open the terminal", "open_app"),
    ("lock my screen", "lock_screen"),
    ("lock the laptop", "lock_screen"),
    ("turn the volume up", "set_volume"),
    ("make it louder", "set_volume"),
    ("mute the sound", "set_volume"),
    ("turn the brightness down", "set_brightness"),
    ("dim the screen a bit", "set_brightness"),
    ("pause the music", "media_control"),
    ("next song", "media_control"),
    ("skip this track", "media_control"),

    # ---- messaging and contacts
    ("message arnav that I'm running late", "message_person"),
    ("text mum I'll call her tonight", "message_person"),
    ("send a whatsapp to deepti", "whatsapp_send"),
    ("any new whatsapp messages", "whatsapp_inbox"),
    ("what did arnav say", "whatsapp_inbox"),
    ("check my instagram dms", "instagram_dms"),
    ("what's arnav's number", "find_contact"),

    # ---- google
    ("what's on my calendar today", "google_agenda"),
    ("what's my next meeting", "google_agenda"),
    ("do I have anything tomorrow morning", "google_agenda"),
    ("put lunch with sarah in my calendar at one", "google_calendar_create"),
    ("schedule a meeting friday at four", "google_calendar_create"),
    ("any new email", "google_email_check"),
    ("read me the latest email", "google_email_read"),
    ("send an email to my professor about the deadline", "google_email_send"),
    ("add finish the report to my tasks", "google_tasks_add"),
    ("what's on my todo list", "google_tasks_list"),

    # ---- time and reminders
    ("set a timer for ten minutes", "set_timer"),
    ("timer for five minutes", "set_timer"),
    ("remind me to call the dentist at four", "set_reminder"),
    ("remind me about the demo tomorrow", "set_reminder"),

    # ---- system and files
    ("how much memory am I using", "system_stats"),
    ("what's my cpu at", "system_stats"),
    ("is my battery low", "system_stats"),
    ("run git status", "run_bash"),
    ("what's in my downloads folder", "list_dir"),
    ("read the readme in this project", "read_file"),
    ("what's on my clipboard", "read_clipboard"),

    # ---- knowledge and memory
    ("what did we decide about the hackathon", "recall"),
    ("remember that my flight is on the fourth", "remember_automation"),
    ("what did I say about the logo last week", "conversation_search"),
    ("search the web for the new gemini pricing", "web_search"),
    ("look up what happened at the apple event", "web_search"),
    ("do some deep research on rust async runtimes", "deep_research"),

    # ---- pictures
    ("generate an image of iron man brushing his teeth", "generate_image"),
    ("make me a picture of a samurai in bamboo", "generate_image"),
    ("draw me a wallpaper of a red planet", "generate_image"),

    # ---- coding agents
    ("what are the coding agents doing", "check_coding_tasks"),
    ("is claude done yet", "check_coding_tasks"),

    # ---- presence and devices
    ("who's around", "who_is_around"),
    ("is anyone else in the room", "who_is_around"),
    ("what wifi networks can you see", "wifi_scan"),
    ("scan for bluetooth devices", "bluetooth_scan"),
]

# Utterances that must NOT reach a tool that changes anything. A question routed to a write tool
# is the failure mode that sends a message nobody asked for, and it is worth asserting separately
# from "did the right tool appear".
QUESTIONS_THAT_MUST_NOT_WRITE: list[str] = [
    "what's on my calendar today",
    "any new email",
    "what did arnav say",
    "how much memory am I using",
    "what's my next meeting",
    "is my battery low",
    "what's on my todo list",
    "what's on my clipboard",
]
