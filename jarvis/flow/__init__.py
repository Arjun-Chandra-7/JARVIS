"""Voice writing: hold a key, speak, and clean text lands in whatever field has the cursor.

    keys.py        the global shortcut (evdev: works under GNOME Wayland for every app)
    mic.py         who owns the microphone — wake listening, the assistant, or dictation
    stt.py         speech to text, cloud first with a local fallback, raw transcript kept
    cleanup.py     deterministic cleanup: fillers, false starts, corrections, punctuation, lists
    commands.py    "new paragraph", "delete last sentence", "make this formal" — edits, not text
    dictionary.py  the person's own words: names, projects, spellings
    profiles.py    how text is shaped for chat, email, documents, code, terminals, search boxes
    focus.py       the focused field, read and written through AT-SPI (never a password)
    insert.py      putting text at the cursor, verified, with the clipboard put back
    history.py     what was dictated, for "copy that again" and "retry" — local, short-lived
    engine.py      the whole path from key press to verified text
"""
