# Voice writing (system-wide dictation)

Put the cursor in any text field, press **Right Alt**, speak, and clean text appears there — in
English, Hindi (Devanagari) or Hinglish, in a browser, an editor, a terminal or a chat box.

## Using it

| What you do | What happens |
| --- | --- |
| Tap Right Alt | Hands-free: speak as long as you like, tap again to insert |
| Hold Right Alt | Push-to-talk: release to insert |
| Escape | Cancel: nothing is inserted, the clipboard and the field are untouched |
| Say "new paragraph", "delete the last sentence", "replace Friday with Monday" … as the *whole* utterance | Edits instead of text (see Commands) |
| In a terminal | The command is shown first; press Right Alt again to insert it. Enter is never pressed |
| "Jarvis, dictate" | The older voice-started mode; now uses the same cleanup and insertion |

A small capsule at the bottom of the screen shows *Listening → Processing → Inserted*, and says
plainly when something could not be confirmed or was refused. It never takes focus.

## How it works

```
Right Alt (evdev, jarvis/flow/keys.py)
  → the voice process hands the microphone over (jarvis/flow/mic.py)
      wake listening pauses; a capture in progress is preempted; Jarvis's speech is cut first
  → capture, with the last ~300 ms before the key (so a first word said early is kept)
      the focused field is read in parallel (scripts/atspi_focus_bridge.py)
  → key up / tap / Escape
  → speech to text (jarvis/flow/stt.py): Groq Whisper large-v3-turbo, local faster-whisper small
      fallback; password field → nothing is transcribed at all
  → command or text (jarvis/flow/commands.py)
  → cleanup for the field's profile (jarvis/flow/cleanup.py, jarvis/flow/profiles.py)
  → insertion, verified (jarvis/flow/insert.py)
  → history (jarvis/flow/history.py); the microphone goes back to the wake listener
```

Everything lives in the voice service (`jarvis-voice`); the capsule is part of the overlay.

### Insertion, in order

1. **AT-SPI** — insert at the caret through the field's EditableText, read back. GTK 4 fields
   report their length but read back empty; there, growing by exactly the text is the evidence.
2. **Clipboard** — the old clipboard is saved (while speech is still being recognised), the text
   pasted with Ctrl+V (Ctrl+Shift+V in a terminal), and the clipboard put back 250 ms later.
   Firefox/Zen do not update their accessible text after a paste, so there the page's own focused
   field is read over Marionette to confirm.
3. **Keystrokes** — ydotool, ASCII only (it cannot type Devanagari), never reported as verified.

A method that may have changed the field is never followed by another — that is how text appears
twice. An accessible insertion that changes nothing (Firefox says "ok" and does nothing in a
contenteditable) is looked at again 150 ms later before pasting.

### Profiles

| Profile | Where | Treatment |
| --- | --- | --- |
| messaging | WhatsApp, Telegram, Slack, Discord… | natural; no full stop on one line; Hindi in Roman letters |
| email | Gmail, Outlook, Thunderbird | paragraphs, full punctuation |
| document | editors, Docs, Notion, notes | paragraphs, lists |
| code | VS Code, JetBrains… | literal: "user underscore name equals none" → `user_name=none` |
| terminal | any terminal | literal, previewed, never followed by Enter; "Get status" → `git status` |
| search | search boxes, address bars | a query: "search for X" → `X` |
| prose | anything else | sentences |

Secret fields — password inputs, and fields labelled OTP, PIN, verification code, token, API key,
private key — are refused. Their audio is not transcribed and nothing is kept.

### Cleanup (deterministic, ~1–3 ms)

Fillers (um, uh, hmm), stutters on short words ("the the"), false starts ("I want to— I need to"),
corrections ("at five, actually make that six", "Friday—no, Saturday", "scratch that",
"nahi, instead parso", "ye hatao", "replace X with Y"), spoken punctuation ("comma", "full stop",
"new paragraph", "question mark"), spoken lists ("first point … second point …"), email
addresses ("riya at example dot com"), dictionary spellings, capitals, and `।` after Devanagari.

