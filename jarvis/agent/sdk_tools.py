"""Jarvis's custom in-process tools, exposed to the agent as an SDK MCP server.

Extends Claude Code's built-in toolset with abilities specific to Jarvis: semantic memory recall,
background task dispatch, screen capture, and Google (Calendar / Gmail / Tasks).
"""

from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..config import Config
from ..integrations.google import calendar as gcal
from ..integrations.google import gmail as gmail_svc
from ..integrations.google import tasks as gtasks
from ..jobs.runner import JobRunner
from ..memory import search as memsearch
from ..vision import screenshot

_GOOGLE_NOT_CONNECTED = "Google isn't connected. Run:  python -m jarvis --google-auth"


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _g(out) -> dict:
    return _text(out if out is not None else _GOOGLE_NOT_CONNECTED)


def build_tool_server(config: Config, job_runner: JobRunner):
    # --- memory / autonomy / vision -------------------------------------
    @tool(
        "recall",
        "Search your Obsidian memory vault (semantic + keyword) for notes relevant to a query. "
        "Use it before answering questions about the user, their projects, people, or past decisions.",
        {"query": str},
    )
    async def recall(args):
        return _text(memsearch.recall(args.get("query", ""), config.vault_path))

    @tool(
        "dispatch_background_task",
        "Hand off a long or slow job (deep research, a large build) to a background worker and keep "
        "talking to the user. Returns immediately; the result is saved to the vault and the user is "
        "notified when it finishes.",
        {"description": str},
    )
    async def dispatch(args):
        job_id = job_runner.dispatch(args.get("description", ""))
        return _text(
            f"Started background task {job_id}. I'll save the result to the vault and notify "
            f"{config.user_name} when it's done."
        )

    @tool("check_background_tasks", "Report the status and results of background tasks you've dispatched.", {})
    async def check(args):
        return _text(job_runner.status_report())

    @tool(
        "code_with_antigravity",
        "Refine the user's coding prompt and delegate the task to Antigravity (agy) to write code and verify tests in the background.",
        {"task": str},
    )
    async def code_with_antigravity(args):
        from pathlib import Path
        from ..integrations import coding
        folder = coding.active_folder()
        if not folder:
            return _text("No VS Code project is open to work on.")
        task = args.get("task", "")
        job_id = job_runner.dispatch_antigravity(task, folder=folder, open_gui=True)
        proj = Path(folder).name
        return _text(
            f"Dispatched Antigravity task {job_id} on {proj}. The IDE is open and agy is working "
            "autonomously. I will announce when finished."
        )

    @tool(
        "capture_screen",
        "Take a screenshot of the user's screen, then use your Read tool on the returned path to "
        "see what they're looking at.",
        {},
    )
    async def capture(args):
        path = screenshot.capture()
        if path:
            return _text(f"Screenshot saved to {path}. Read that file to see the screen. {screenshot.scale_note()}")
        return _text("Couldn't capture the screen — no screenshot tool installed or no graphical session.")

    @tool(
        "screen_share_start",
        "Turn on live screen-share. While on, you automatically receive a fresh screenshot of the "
        "user's screen before each thing they say (its path is Read-able), so you can watch and help "
        "with whatever they're doing in real time. Use when they say to share/watch/look at their screen.",
        {},
    )
    async def screen_share_start(args):
        from ..vision import live

        live.set_active(True)
        return _text("Live screen-share is on — I can now see your screen each time you speak.")

    @tool("screen_share_stop", "Turn off live screen-share mode (stop auto-seeing the screen).", {})
    async def screen_share_stop(args):
        from ..vision import live

        live.set_active(False)
        return _text("Live screen-share is off.")

    # --- self-configuration (voice-controlled model / effort) -----------
    @tool(
        "set_claude_model",
        "Switch which Claude model you (Jarvis) run on. 'model' is opus (most capable), sonnet "
        "(balanced, default), or haiku (fastest). Takes effect from the next turn. Use when the user "
        "says to change models, e.g. 'switch to opus' or 'go back to sonnet'.",
        {"model": str},
    )
    async def set_claude_model(args):
        from . import runtime

        m = runtime.normalize_model(args.get("model", ""))
        if not m:
            return _text("I couldn't tell which model you meant — say opus, sonnet, or haiku.")
        config.model = m
        runtime.mark_dirty()
        return _text(f"Switching to {m} from my next turn.")

    @tool(
        "set_effort",
        "Change your reasoning effort (thinking depth): low (fast), medium, or high (deepest). "
        "Takes effect from the next turn. Use when the user says e.g. 'think harder' or 'keep it quick'.",
        {"effort": str},
    )
    async def set_effort(args):
        from . import runtime

        e = runtime.normalize_effort(args.get("effort", ""))
        if not e:
            return _text("Say low, medium, or high effort.")
        config.effort = e
        runtime.mark_dirty()
        return _text(f"Effort set to {e} from my next turn.")

    # --- timers ---------------------------------------------------------
    @tool(
        "set_timer",
        "Start a countdown timer/alarm. 'seconds' is the total duration (e.g. a 5-minute timer = 300). "
        "It fires out-of-band with a desktop notification and, in voice mode, a spoken alert. Optional "
        "label describes it ('tea', 'stand up').",
        {"seconds": int, "label": str},
    )
    async def set_timer(args):
        from ..jobs import timers

        secs = int(args.get("seconds", 0) or 0)
        if secs <= 0:
            return _text("How long should the timer run? Give me a duration.")
        label = args.get("label", "")
        tid = timers.set_timer(secs, label)
        mins = secs // 60
        human = f"{mins} min" if mins and secs % 60 == 0 else f"{secs} sec"
        return _text(f"Timer #{tid} set for {human}{(' — ' + label) if label else ''}.")

    @tool("list_timers", "List active timers with their remaining time.", {})
    async def list_timers(args):
        from ..jobs import timers

        items = timers.list_timers()
        if not items:
            return _text("No timers running.")
        return _text(
            "\n".join(
                f"#{tid} {label or '(no label)'}: {rem // 60}m {rem % 60}s left"
                for tid, label, rem in items
            )
        )

    @tool("cancel_timer", "Cancel a timer by its id.", {"id": int})
    async def cancel_timer(args):
        from ..jobs import timers

        ok = timers.cancel_timer(int(args.get("id", 0) or 0))
        return _text("Timer cancelled." if ok else "No timer with that id.")

    # --- reminders (persistent; survive restarts) -----------------------
    @tool(
        "set_reminder",
        "Set a reminder that fires at a specific time (persists across restarts). 'when' is an "
        "ISO-8601 datetime — compute it from the current time you're given (e.g. 'at 6pm' today → "
        "2026-07-18T18:00:00+05:30). 'text' is what to remind about.",
        {"when": str, "text": str},
    )
    async def set_reminder(args):
        from ..jobs import reminders

        when = args.get("when", "").strip()
        text = args.get("text", "").strip()
        if not when or not text:
            return _text("I need both a time and what to remind you about.")
        r = reminders.add(config.vault_path, when, text)
        return _text(f"Reminder #{r['id']} set for {when}: {text}")

    @tool("list_reminders", "List pending reminders.", {})
    async def list_reminders(args):
        from ..jobs import reminders

        items = reminders.list_pending(config.vault_path)
        if not items:
            return _text("No pending reminders.")
        return _text("\n".join(f"#{r['id']} {r['due']} — {r['text']}" for r in items))

    @tool("cancel_reminder", "Cancel a reminder by its id.", {"id": int})
    async def cancel_reminder(args):
        from ..jobs import reminders

        ok = reminders.cancel(config.vault_path, int(args.get("id", 0) or 0))
        return _text("Reminder cancelled." if ok else "No reminder with that id.")

    # --- clipboard ------------------------------------------------------
    @tool(
        "read_clipboard",
        "Read the text currently on the user's clipboard. Use for 'what did I just copy', "
        "'summarize this', 'what does this mean' when they've copied something.",
        {},
    )
    async def read_clipboard(args):
        from ..integrations import apps

        text = apps.read_clipboard()
        if text is None:
            return _text("Couldn't read the clipboard (no clipboard tool, e.g. wl-clipboard).")
        if not text.strip():
            return _text("The clipboard is empty.")
        return _text(f"Clipboard contents:\n{text[:4000]}")

    # --- catch up ("what did I miss?") ----------------------------------
    @tool(
        "catch_up",
        "Sweep recent activity across channels for a quick 'what did I miss' digest: unread Gmail, "
        "recent WhatsApp messages, and today's calendar. Summarize what you get back for the user.",
        {},
    )
    async def catch_up(args):
        sections = []
        try:
            em = gmail_svc.check(config, "is:unread")
            if em:
                sections.append(f"UNREAD EMAIL:\n{em}")
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..integrations import whatsapp

            msgs = whatsapp.inbox()
            if msgs:
                wa = "\n".join(
                    f"{m.get('name')} [{m.get('from')}]: {m.get('text')}" for m in msgs[-10:]
                )
                sections.append(f"RECENT WHATSAPP:\n{wa}")
        except Exception:  # noqa: BLE001
            pass
        try:
            ag = gcal.agenda(config, 1)
            if ag:
                sections.append(f"TODAY'S CALENDAR:\n{ag}")
        except Exception:  # noqa: BLE001
            pass
        if not sections:
            return _text("Nothing to catch up on (or those channels aren't connected).")
        return _text("\n\n".join(sections))

    # --- contacts brain -------------------------------------------------
    @tool(
        "find_contact",
        "Look up a person the user knows: searches People/ notes in the vault and recent WhatsApp "
        "senders for a matching name, returning any phone number or WhatsApp JID on file. Use before "
        "asking the user for a number.",
        {"name": str},
    )
    async def find_contact(args):
        name = args.get("name", "").strip().lower()
        if not name:
            return _text("Who are you looking for?")
        hits = []
        people = config.vault_path / "People"
        if people.exists():
            for f in people.glob("*.md"):
                try:
                    body = f.read_text(errors="ignore")
                except OSError:
                    continue
                if name in f.stem.lower() or name in body.lower():
                    hits.append(f"{f.stem}:\n{body[:300]}")
        try:
            from ..integrations import whatsapp

            for m in whatsapp.inbox():
                if name in str(m.get("name", "")).lower():
                    hits.append(f"WhatsApp: {m.get('name')} → JID {m.get('from')}")
        except Exception:  # noqa: BLE001
            pass
        if not hits:
            return _text(f"No contact matching '{name}' in People/ notes or recent WhatsApp.")
        return _text("\n\n".join(hits[:6]))

    # --- episodic memory (activity log) ---------------------------------
    @tool(
        "log_activity",
        "Record a short note of what the user just did or decided, into today's journal with a "
        "timestamp. Use it whenever something notable happens (a task done, a decision, a meeting, a "
        "place they went) so you can later answer 'what did I do today' and give accurate briefings.",
        {"note": str},
    )
    async def log_activity(args):
        from ..memory import vault as vaultmod

        note = args.get("note", "").strip()
        if not note:
            return _text("Nothing to log.")
        vaultmod.journal_append(config.vault_path, note)
        return _text("Logged to today's journal.")

    # --- launching / opening (laptop + phone) ---------------------------
    @tool(
        "open_url",
        "Open a URL in the user's browser (Opera by default). Use for 'open YouTube', 'pull up X' — "
        "e.g. open_url('youtube.com') or a full search/watch URL.",
        {"url": str},
    )
    async def open_url(args):
        from ..integrations import apps

        res = apps.open_url(args.get("url", ""))
        return _text(f"Opened {res} in Opera." if res else "Couldn't open it — no browser found.")

    @tool(
        "launch_app",
        "Launch a desktop application on the laptop by name (e.g. 'code', 'spotify', 'obsidian', "
        "'nautilus'). For WhatsApp/YouTube prefer open_url with the web address.",
        {"name": str},
    )
    async def launch_app(args):
        from ..integrations import apps

        res = apps.launch_app(args.get("name", ""))
        return _text(res.capitalize() if res else f"Couldn't find an app called '{args.get('name','')}'.")

    @tool(
        "phone_open_url",
        "Open a URL on the user's PHONE via KDE Connect (e.g. a YouTube link opens in the YouTube app, "
        "a wa.me link opens WhatsApp). Cannot launch arbitrary apps — Android blocks that.",
        {"url": str},
    )
    async def phone_open_url(args):
        from ..integrations import apps

        ok = apps.phone_open_url(args.get("url", ""), config.kde_device_id or None)
        return _text("Opened on your phone." if ok else "Couldn't reach the phone (KDE Connect).")

    @tool(
        "phone_mirror",
        "Open the phone on screen (scrcpy) so the user can see and control it with mouse/keyboard. "
        "Use for 'open my phone', 'open my Nothing', 'mirror my phone'.",
        {},
    )
    async def phone_mirror(args):
        from ..integrations import apps

        ok, msg = apps.phone_mirror()
        return _text(msg)

    @tool("phone_ring", "Ring the user's phone to help find it (KDE Connect).", {})
    async def phone_ring(args):
        from ..integrations import apps

        ok = apps.phone_ring(config.kde_device_id or None)
        return _text("Ringing your phone." if ok else "Couldn't reach the phone (KDE Connect).")

    @tool(
        "place_call",
        "Start a phone call to a number (opens the dialer on the phone; the user confirms the dial). "
        "Use whatsapp/find_contact to resolve a name to a number first.",
        {"number": str},
    )
    async def place_call(args):
        from ..integrations import apps

        ok = apps.phone_call(args.get("number", ""), config.kde_device_id or None)
        return _text("Dialing on your phone — tap to connect." if ok else "Couldn't start the call.")

    # --- away / auto-attendant mode -------------------------------------
    @tool(
        "set_away",
        "Turn ON away mode: while away, Jarvis auto-replies to incoming WhatsApp/SMS and calls telling "
        "them the user is unavailable, and logs who reached out. Give an optional reason ('in a meeting').",
        {"reason": str},
    )
    async def set_away(args):
        from . import away

        away.set_away(args.get("reason", ""))
        r = away.reason()
        return _text(f"Away mode on{(' — ' + r) if r else ''}. I'll cover your messages and calls.")

    @tool("set_available", "Turn OFF away mode — the user is back and handling their own messages/calls.", {})
    async def set_available(args):
        from . import away

        away.set_available()
        return _text("Welcome back — away mode off.")

    # --- system & media control ----------------------------------------
    @tool("set_volume", "Set the system output volume to an absolute percent (0-150).", {"percent": int})
    async def set_volume(args):
        from ..integrations import system_control as sc

        ok = sc.set_volume(int(args.get("percent", 50)))
        return _text(f"Volume set to {int(args.get('percent', 50))}%." if ok else "Couldn't set volume.")

    @tool(
        "adjust_volume",
        "Nudge the volume up or down by a percentage (positive = louder, negative = quieter).",
        {"delta": int},
    )
    async def adjust_volume(args):
        from ..integrations import system_control as sc

        d = int(args.get("delta", 0))
        sc.adjust_volume(d)
        return _text(f"Volume {sc.get_volume() or ('up' if d >= 0 else 'down')}.")

    @tool("mute_audio", "Mute or unmute system audio.", {"mute": bool})
    async def mute_audio(args):
        from ..integrations import system_control as sc

        on = bool(args.get("mute", True))
        sc.mute_audio(on)
        return _text("Muted." if on else "Unmuted.")

    @tool(
        "media_control",
        "Control whatever is playing (browser, Spotify, phone). action: play_pause, next, previous, stop.",
        {"action": str},
    )
    async def media_control(args):
        from ..integrations import system_control as sc

        who = sc.media_control(args.get("action", ""))
        if who is None:
            return _text("Nothing playing, or that action isn't valid.")
        return _text(f"{args.get('action', '').replace('_', ' ').title()} on {who}.")

    @tool("set_brightness", "Set screen brightness to a percent (1-100).", {"percent": int})
    async def set_brightness(args):
        from ..integrations import system_control as sc

        ok = sc.set_brightness(int(args.get("percent", 70)))
        return _text(f"Brightness set to {int(args.get('percent', 70))}%." if ok else "Couldn't set brightness.")

    @tool("lock_screen", "Lock the screen.", {})
    async def lock_screen(args):
        from ..integrations import system_control as sc

        return _text("Locking the screen." if sc.lock_screen() else "Couldn't lock the screen.")

    @tool("suspend_computer", "Put the computer to sleep (suspend). Confirm with the user first.", {})
    async def suspend_computer(args):
        from ..integrations import system_control as sc

        return _text("Suspending." if sc.suspend() else "Couldn't suspend.")

    @tool("set_radio", "Turn wifi or bluetooth on/off. radio: 'wifi' or 'bluetooth'.", {"radio": str, "on": bool})
    async def set_radio(args):
        from ..integrations import system_control as sc

        radio = args.get("radio", "")
        ok = sc.set_radio(radio, bool(args.get("on", True)))
        state = "on" if args.get("on", True) else "off"
        return _text(f"{radio.title()} turned {state}." if ok else f"Couldn't change {radio}.")

    @tool(
        "system_stats",
        "Report machine health: CPU load and temperature, memory used, GPU utilization/temp, disk, "
        "battery, uptime. Use for 'how hot is my CPU', 'how much RAM is left', 'GPU usage', etc.",
        {},
    )
    async def system_stats(args):
        from ..integrations import system_stats as stats

        return _text(stats.report())

    # --- computer control (mouse + keyboard; "take control") ------------
    _CONTROL_HINT = (
        "Desktop control isn't ready — run scripts/enable-control.sh once (adds you to the input "
        "group + starts ydotoold), then re-login."
    )

    @tool(
        "mouse_move",
        "Move the mouse to real screen pixel coordinates (x, y). Read the current screenshot first and "
        "scale image coordinates to screen coordinates using the note from capture_screen.",
        {"x": int, "y": int},
    )
    async def mouse_move(args):
        from ..integrations import desktop_control as dc

        if dc.available() is None:
            return _text(_CONTROL_HINT)
        ok = dc.move(int(args.get("x", 0)), int(args.get("y", 0)))
        return _text("Moved." if ok else "Mouse move failed.")

    @tool(
        "mouse_click",
        "Click the mouse. button: left/right/middle. Optionally give x,y to move there first "
        "(real screen pixels). Set double=true for a double-click.",
        {"button": str, "x": int, "y": int, "double": bool},
    )
    async def mouse_click(args):
        from ..integrations import desktop_control as dc

        if dc.available() is None:
            return _text(_CONTROL_HINT)
        button = args.get("button", "left") or "left"
        double = bool(args.get("double", False))
        x, y = int(args.get("x", -1)), int(args.get("y", -1))
        ok = dc.move_click(x, y, button, double) if x >= 0 and y >= 0 else dc.click(button, double)
        return _text(f"{'Double-' if double else ''}Clicked {button}." if ok else "Click failed.")

    @tool(
        "type_text",
        "Type text at the current cursor/focus, as if typed on the keyboard.",
        {"text": str},
    )
    async def type_text(args):
        from ..integrations import desktop_control as dc

        if dc.available() is None:
            return _text(_CONTROL_HINT)
        ok = dc.type_text(args.get("text", ""))
        return _text("Typed." if ok else "Typing failed.")

    @tool(
        "press_keys",
        "Press a key or key combo, e.g. 'enter', 'ctrl+c', 'alt+Tab', 'super', 'ctrl+shift+t'.",
        {"keys": str},
    )
    async def press_keys(args):
        from ..integrations import desktop_control as dc

        if dc.available() is None:
            return _text(_CONTROL_HINT)
        ok = dc.press_keys(args.get("keys", ""))
        return _text(f"Pressed {args.get('keys','')}." if ok else f"Couldn't press '{args.get('keys','')}'.")

    @tool(
        "scroll_page",
        "Scroll up or down on the screen. direction: 'up' or 'down', amount: steps (default 5).",
        {"direction": str, "amount": int},
    )
    async def scroll_page(args):
        from ..integrations import desktop_control as dc
        if dc.available() is None:
            return _text(_CONTROL_HINT)
        ok = dc.scroll(args.get("direction", "down") or "down", int(args.get("amount", 5) or 5))
        return _text("Scrolled." if ok else "Scroll failed.")

    @tool(
        "find_and_click",
        "Automatically find a button, icon, or text element on screen using vision and click it directly. Use when asked to click something like 'ok button' or 'submit' or an icon without needing manual coordinate calculation.",
        {"target": str, "button": str, "double": bool},
    )
    async def find_and_click(args):
        from ..integrations import desktop_control as dc
        if dc.available() is None:
            return _text(_CONTROL_HINT)
        res = dc.find_and_click(args.get("target", ""), button=args.get("button", "left") or "left", double=bool(args.get("double", False)), config=config)
        return _text(res)

    @tool("do_not_disturb", "Turn Do Not Disturb (notification silencing) on or off.", {"on": bool})
    async def do_not_disturb(args):
        from ..integrations import system_control as sc

        on = bool(args.get("on", True))
        ok = sc.do_not_disturb(on)
        return _text(("Do Not Disturb on." if on else "Do Not Disturb off.") if ok else "Couldn't change DND.")

    # --- Google: Calendar / Gmail / Tasks -------------------------------
    @tool("google_agenda", "Your upcoming Google Calendar events for the next N days.", {"days": int})
    async def google_agenda(args):
        return _g(gcal.agenda(config, int(args.get("days", 1) or 1)))

    @tool(
        "google_calendar_create",
        "Create a Google Calendar event. start/end are ISO-8601 datetimes with a timezone offset "
        "(e.g. 2026-07-16T15:00:00+05:30). Confirm details with the user first.",
        {"title": str, "start": str, "end": str, "description": str},
    )
    async def google_calendar_create(args):
        return _g(
            gcal.create_event(
                config, args.get("title", ""), args.get("start", ""), args.get("end", ""),
                args.get("description", ""),
            )
        )

    @tool(
        "google_email_check",
        "List recent/unread Gmail messages (id, from, subject, snippet). Optional Gmail search query "
        "like 'is:unread' or 'from:alice newer_than:2d'.",
        {"query": str},
    )
    async def google_email_check(args):
        return _g(gmail_svc.check(config, args.get("query") or "is:unread"))

    @tool("google_email_read", "Read the full body of one Gmail message by its id.", {"id": str})
    async def google_email_read(args):
        return _g(gmail_svc.read(config, args.get("id", "")))

    @tool(
        "google_email_send",
        "Send an email on the user's behalf. Always confirm recipient, subject, and body first.",
        {"to": str, "subject": str, "body": str},
    )
    async def google_email_send(args):
        return _g(gmail_svc.send(config, args.get("to", ""), args.get("subject", ""), args.get("body", "")))

    @tool("google_tasks_list", "List the user's open Google Tasks.", {})
    async def google_tasks_list(args):
        return _g(gtasks.list_tasks(config))

    @tool("google_tasks_add", "Add a Google Task.", {"title": str, "notes": str})
    async def google_tasks_add(args):
        return _g(gtasks.add_task(config, args.get("title", ""), args.get("notes", "")))

    @tool("google_tasks_complete", "Mark an open Google Task complete by (partial) title match.", {"title": str})
    async def google_tasks_complete(args):
        return _g(gtasks.complete_task(config, args.get("title", "")))

    # --- Phone (KDE Connect) --------------------------------------------
    phone = {"kc": None}

    async def _phone():
        if phone["kc"] is None:
            from ..integrations.phone.kdeconnect import KDEConnect

            phone["kc"] = await KDEConnect(config.kde_device_id or None).connect()
        return phone["kc"]

    @tool(
        "phone_messages",
        "Read current phone notifications (WhatsApp, Instagram, SMS, etc.) mirrored from the phone.",
        {},
    )
    async def phone_messages(args):
        try:
            notes = await (await _phone()).active_notifications()
        except Exception as exc:  # noqa: BLE001
            return _text(f"Phone not reachable ({exc}).")
        if not notes:
            return _text("No active phone notifications.")
        return _text(
            "\n".join(
                f"[{n['app']}] {n['title']}: {n['text']} (id={n['id']}, repliable={n['repliable']})"
                for n in notes
            )
        )

    @tool(
        "phone_reply",
        "Reply to a phone message by its notification id. Works only if that notification is repliable.",
        {"id": str, "message": str},
    )
    async def phone_reply(args):
        try:
            await (await _phone()).reply(args.get("id", ""), args.get("message", ""))
            return _text("Reply sent.")
        except Exception as exc:  # noqa: BLE001
            return _text(f"Couldn't reply ({exc}) — that notification may not support inline replies.")

    @tool("phone_send_sms", "Send an SMS to a phone number via the phone.", {"number": str, "message": str})
    async def phone_send_sms(args):
        try:
            ok = (await _phone()).send_sms(args.get("number", ""), args.get("message", ""))
        except Exception as exc:  # noqa: BLE001
            return _text(f"Couldn't send SMS ({exc}).")
        return _text("SMS sent." if ok else "Couldn't send the SMS.")

    # --- WhatsApp (Baileys bridge) --------------------------------------
    @tool(
        "whatsapp_send",
        "Send a WhatsApp message. 'to' can be a contact NAME, a phone number (country code, digits "
        "only), or a JID. It resolves names safely and refuses rather than guessing. Put the user's "
        "message VERBATIM in 'message' — never paraphrase or add words.",
        {"to": str, "message": str},
    )
    async def whatsapp_send(args):
        from ..integrations import whatsapp

        return _text(whatsapp.smart_send(args.get("to", ""), args.get("message", ""))["message"])

    @tool(
        "whatsapp_inbox",
        "Recent incoming WhatsApp messages (sender name + text only). To reply, call whatsapp_send "
        "with the person's NAME.",
        {},
    )
    async def whatsapp_inbox(args):
        from ..integrations import whatsapp

        msgs = whatsapp.inbox()
        if not msgs:
            return _text("No recent WhatsApp messages (or the bridge isn't running).")
        return _text("\n".join(f"{m.get('name')}: {m.get('text')}" for m in msgs[-15:]))

    return create_sdk_mcp_server(
        "jarvis",
        tools=[
            recall, dispatch, check, capture, screen_share_start, screen_share_stop,
            set_claude_model, set_effort,
            set_timer, list_timers, cancel_timer, log_activity,
            set_reminder, list_reminders, cancel_reminder,
            read_clipboard, catch_up, find_contact,
            open_url, launch_app, phone_open_url, phone_ring, place_call, phone_mirror,
            set_away, set_available,
            set_volume, adjust_volume, mute_audio, media_control, set_brightness,
            lock_screen, suspend_computer, set_radio, do_not_disturb, system_stats,
            mouse_move, mouse_click, type_text, press_keys, scroll_page, find_and_click,
            google_agenda, google_calendar_create,
            google_email_check, google_email_read, google_email_send,
            google_tasks_list, google_tasks_add, google_tasks_complete,
            phone_messages, phone_reply, phone_send_sms,
            whatsapp_send, whatsapp_inbox,
        ],
    )
