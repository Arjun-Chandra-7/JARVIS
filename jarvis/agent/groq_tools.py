"""Tool registry for the Groq brain: OpenAI-format function schemas + an async dispatcher.

Groq gives us only the model, so here we re-expose everything — the "built-in" shell/file/web tools
that Claude Code provided, plus all of Jarvis's own abilities (memory, timers, phone, system control,
Google, desktop control). Each entry maps a tool name to (json-schema, python callable).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ..config import Config
from .permissions import is_destructive


def _obj(props: dict, required: list[str] | None = None) -> dict:
    if not props:
        props = {"_dummy": {"type": "string", "description": "Ignore this field, leave empty."}}
    return {"type": "object", "properties": props, "required": required or []}


def _i(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _b(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


async def _compose_message(config: Config, name: str, about: str) -> str:
    """Turn an intent ('ask how his health is') into a natural WhatsApp message, via the LLM.

    Falls back to a sensible template if the model is unreachable, so a message always goes out.
    """
    about = (about or "").strip()
    first = (name or "there").strip().split()[0].title()
    try:
        import httpx

        base, key, model = config.llm_params()
        prompt = (
            f"Write a short, warm, natural WhatsApp message to {first} about: {about}. "
            "One or two sentences, first person as the sender, no quotes, no preamble, no emojis "
            "unless natural. Just the message text."
        )
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7},
            )
            text = r.json()["choices"][0]["message"]["content"].strip().strip('"').strip()
            if text:
                return text
    except Exception:  # noqa: BLE001
        pass
    return f"Hey {first}, {about}".strip()


def build_registry(config: Config, job_runner, confirm_fn: Optional[Callable[[str], Awaitable[bool]]]):
    """Return (schemas, dispatch). `dispatch(name, args)` runs a tool and returns text."""
    reg: dict[str, tuple[dict, Callable]] = {}

    def tool(name: str, desc: str, params: dict, required=None):
        def deco(fn):
            reg[name] = ({"type": "function", "function": {"name": name, "description": desc,
                          "parameters": _obj(params, required)}}, fn)
            return fn
        return deco

    async def _confirm(desc: str) -> bool:
        return bool(confirm_fn and await confirm_fn(desc))

    # ---------------- built-in equivalents (shell / files / web) ----------------
    @tool("run_bash", "Run a shell command on this Linux machine and return stdout/stderr.",
          {"command": {"type": "string"}}, ["command"])
    async def run_bash(a):
        cmd = a.get("command", "")
        if is_destructive(cmd) and not config.allow_unconfirmed_shell:
            if not await _confirm(f"run this command:\n    {cmd}"):
                return "User declined the command."
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            return out.strip()[:6000] or f"(exit {r.returncode}, no output)"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("read_file", "Read a text file.", {"path": {"type": "string"}}, ["path"])
    async def read_file(a):
        try:
            return Path(a["path"]).expanduser().read_text(errors="ignore")[:8000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("write_file", "Create or overwrite a text file.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])
    async def write_file(a):
        try:
            p = Path(a["path"]).expanduser().resolve()
            if p.exists() and not config.allow_unconfirmed_shell:
                head = p.read_text(errors="ignore")[:400]
                if "author: jarvis" not in head and "/scratch" not in str(p) and "/tmp" not in str(p):
                    if not await _confirm(f"overwrite existing file not created by Jarvis:\n    {p}"):
                        return "User declined overwriting this file."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(a.get("content", ""))
            return f"wrote {p}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("list_dir", "List a directory.", {"path": {"type": "string"}}, ["path"])
    async def list_dir(a):
        try:
            return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in Path(a["path"]).expanduser().iterdir()))[:4000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("web_search", "Search the web (returns a short summary + top links).", {"query": {"type": "string"}}, ["query"])
    async def web_search(a):
        import httpx
        q = a.get("query", "")
        try:
            r = httpx.get("https://api.duckduckgo.com/", params={"q": q, "format": "json", "no_html": 1}, timeout=8)
            j = r.json()
            bits = [j.get("AbstractText", "")]
            for t in (j.get("RelatedTopics") or [])[:5]:
                if isinstance(t, dict) and t.get("Text"):
                    bits.append("- " + t["Text"])
            out = "\n".join(b for b in bits if b)
            return out[:3000] or "No instant answer; try web_fetch on a specific URL."
        except Exception as exc:  # noqa: BLE001
            return f"search error: {exc}"

    @tool("web_fetch", "Fetch a URL and return its text.", {"url": {"type": "string"}}, ["url"])
    async def web_fetch(a):
        import re
        import httpx
        try:
            html = httpx.get(a["url"], timeout=10, follow_redirects=True).text
            text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html))
            return re.sub(r"\s+", " ", text).strip()[:5000]
        except Exception as exc:  # noqa: BLE001
            return f"fetch error: {exc}"

    @tool("deep_research",
          "Do serious, up-to-date research using the user's Perplexity account (browses the live web, "
          "synthesises sources). Use this WHENEVER the user asks to 'research', wants depth/current "
          "information, or is thinking through a project — not for trivial facts (use web_search for those).",
          {"query": {"type": "string"}}, ["query"])
    async def deep_research(a):
        from ..integrations import research
        q = a.get("query", "")
        try:
            r = await research.research(q)
        except Exception as exc:  # noqa: BLE001
            return f"Deep research failed ({exc}). Is Playwright/Chrome set up? Try `--perplexity-login`."
        if not r.get("ok"):
            return r.get("text", "No result.")
        out = r["text"]
        if r.get("sources"):
            out += "\n\nSources:\n" + "\n".join("- " + s for s in r["sources"])
        return out[:6000]

    # ---------------- memory / status ----------------
    @tool("recall", "Search the Obsidian memory vault for relevant notes.", {"query": {"type": "string"}}, ["query"])
    async def recall(a):
        from ..memory import search as memsearch
        return memsearch.recall(a.get("query", ""), config.vault_path)

    @tool("log_activity", "Save a short timestamped note to today's journal.", {"note": {"type": "string"}}, ["note"])
    async def log_activity(a):
        from ..memory import vault as v
        v.journal_append(config.vault_path, a.get("note", ""))
        return "logged."

    @tool("system_stats", "Machine health: CPU/mem/GPU/disk/battery/temps.", {})
    async def system_stats(a):
        from ..integrations import system_stats as s
        return s.report()

    @tool("capture_screen", "Look at the user's screen. 'question' = what to look for.",
          {"question": {"type": "string"}})
    async def capture_screen(a):
        from ..vision import analyze, screenshot
        path = screenshot.capture()
        if not path:
            return "Couldn't capture the screen."
        ans = analyze.describe(path, a.get("question", ""), config)
        if ans is None:
            return ("Screenshot taken, but no vision model is set up. Add GEMINI_API_KEY to .env, or "
                    "install Ollama and `ollama pull moondream`.")
        return ans

    @tool("analyze_image", "Describe/answer about an image file. path + optional question.",
          {"path": {"type": "string"}, "question": {"type": "string"}}, ["path"])
    async def analyze_image(a):
        from ..vision import analyze
        ans = analyze.describe(a.get("path", ""), a.get("question", ""), config)
        return ans or "No vision model set up (GEMINI_API_KEY or Ollama + moondream)."

    @tool("screen_share_start", "Start watching the user's screen live (a fresh look each turn).", {})
    async def screen_share_start(a):
        from ..vision import live
        live.set_active(True)
        return "Live screen-share on — I'll keep an eye on your screen."

    @tool("screen_share_stop", "Stop watching the screen live.", {})
    async def screen_share_stop(a):
        from ..vision import live
        live.set_active(False)
        return "Live screen-share off."

    # --- coding agent (VS Code + Antigravity) ---------------------------
    @tool("read_project", "Find the project open in VS Code and list its files so you can analyze it.", {})
    async def read_project(a):
        from ..integrations import coding
        folder = coding.active_folder()
        if not folder:
            return "I don't see a VS Code project open."
        return coding.overview(folder)

    @tool(
        "code_with_antigravity",
        "Refine the user's coding prompt and delegate the task to the Antigravity CLI (agy) to write code and verify tests. Executes in the background on the active VS Code file/workspace, announcing when finished.",
        {"task": {"type": "string"}, "folder": {"type": "string"}}, ["task"],
    )
    async def code_with_antigravity(a):
        from pathlib import Path
        from ..integrations import coding
        ctx = coding.active_context()
        folder = a.get("folder") or ctx.get("folder")
        if not folder:
            return "No VS Code or project folder is open to work on."
        task = a.get("task", "")
        target_label = ctx.get("file") or ctx.get("project_name") or Path(folder).name
        if job_runner:
            job_id = job_runner.dispatch_antigravity(
                task,
                folder=folder,
                active_file=ctx.get("file"),
                active_file_path=ctx.get("file_path"),
            )
            return (
                f"I have refined your prompt and dispatched agy CLI ({job_id}) on {target_label}. "
                "It is running autonomously in the background and I will announce when finished."
            )
        return "No task runner available."

    @tool(
        "check_coding_tasks",
        "Check status and results of background coding tasks running in Antigravity.",
        {},
    )
    async def check_coding_tasks(a):
        if not job_runner:
            return "No task runner available."
        return job_runner.status_report()

    @tool("catch_up", "Sweep unread email + recent WhatsApp + today's calendar.", {})
    async def catch_up(a):
        from ..integrations import whatsapp
        from ..integrations.google import calendar as gcal, gmail
        out = []
        for label, fn in (("EMAIL", lambda: gmail.check(config, "is:unread")),
                          ("CALENDAR", lambda: gcal.agenda(config, 1))):
            try:
                v = fn()
                if v:
                    out.append(f"{label}:\n{v}")
            except Exception:  # noqa: BLE001
                pass
        try:
            msgs = whatsapp.inbox()
            if msgs:
                out.append("WHATSAPP:\n" + "\n".join(f"{m.get('name')}: {m.get('text')}" for m in msgs[-8:]))
        except Exception:  # noqa: BLE001
            pass
        return "\n\n".join(out) or "Nothing to catch up on."

    # ---------------- timers / reminders ----------------
    @tool("set_timer", "Countdown timer. seconds = total number of seconds (5 min = 300).",
          {"seconds": {"type": "string"}, "label": {"type": "string"}}, ["seconds"])
    async def set_timer(a):
        from ..jobs import timers
        tid = timers.set_timer(_i(a.get("seconds"), 60), a.get("label", ""))
        return f"timer #{tid} set."

    @tool("set_reminder", "Reminder at an ISO-8601 time (persists).",
          {"when": {"type": "string"}, "text": {"type": "string"}}, ["when", "text"])
    async def set_reminder(a):
        from ..jobs import reminders
        r = reminders.add(config.vault_path, a.get("when", ""), a.get("text", ""))
        return f"reminder #{r['id']} set for {a.get('when')}."

    # ---------------- comms ----------------
    @tool("whatsapp_send",
          "Send a WhatsApp message. 'to' = a contact NAME, a phone number, or a JID. Put the user's "
          "message VERBATIM in 'message' — do not paraphrase or add words.",
          {"to": {"type": "string"}, "message": {"type": "string"}}, ["to", "message"])
    async def whatsapp_send(a):
        from ..integrations import whatsapp
        return whatsapp.smart_send(a.get("to", ""), a.get("message", ""))["message"]

    @tool("instagram_dms", "Read the user's recent Instagram direct-message threads (their account).", {})
    async def instagram_dms(a):
        from ..integrations import instagram
        r = await instagram.dms()
        return r.get("text", "Couldn't read Instagram DMs.")

    @tool("whatsapp_inbox", "Recent incoming WhatsApp messages (sender name + text; no IDs).", {})
    async def whatsapp_inbox(a):
        from ..integrations import whatsapp
        m = whatsapp.inbox()
        return "\n".join(f"{x.get('name')}: {x.get('text')}" for x in m[-15:]) or "No new messages."

    @tool("find_contact", "Look up a person's WhatsApp contact by name (before sending).",
          {"name": {"type": "string"}}, ["name"])
    async def find_contact(a):
        from ..integrations import contacts, phone_contacts, whatsapp
        name = a.get("name", "")
        out = []
        local = contacts.lookup(name)
        if local:
            out.append(f"{local['name']}" + (f" ({local['number']})" if local.get("number") else "") + " [remembered]")
        for c in phone_contacts.lookup(name)[:6]:
            out.append(f"{c['name']} ({c['number']})")
        out += [c["name"] for c in whatsapp.resolve(name)[:4]]
        return "; ".join(out) if out else f"No contact matching '{name}'."

    @tool("remember_contact",
          "Permanently remember a person's phone number (and optional note) in the vault, so you can "
          "message/call them later. Use whenever the user tells you someone's number or who someone is.",
          {"name": {"type": "string"}, "number": {"type": "string"}, "note": {"type": "string"}},
          ["name"])
    async def remember_contact(a):
        from ..integrations import contacts
        return contacts.remember(a.get("name", ""), a.get("number", ""), a.get("note", ""))["message"]

    @tool("wifi_scan",
          "List nearby Wi-Fi networks and reveal the passwords THIS computer has already "
          "saved. Cannot recover passwords for networks the machine never joined.",
          {})
    async def wifi_scan(a):
        from ..integrations import wifi_scan as ws
        return await asyncio.to_thread(ws.report)

    @tool("who_is_around",
          "Who is physically nearby right now: people located by the webcam (range + bearing), "
          "range-only acoustic contacts, and people identified by their devices. Says what is "
          "unknown rather than guessing.",
          {})
    async def who_is_around(a):
        from ..presence import service as presence
        return await asyncio.to_thread(presence.summary, config)

    @tool("remember_device",
          "Bind a MAC address to a person so Jarvis can recognise them by their phone/watch "
          "even with no line of sight. Randomised (private) MACs are refused.",
          {"mac": {"type": "string"}, "person": {"type": "string"}}, ["mac", "person"])
    async def remember_device(a):
        from ..presence import identity
        return identity.remember(config, a.get("mac", ""), a.get("person", ""))["message"]

    @tool("contact_context",
          "What Jarvis knows about a person from past WhatsApp chats: frequency, recurring topics, "
          "pending items, last interaction. Use before messaging or when the user asks about someone. "
          "Compact derived summary only — not raw history.",
          {"name": {"type": "string"}}, ["name"])
    async def contact_context(a):
        from ..memory import contacts_index
        out = await asyncio.to_thread(contacts_index.recall, config, a.get("name", ""))
        return out or f"No prior WhatsApp context for '{a.get('name', '')}'."

    @tool("conversation_search",
          "Search your own past WhatsApp conversations for a topic and return the matching lines "
          "(who said what). Local, private. Use when the user asks 'what did X say about Y'.",
          {"query": {"type": "string"}}, ["query"])
    async def conversation_search(a):
        from ..memory import contacts_index
        rows = await asyncio.to_thread(
            lambda: contacts_index.search(config, a.get("query", ""), authorized=True, limit=12))
        if not rows:
            return "Nothing in your local conversation index matches that."
        return "\n".join(f"{r['name']}{' (you)' if r['from_me'] else ''}: {r['text']}" for r in rows)

    @tool("message_person",
          "Message someone by INTENT — you give the person's name and what the message is ABOUT, and "
          "Jarvis composes a natural, friendly WhatsApp message and sends it. Use this for requests like "
          "'message Pradhuman about his health' (about='ask how his health is'). For exact dictated text "
          "use whatsapp_send instead.",
          {"name": {"type": "string"}, "about": {"type": "string"}}, ["name", "about"])
    async def message_person(a):
        from ..integrations import whatsapp
        name, about = a.get("name", ""), a.get("about", "")
        text = await _compose_message(config, name, about)
        res = whatsapp.smart_send(name, text)
        if res.get("ok"):
            return f'Sent to {name}: "{text}"'
        return res["message"]

    @tool("place_call", "Open the phone dialer for a number.", {"number": {"type": "string"}}, ["number"])
    async def place_call(a):
        from ..integrations import apps
        return "dialing." if apps.phone_call(a.get("number", ""), config.kde_device_id or None) else "couldn't call."

    @tool("phone_mirror", "Mirror + control the phone on screen (scrcpy).", {})
    async def phone_mirror(a):
        from ..integrations import apps
        ok, msg = apps.phone_mirror()
        return msg

    @tool("set_away", "Away mode ON — auto-reply to messages/calls.", {"reason": {"type": "string"}})
    async def set_away(a):
        from . import away, pa_daemon
        away.set_away(a.get("reason", ""), config)
        pa_daemon.start(config)
        return "away mode on."

    @tool("set_available", "Away mode OFF.", {})
    async def set_available(a):
        from . import away
        away.set_available(config)
        return "away mode off."

    # ---------------- system / media / apps ----------------
    @tool("set_volume", "Set output volume percent (0-150).", {"percent": {"type": "string"}}, ["percent"])
    async def set_volume(a):
        from ..integrations import system_control as sc
        sc.set_volume(_i(a.get("percent"), 50))
        return "done."

    @tool("media_control", "Media: play_pause/next/previous/stop.", {"action": {"type": "string"}}, ["action"])
    async def media_control(a):
        from ..integrations import system_control as sc
        return sc.media_control(a.get("action", "")) or "nothing playing."

    @tool("set_brightness", "Set screen brightness percent (1-100).", {"percent": {"type": "string"}}, ["percent"])
    async def set_brightness(a):
        from ..integrations import system_control as sc
        sc.set_brightness(_i(a.get("percent"), 70))
        return "done."

    @tool("lock_screen", "Lock the screen.", {})
    async def lock_screen(a):
        from ..integrations import system_control as sc
        sc.lock_screen()
        return "locking."

    @tool("do_not_disturb", "Silence notifications. on = 'true' or 'false'.", {"on": {"type": "string"}}, ["on"])
    async def dnd(a):
        from ..integrations import system_control as sc
        from ..preferences import set_notifications
        set_notifications(not _b(a.get("on", True)))
        sc.do_not_disturb(_b(a.get("on", True)))
        return "done."

    @tool("open_url", "Open a URL in the browser (Opera).", {"url": {"type": "string"}}, ["url"])
    async def open_url(a):
        from ..integrations import apps
        return f"opened {apps.open_url(a.get('url', ''))}"

    @tool("launch_app", "Launch a desktop app by name.", {"name": {"type": "string"}}, ["name"])
    async def launch_app(a):
        from ..integrations import apps
        return apps.launch_app(a.get("name", "")) or "not found."

    @tool("read_clipboard", "Read the clipboard text.", {})
    async def read_clipboard(a):
        from ..integrations import apps
        return apps.read_clipboard() or "(clipboard empty/unavailable)"

    # ---------------- desktop control ----------------
    @tool("mouse_move", "Move the mouse cursor to screen pixel coordinates x, y.", {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"])
    async def mouse_move(a):
        from ..integrations import desktop_control as dc
        return "moved." if dc.move(_i(a.get("x", 0)), _i(a.get("y", 0))) else "control not ready."

    @tool("mouse_click", "Click mouse button at optional x, y coordinates.", {"button": {"type": "string"}, "x": {"type": "integer"}, "y": {"type": "integer"}, "double": {"type": "boolean"}})
    async def mouse_click(a):
        from ..integrations import desktop_control as dc
        button = a.get("button", "left") or "left"
        double = _b(a.get("double", False))
        x, y = _i(a.get("x", -1), -1), _i(a.get("y", -1), -1)
        ok = dc.move_click(x, y, button, double) if x >= 0 and y >= 0 else dc.click(button, double)
        return f"{'double-' if double else ''}clicked {button}." if ok else "click failed."

    @tool("type_text", "Type text at the keyboard focus.", {"text": {"type": "string"}}, ["text"])
    async def type_text(a):
        from ..integrations import desktop_control as dc
        return "typed." if dc.type_text(a.get("text", "")) else "control not ready."

    @tool("press_keys", "Press a key combo, e.g. 'ctrl+c', 'enter'.", {"keys": {"type": "string"}}, ["keys"])
    async def press_keys(a):
        from ..integrations import desktop_control as dc
        return "pressed." if dc.press_keys(a.get("keys", "")) else "control not ready."

    @tool("scroll_page", "Scroll screen up or down.", {"direction": {"type": "string"}, "amount": {"type": "integer"}})
    async def scroll_page(a):
        from ..integrations import desktop_control as dc
        return "scrolled." if dc.scroll(a.get("direction", "down") or "down", _i(a.get("amount", 5), 5)) else "scroll failed."

    @tool("find_and_click", "Find a button, icon, or text element on screen via vision and click it directly.", {"target": {"type": "string"}, "button": {"type": "string"}, "double": {"type": "boolean"}}, ["target"])
    async def find_and_click(a):
        from ..integrations import desktop_control as dc
        return dc.find_and_click(a.get("target", ""), button=a.get("button", "left") or "left", double=_b(a.get("double", False)), config=config)

    # ---------------- Google ----------------
    @tool("google_agenda", "Upcoming calendar events for N days.", {"days": {"type": "string"}})
    async def google_agenda(a):
        from ..integrations.google import calendar as gcal
        return gcal.agenda(config, _i(a.get("days"), 1) or 1) or "Google not connected."

    @tool("google_email_check", "List Gmail (default unread).", {"query": {"type": "string"}})
    async def google_email_check(a):
        from ..integrations.google import gmail
        return gmail.check(config, a.get("query") or "is:unread") or "Google not connected."

    @tool("google_email_read", "Read the full body of one Gmail message by its id (from google_email_check).",
          {"id": {"type": "string"}}, ["id"])
    async def google_email_read(a):
        from ..integrations.google import gmail
        return gmail.read(config, a.get("id", "")) or "Google not connected."

    @tool("google_email_send", "Send an email (confirm first).",
          {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["to", "subject", "body"])
    async def google_email_send(a):
        if not await _confirm(f"send an email to {a.get('to')} — {a.get('subject')}"):
            return "user declined."
        from ..integrations.google import gmail
        return gmail.send(config, a.get("to", ""), a.get("subject", ""), a.get("body", "")) or "Google not connected."

    @tool("google_calendar_create", "Create a calendar event. start/end are ISO datetimes.",
          {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "description": {"type": "string"}}, ["title", "start", "end"])
    async def google_calendar_create(a):
        if not await _confirm(f"create calendar event '{a.get('title')}' at {a.get('start')}"):
            return "user declined."
        from ..integrations.google import calendar as gcal
        return gcal.create_event(config, a.get("title", ""), a.get("start", ""), a.get("end", ""),
                                 a.get("description", "")) or "Google not connected."

    @tool("google_tasks_list", "List your Google Tasks.", {})
    async def google_tasks_list(a):
        from ..integrations.google import tasks
        return tasks.list_tasks(config) or "Google not connected."

    @tool("google_tasks_add", "Add a Google Task.", {"title": {"type": "string"}, "notes": {"type": "string"}}, ["title"])
    async def google_tasks_add(a):
        from ..integrations.google import tasks
        return tasks.add_task(config, a.get("title", ""), a.get("notes", "")) or "Google not connected."

    @tool("google_tasks_complete", "Mark a Google Task complete by its title.", {"title": {"type": "string"}}, ["title"])
    async def google_tasks_complete(a):
        from ..integrations.google import tasks
        return tasks.complete_task(config, a.get("title", "")) or "Google not connected."

    # ---------------- n8n Automation Engine (thousands of app connectors) ----------------
    @tool("trigger_automation", "Trigger an n8n automation workflow via webhook alias or URL. Pass extra JSON fields in 'payload_json'.",
          {"workflow": {"type": "string"}, "payload_json": {"type": "string"}}, ["workflow"])
    async def trigger_automation(a):
        from ..integrations import n8n
        import json
        data = {}
        if a.get("payload_json"):
            try:
                data = json.loads(a["payload_json"])
            except Exception:
                data = {"data": a["payload_json"]}
        return n8n.trigger_workflow(a.get("workflow", ""), data, config=config)

    @tool("remember_automation", "Save an n8n webhook ID/URL under an easy alias (e.g. alias='notion-sync') so you can trigger it anytime.",
          {"alias": {"type": "string"}, "url_or_id": {"type": "string"}, "description": {"type": "string"}}, ["alias", "url_or_id"])
    async def remember_automation(a):
        from ..integrations import n8n
        return n8n.register_workflow(a.get("alias", ""), a.get("url_or_id", ""), a.get("description", ""), config=config)

    @tool("list_automations", "List all remembered n8n automations and workflows.", {})
    async def list_automations(a):
        from ..integrations import n8n
        return n8n.list_workflows(config=config)

    # ---------------- OmniCore (Universal Recorder & 2-Sided PA Guardian Shield) ----------------
    @tool("get_activity_recordings", "Retrieve recent recorded text messages, calls, schedule alerts, and system activities.",
          {"limit": {"type": "integer"}, "category": {"type": "string"}})
    async def get_activity_recordings(a):
        from . import omnicore
        return omnicore.list_recent_recordings(limit=_i(a.get("limit", 15), 15), category_filter=a.get("category"), config=config)

    @tool("check_pa_status", "Check Arjun's real-time computed schedule/busy status and whether 2-sided PA conversational defense is armed.", {})
    async def check_pa_status(a):
        from . import omnicore
        status = omnicore.get_current_status(config=config)
        return f"Current Status: {'BUSY / AWAY (' + status.get('reason','') + ') until ' + str(status.get('until','')) if status.get('busy') else 'AVAILABLE'}. [Source: {status.get('source','Normal')}]"

    @tool("set_pa_status", "Set Arjun's status manually (e.g., 'I am going out for 2 hours', 'In a meeting') or mark 'available'.",
          {"status_reason": {"type": "string"}, "is_busy": {"type": "boolean"}}, ["status_reason"])
    async def set_pa_status(a):
        # Single owner: away state + the WhatsApp auto-reply daemon are driven the same way
        # everywhere (voice command router, set_away tool, here).
        from . import away, omnicore, pa_daemon
        if not _b(a.get("is_busy", True)) or a.get("status_reason", "").strip().lower() in ("available", "free", "back", "off"):
            away.set_available(config)
            omnicore.record_event("Status Change", "User", "Marked AVAILABLE / back at desk", config=config)
            return "Status updated to AVAILABLE. Welcome back, sir!"
        reason = a.get("status_reason", "Away from desk").strip()
        away.set_away(reason, config)
        pa_daemon.start(config)
        omnicore.record_event("Status Change", "User", f"Marked AWAY/BUSY: {reason}", config=config)
        return f"Status set to BUSY / AWAY ({reason}). I'll answer new WhatsApp messages as your assistant and brief you on return."

    @tool("add_user_schedule", "Register a scheduled activity or event (like tuition or classes) with start and end ISO timestamps.",
          {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}}, ["title", "start", "end"])
    async def add_user_schedule(a):
        from . import omnicore
        return omnicore.add_schedule_event(a.get("title", ""), a.get("start", ""), a.get("end", ""), source="Agent Tool", config=config)

    @tool("process_incoming_communication", "Record an incoming text/call for the away-mode debrief. Does NOT auto-reply — the away-mode daemon is the sole WhatsApp auto-responder.",
          {"type": {"type": "string"}, "sender": {"type": "string"}, "id": {"type": "string"}, "content": {"type": "string"}}, ["type", "sender", "content"])
    async def process_incoming_communication(a):
        # Passive recorder only. Automatic replies to incoming WhatsApp/calls are owned
        # exclusively by jarvis.agent.pa_daemon so there is never a second responder.
        from . import away
        c_type = "call" if "call" in a.get("type", "text").lower() else "whatsapp"
        away.record_event(config, {
            "id": a.get("id", "") or f"{c_type}:{a.get('sender', '')}",
            "type": c_type, "sender": a.get("sender", "Unknown"),
            "jid": a.get("id", "") or a.get("sender", ""),
            "text": a.get("content", "") or ("Incoming call" if c_type == "call" else ""),
            "status": "recorded",
        })
        return f"Recorded {c_type} from {a.get('sender', 'Unknown')} for your away-mode debrief."

    # ---------------- Full Laptop Mastery & Omni-Control ----------------
    @tool("enable_full_laptop_autonomy", "Unlock unconfirmed shell execution and full computer automation permissions so Jarvis can control all tools and the whole laptop fully.",
          {"enable": {"type": "boolean"}})
    async def enable_full_laptop_autonomy(a):
        val = _b(a.get("enable", True))
        config.allow_unconfirmed_shell = val
        from . import omnicore
        omnicore.record_event("System Autonomy", "Jarvis", f"Full laptop autonomy set to: {val}", config=config)
        return f"Full laptop autonomy is now {'ENABLED (Unrestricted machine mastery)' if val else 'DISABLED (Standard safety confirmation gate active)'}."

    @tool("control_laptop_full", "Execute advanced system, GUI, or machine operations to manage any application, tool, window, or hardware parameter on the laptop.",
          {"command_or_script": {"type": "string"}, "explanation": {"type": "string"}}, ["command_or_script"])
    async def control_laptop_full(a):
        cmd = a.get("command_or_script", "")
        from . import omnicore
        if is_destructive(cmd) and not config.allow_unconfirmed_shell:
            if not await _confirm(f"run this system command:\n    {cmd}"):
                return "User declined the command."
        omnicore.record_event("Laptop Control", "Jarvis", f"Executing: {cmd} ({a.get('explanation','')})", config=config)
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
            return f"[Omni-Control Execution Result] Return Code {r.returncode}:\n{out.strip()[:6000] or '(Command executed successfully, no terminal output)'}"
        except Exception as exc:
            return f"[Omni-Control Error]: {exc}"

    # ---------------- PA Guardian & Meet Bot ----------------

    @tool("start_pa_daemon", "Activate the PA guardian shield: monitors WhatsApp messages and phone calls while you're away, auto-replies on your behalf, and gives you a full debrief when you're back.", {})
    async def start_pa_daemon(a):
        from . import pa_daemon
        pa_daemon.start(config)
        return "PA Guardian armed, sir. I'll handle all incoming messages and calls while you're out. I'll brief you the moment you're back."

    @tool("stop_pa_daemon", "Deactivate the PA guardian and get a summary of what happened while you were away.", {})
    async def stop_pa_daemon(a):
        from . import pa_daemon
        brief = pa_daemon.stop()
        return brief

    @tool("whatsapp_scan", "Scan and analyse all WhatsApp contacts and recent chat history from the bridge.", {})
    async def whatsapp_scan(a):
        import httpx, os
        base = f"http://127.0.0.1:{os.environ.get('WA_PORT', '8765')}"
        try:
            status = httpx.get(f"{base}/status", timeout=3).json().get("connected", False)
            if not status:
                return "WhatsApp bridge is not connected. Start it with: cd ~/Dev/Jarvis/whatsapp && node wa_service.js"
            contacts_list = httpx.get(f"{base}/contacts", timeout=5).json()
            inbox_list = httpx.get(f"{base}/inbox", timeout=5).json()
        except Exception as exc:  # noqa: BLE001
            return f"Could not reach WhatsApp bridge: {exc}"
        by_sender: dict = {}
        for msg in inbox_list:
            name = msg.get("name") or msg.get("from", "?")
            by_sender.setdefault(name, []).append(msg.get("text", ""))
        lines = [f"WhatsApp Bridge — {len(contacts_list)} contacts known, {len(inbox_list)} recent messages.\n"]
        lines.append("Recent conversations:")
        for sender, texts in list(by_sender.items())[-10:]:
            last = texts[-1][:80] if texts else ""
            lines.append(f"  {sender} ({len(texts)} msg): \"{last}\"")
        if not by_sender:
            lines.append("  (No recent incoming messages in the bridge buffer.)")
        lines.append(f"\nTop contacts: {', '.join(c['name'] for c in contacts_list[:20] if c.get('name'))}")
        return "\n".join(lines)

    @tool("join_meet_and_take_notes",
          "Join Google Meet with mic/camera off, record captions and participant changes. Omitted URL uses twa-pgjz-gss. Admission can require host approval; check status before claiming joined.",
          {"url": {"type": "string"}, "return_time": {"type": "string"}})  # url not required — auto-detected
    async def join_meet_and_take_notes(a):
        from ..integrations import meet_bot
        msg = await meet_bot.join_meet(a.get("url", "") or "", a.get("return_time", "soon"), config)
        st = meet_bot.status()
        return f"{msg} (state: {st['state']})"

    @tool("stop_meet_notes", "Stop the Google Meet bot and retrieve the full meeting notes transcript.", {})
    async def stop_meet_notes(a):
        from ..integrations import meet_bot
        return await meet_bot.stop_meet()

    @tool("toggle_sports_widget",
          "Opens or closes the live cricket sports widget on the screen.",
          {"state": {"type": "string", "description": "Must be exactly one of: on, off, toggle."}},
          ["state"])
    def toggle_sports_widget(a):
        """Toggles the sports widget visibility."""
        import urllib.request
        import json
        state = a.get("state", "toggle")
        try:
            # emit to the webserver so the overlay picks it up
            data = json.dumps({"kind": "sports_toggle", "text": state}).encode()
            req = urllib.request.Request("http://127.0.0.1:8770/emit", data=data, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=2)
            return f"Sports widget {state}."
        except Exception as e:
            return f"Failed to toggle sports widget: {e}"

    schemas = []
    has_google = (config.vault_path / "credentials.json").exists() or Path.home().joinpath(".credentials", "credentials.json").exists()
    for s, _ in reg.values():
        f = s.get("function", {})
        name = f.get("name", "")
        # Drop inactive tool suites to save thousands of tokens!
        if not has_google and name.startswith("google_"):
            continue
            
        f.pop("description", None)
        for p_val in f.get("parameters", {}).get("properties", {}).values():
            p_val.pop("description", None)
        schemas.append(s)

    async def dispatch(name: str, args: dict) -> str:
        entry = reg.get(name)
        if not entry:
            return f"unknown tool: {name}"
        try:
            res = entry[1](args or {})
            return await res if asyncio.iscoroutine(res) else str(res)
        except Exception as exc:  # noqa: BLE001
            return f"tool error ({name}): {exc}"

    return schemas, dispatch