It is careful with meaning: "I actually liked it", "very very", "jaldi jaldi", "bye bye" and
"Tea? No, coffee." are left as they are. It never translates. No model is used for plain
dictation; the strong model is used only for the explicit rewrite commands.

### Commands (the whole utterance only)

new paragraph · new line · bullet list · numbered list · delete last word · delete the last
sentence · undo that · select last sentence · replace X with Y · make this formal · make this
shorter · fix grammar · translate this to English · write this in Hinglish · cancel / rehne do ·
copy last dictation · paste last dictation · retry · always spell this as X · add this name to my
dictionary · forget that correction · discard the last transcript · clear dictation history. Hinglish forms work too ("isko formal banao", "naya
paragraph"). Anything longer or looser than the grammar stays text.

Edits act on the selection, else on what was just dictated. Where the field cannot be addressed,
the new version is put on the clipboard instead of guessing where to type it.

## Personal dictionary

`~/.local/share/jarvis/dictation-dictionary.json` (0600). Seeded only with product words (JARVIS,
Viralyst, NCERT, Pythagoras, WhatsApp, GitHub…). Entries prime the recogniser and correct
spellings afterwards. Voice changes ("always spell this as Viralyst") wait for a key press to
confirm before anything is written.

## Privacy

* Audio lives in memory only and is dropped as soon as it is transcribed. Nothing is written.
* History (`~/.local/share/jarvis/dictation-history.jsonl`, 0600) keeps the raw and cleaned text
  for 24 h (JARVIS_DICTATION_HISTORY_HOURS), 200 rows at most; `JARVIS_DICTATION_HISTORY=off`
  keeps nothing. Say "clear dictation history" (or "discard the last transcript") to remove it.
* The service journal gets states and timings only — never what was said, never the preview.
  In the voice-started mode, transcripts are logged as "(dictated text)".
* Nothing from a password field is read, transcribed or kept.

## Measured on this machine

| Step | Target | Measured |
| --- | --- | --- |
| Key → recording | < 150 ms | 81–93 ms with the final design (137–158 ms before it); plus ~300 ms of pre-roll |
| Release → transcript (Groq) | < 1.5 s | 0.24–0.52 s (live, real microphone: 0.25–0.37 s) |
| Release → transcript (local small) | < 3 s | 0.3–0.5 s warm; the first use loads the model |
| Cleanup | < 400 ms | 0.2–3 ms |
| Insertion (AT-SPI) | < 200 ms | 36–57 ms |
| Insertion (clipboard paste) | < 200 ms | 117–199 ms |
| Cancel | immediate | nothing inserted |

Accuracy (synthetic speech; `scripts/bench_dictation_stt.py`): Groq turbo — English word error
~8%, Hindi character error ~12%, Devanagari preserved; local small — English ~14%, Hindi ~24%
after the dictation-specific fixes (it was 100%: translated to English or empty).

## Troubleshooting

* **Nothing happens on Right Alt** — the journal (`journalctl --user -u jarvis-voice`) says
  `dictation: rightalt (auto)` at start, or why not. The account must be in the `input` group.
  If Right Alt is AltGr/Compose in your layout, pick another key (JARVIS_DICTATION_KEY).
* **"Sent — couldn't confirm"** — the app took the paste but will not show its text. The text
  is in history: say "paste last dictation" or "copy last dictation".
* **Text went nowhere** — the capsule says so and the text is kept; click into the field and
  "paste last dictation".
* **Hindi comes back in the wrong script** — JARVIS_DICTATION_HINDI_SCRIPT=roman|devanagari.
* **Offline** — the local model takes over; Hindi is weaker there than online.
* **ydotoold** must be running for pastes (`systemctl --user status ydotool`).
* **Trying it safely** — `JARVIS_DICTATION_DRY_RUN=1` runs everything (key, microphone, speech
  recognition, cleanup, profile) and types nothing; the result is in the history file.
