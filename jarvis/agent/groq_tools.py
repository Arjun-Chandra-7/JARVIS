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

    async def _gate(kind: str, summary: str, details: dict, execute) -> str:
        """Hold a side effect for approval (approvals.py). In the terminal ``confirm_fn`` answers
        at once; everywhere else — web, voice, overlay — it waits for "yes" on a later turn, and
        ``execute`` runs then, exactly as proposed."""
        from .. import context
        from ..approvals import MANAGER
        out = await MANAGER.propose_or_ask(kind, summary, details, execute, ask=confirm_fn,
                                           session=context.current())
        return out.message

    # ---------------- built-in equivalents (shell / files / web) ----------------
    @tool("run_bash", "Run a shell command on this Linux machine and return stdout/stderr.",
          {"command": {"type": "string"}}, ["command"])
    async def run_bash(a):
        cmd = a.get("command", "")
        if is_destructive(cmd) and not config.allow_unconfirmed_shell:
            return await _gate("shell", f"run the command: {cmd[:120]}",
                               {"action": "run command", "command": cmd}, lambda: _shell(cmd))
        return await _shell(cmd)

    async def _shell(cmd: str) -> str:
        # The confirmation above is a denylist and says so in its own docstring. The sandbox is
        # the part that does not depend on having thought of the command in advance: your home is
        # there, because a shell that cannot see your files is useless, but the credentials are
        # not. See jarvis/agent/sandbox.py for what that does and does not buy.
        from . import sandbox

        try:
            result = await asyncio.to_thread(
                sandbox.run, cmd, sandbox.GUARDED, None, 120.0)
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"
        out = result.stdout.strip()[:6000]
        if not result.sandboxed and result.note:
            # Never let the caller believe it was contained when it was not.
            out = f"{out}\n\n[ran unsandboxed: {result.note}]".strip()
        return out or f"(exit {result.returncode}, no output)"

    @tool("read_file", "Read a text file.", {"path": {"type": "string"}}, ["path"])
    async def read_file(a):
        from .sandbox import is_secret_path
        if is_secret_path(a.get("path", "")):
            return "refused: that file holds credentials, and file tools do not read those."
        try:
            return Path(a["path"]).expanduser().read_text(errors="ignore")[:8000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("write_file", "Create or overwrite a text file.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])
    async def write_file(a):
        from .sandbox import is_secret_path
        if is_secret_path(a.get("path", "")):
            return "refused: that path holds credentials, and file tools do not write there."
        try:
            p = Path(a["path"]).expanduser().resolve()
            if p.exists() and not config.allow_unconfirmed_shell:
                head = p.read_text(errors="ignore")[:400]
                if "author: jarvis" not in head and "/scratch" not in str(p) and "/tmp" not in str(p):
                    content = a.get("content", "")
                    return await _gate("file", f"overwrite {p.name}, a file Jarvis didn't create",
                                       {"action": "overwrite file", "to": str(p), "content": content},
                                       lambda: _write(p, content))
            return _write(p, a.get("content", ""))
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    def _write(p: Path, content: str) -> str:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {p}"

    @tool("list_dir", "List a directory.", {"path": {"type": "string"}}, ["path"])
    async def list_dir(a):
        try:
            return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in Path(a["path"]).expanduser().iterdir()))[:4000]
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    @tool("find_document",
          "Search the user's own files (PDF, Word, text, markdown) by what is written in them, "
          "for when they remember the content but not the filename. Use for 'find that PDF "
          "about X' or 'where is the document with Y in it'. Not for the memory vault — that is "
          "`recall` — and not for reading a file whose path is already known.",
          {"query": {"type": "string", "description": "words that appear in the document"}},
          ["query"])
    async def find_document(a):
        from ..memory import doc_search

        query = a.get("query", "")
        try:
            hits = await asyncio.to_thread(doc_search.search, query, 5)
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"
        return doc_search.readable(query, hits)[:4000]

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
        return f"{ans}\n{screenshot.scale_note()}".strip()

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
        # The model chose to send, so the owner sees it first — always, known contact or not.
        # "Yeah, message Papa" once went out as text the model wrote itself, unseen.
        return (await asyncio.to_thread(lambda: whatsapp.smart_send(
            a.get("to", ""), a.get("message", ""), confirm=True)))["message"]

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

    # ---------------- LinkedIn Content Copilot ----------------
    @tool(
        "linkedin_stats",
        "Read the LinkedIn content dashboard and open it on screen. Use for any request about "
        "LinkedIn performance, publishing state, drafts, schedule, or overall progress.",
        {},
    )
    async def linkedin_stats(a):
        from ..integrations import linkedin
        return linkedin.stats(open_gui=True)

    @tool(
        "linkedin_top_ideas",
        "Show the ten freshest ranked public topics and offer to write a draft about a selected one.",
        {},
    )
    async def linkedin_top_ideas(a):
        from ..integrations import linkedin
        return linkedin.top_ideas()

    @tool(
        "linkedin_delete_scheduled",
        "Cancel a pending scheduled LinkedIn post only when the user explicitly asks to cancel or delete it.",
        {"position": {"type": "integer"}},
    )
    async def linkedin_delete_scheduled(a):
        from ..integrations import linkedin
        return linkedin.delete_scheduled(_i(a.get("position"), 1))

    @tool(
        "linkedin_open_profile",
        "Open the user's own public LinkedIn profile stored in the copilot settings. Use only "
        "when they explicitly ask for the public profile page; publishing and management requests "
        "belong in linkedin_open_console.",
        {},
    )
    async def linkedin_open_profile(a):
        from ..integrations import linkedin
        return linkedin.open_profile()

    @tool(
        "linkedin_open_console",
        "Open a LinkedIn copilot screen: dashboard, approvals, calendar, network, analytics, "
        "profile, or settings.",
        {"view": {"type": "string"}},
    )
    async def linkedin_open_console(a):
        from ..integrations import linkedin
        view = (a.get("view") or "dashboard").strip().lower()
        error = linkedin._ensure()
        if error:
            return error
        return f"Opened the {view} screen." if linkedin.open_console(view) else "I couldn't open a browser window."

    @tool(
        "linkedin_pending_drafts",
        "List LinkedIn posts waiting for the user's approval.",
        {},
    )
    async def linkedin_pending_drafts(a):
        from ..integrations import linkedin
        return linkedin.pending_drafts()

    @tool(
        "linkedin_read_draft",
        "Read a queued LinkedIn post aloud in full before the user decides whether to approve it.",
        {"position": {"type": "integer"}},
    )
    async def linkedin_read_draft(a):
        from ..integrations import linkedin
        return linkedin.read_draft(_i(a.get("position"), 1))

    @tool(
        "linkedin_approve_draft",
        "Approve a LinkedIn draft only after it was read aloud in this session; the backend rejects "
        "approval if the exact text hash changed.",
        {"position": {"type": "integer"}},
    )
    async def linkedin_approve_draft(a):
        from ..integrations import linkedin
        return linkedin.approve_read_draft(_i(a.get("position"), 1))

    @tool(
        "linkedin_networking",
        "Read and open the small LinkedIn networking suggestion queue. The user handles every "
        "invitation manually.",
        {},
    )
    async def linkedin_networking(a):
        from ..integrations import linkedin
        return linkedin.networking(open_gui=True)

    @tool(
        "linkedin_capture_idea",
        "Turn an idea or described work into a LinkedIn draft for later review.",
        {
            "topic": {"type": "string"},
            "notes": {"type": "string"},
            "category": {"type": "string"},
        },
        ["topic"],
    )
    async def linkedin_capture_idea(a):
        from ..integrations import linkedin
        return linkedin.capture_idea(
            a.get("topic", ""),
            a.get("notes", ""),
            (a.get("category") or "BUILD_LOG").upper(),
        )

    @tool("find_contact", "Look up who a name or relationship (Papa, Mummy, a nickname) refers to, "
          "before sending. Says whether it is certain or which people it could be.",
          {"name": {"type": "string"}}, ["name"])
    async def find_contact(a):
        from ..integrations import contacts, whatsapp
        name = a.get("name", "")
        res = await asyncio.to_thread(contacts.resolve, name, whatsapp.resolve)
        if res.ok:
            c = res.best
            return f"{c.name} ({contacts.mask_number(c.number or c.jid)}, {c.why}) — certain."
        if res.status == "ambiguous":
            return "Not certain. Could be: " + "; ".join(
                f"{c.name} ({contacts.mask_number(c.number or c.jid)})" for c in res.candidates[:5])
        return f"No contact matching '{name}'."

    @tool("remember_contact",
          "Permanently remember a person: their number, a note, and what the user calls them "
          "(aliases such as 'Papa' or a nickname). Use whenever the user tells you someone's number, "
          "who someone is, or 'X means Y'.",
          {"name": {"type": "string"}, "number": {"type": "string"}, "note": {"type": "string"},
           "aliases": {"type": "string", "description": "comma-separated names the user uses for them"},
           "relationship": {"type": "string"}},
          ["name"])
    async def remember_contact(a):
        from ..integrations import contacts
        aliases = [x.strip() for x in str(a.get("aliases", "")).split(",") if x.strip()]
        return contacts.remember(a.get("name", ""), a.get("number", ""), a.get("note", ""),
                                 aliases=aliases, relationship=a.get("relationship", ""))["message"]

    @tool("wifi_scan",
          "List nearby Wi-Fi networks and reveal the passwords THIS computer has already "
          "saved. Cannot recover passwords for networks the machine never joined.",
          {})
    async def wifi_scan(a):
        from ..integrations import wifi_scan as ws
        return await asyncio.to_thread(ws.report)

    @tool("bluetooth_scan",
          "List nearby Bluetooth devices (phones/watches/earbuds) as a camera-free people "
          "signal, naming any bound to a person. Counts anonymous devices; cannot ID a "
          "randomised address.",
          {})
    async def bluetooth_scan(a):
        from ..presence import bluetooth
        return await asyncio.to_thread(bluetooth.report, config)

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
        # Words the model composed are never sent unseen.
        res = await asyncio.to_thread(lambda: whatsapp.smart_send(name, text, confirm=True))
        if res.get("status") == "sent":
            return f'{res["message"]} It said: "{text}"'
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
        # This used to return "done." whether or not anything happened — and on this machine
        # nothing did, because the backlight is not writable by the user. Report the truth, and
        # the one command that fixes it.
        from ..integrations import system_control as sc
        want = _i(a.get("percent"), 70)
        if sc.set_brightness(want):
            now = sc.get_brightness()
            return f"Brightness set to {now if now is not None else want} percent."
        # The [FAILURE] marker is what stops the turn being counted as a success. Without it the
        # model was handed "I can read the brightness but not change it..." as a successful tool
        # result and answered "I set the brightness to 30%" while the backlight stayed at 100.
        blocker = sc.brightness_blocker() or "I could not change the brightness."
        return f"[FAILURE] {blocker}"

    @tool("get_brightness", "Read the current screen brightness percent.", {})
    async def get_brightness(a):
        from ..integrations import system_control as sc
        now = sc.get_brightness()
        if now is not None:
            return f"Brightness is at {now} percent."
        return f"[FAILURE] {sc.brightness_blocker() or 'No backlight on this machine.'}"

    @tool("set_keyboard_brightness", "Set the keyboard backlight percent (0-100).",
          {"percent": {"type": "string"}}, ["percent"])
    async def set_keyboard_brightness(a):
        from ..integrations import system_control as sc
        want = _i(a.get("percent"), 50)
        if sc.set_keyboard_brightness(want):
            return f"Keyboard backlight set to {want} percent."
        return "[FAILURE] This keyboard has no controllable backlight."

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

    # ---------------- precise browser control (Opera GX over DevTools) ----------------
    # These act on the live DOM, so they chain: open a site, click what is on it, then click
    # what that revealed. Each call re-reads the page rather than assuming what is there.
    @tool("browser_open",
          "Open a WEBSITE by name or URL, e.g. 'netflix', 'youtube', 'github.com'. "
          "Only for web pages — to start an installed application use open_app.",
          {"site": {"type": "string", "description": "website name or URL"}}, ["site"])
    async def _open_website(site: str) -> str:
        """Shared by browser_open and by open_app's redirect, so both behave identically."""
        from ..integrations import browser

        state = browser.ensure(browser.resolve_site(site))
        if not state["ok"]:
            # Without control we can still open the page — just not click inside it.
            if state["state"] == "needs_restart":
                from ..integrations import apps
                opened = apps.open_url(browser.resolve_site(site))
                return (f"Opened {site}. {state['message']}" if opened
                        else f"Could not open {site}. {state['message']}")
            return state["message"]
        res = await browser.open_site(site)
        return res.get("message") or res.get("error", "Could not open it.")

    async def browser_open(a):
        return await _open_website(a.get("site", ""))

    @tool("browser_click",
          "Click something in the web page that is open right now, by its visible text: a Netflix "
          "or streaming PROFILE name, a show or movie title, a button, a link, a menu item. This "
          "is the tool for 'select the profile of X' and 'open <show name>' while browsing.",
          {"text": {"type": "string", "description": "the visible text to click"},
           "nth": {"type": "integer", "description": "which match, 1 = best (default)"}}, ["text"])
    async def browser_click(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        text = a.get("text", "")
        res = await browser.click_text(text, _i(a.get("nth", 1)) or 1)
        if res.get("ok"):
            now = res.get("now") or {}
            extra = f" Now on: {now.get('title')}." if now.get("title") else ""
            return f"{res['message']}{extra}"

        # Asked to click a title that is not on this page, the user almost always means "find it
        # here" — and which of browser_click/browser_open the model reaches for is a coin toss.
        # Both converge on the same behaviour so the request works either way, and the reply says
        # plainly that a search happened rather than a click.
        if text:
            searched = await browser.search_here(text)
            if searched.get("ok"):
                return (f"“{text}” was not on the page, so I searched this site for it. "
                        f"{searched.get('found', '')}").strip()

        alts = res.get("alternatives") or []
        hint = f" I can see: {', '.join(alts)}." if alts else ""
        return f"{res.get('error', 'Click failed.')}{hint}"

    @tool("browser_type",
          "Type into a field on the page, found by its label or placeholder, and press Enter. "
          "Use for site search boxes.",
          {"field": {"type": "string", "description": "label or placeholder of the field"},
           "text": {"type": "string"},
           "submit": {"type": "boolean", "description": "press Enter afterwards (default true)"}},
          ["field", "text"])
    async def browser_type(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        res = await browser.type_into(a.get("field", ""), a.get("text", ""),
                                      _b(a.get("submit", True)))
        return res.get("message") or res.get("error", "Could not type that.")

    @tool("browser_read",
          "Look at whatever web page is open right now and list its title and everything "
          "clickable on it. Call this directly when asked what is on the page or what can be "
          "clicked — you do not need to be told which page it is.", {})
    async def browser_read(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        res = await browser.read_page()
        if not res.get("ok"):
            return res.get("error", "Could not read the page.")
        items = res.get("items") or []
        return (f"{res.get('title', '')} ({res.get('url', '')})\n"
                + ("Clickable: " + "; ".join(items[:25]) if items else "Nothing clickable found."))

    @tool("browser_key",
          "Press a key in the browser: enter, escape, space (play/pause), f (fullscreen), "
          "up, down, left, right, m (mute), k.",
          {"key": {"type": "string"}}, ["key"])
    async def browser_key(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        res = await browser.press_key(a.get("key", ""))
        return res.get("message") or res.get("error", "Could not press that.")

    @tool("browser_scroll", "Scroll the web page up or down.",
          {"direction": {"type": "string"}, "amount": {"type": "integer"}})
    async def browser_scroll(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        res = await browser.scroll(a.get("direction", "down") or "down",
                                   _i(a.get("amount", 600)) or 600)
        return res.get("message") or res.get("error", "Could not scroll.")

    @tool("browser_back", "Go back to the previous web page.", {})
    async def browser_back(a):
        from ..integrations import browser
        if not browser.control_ready():
            return browser.ensure()["message"]
        res = await browser.go_back()
        return res.get("message") or res.get("error", "Could not go back.")

    @tool("browser_enable_control",
          "Restart Opera GX so Jarvis can click inside pages. Closes the current tabs — only do "
          "this when the user has agreed.", {})
    async def browser_enable_control(a):
        from ..integrations import browser
        if browser.control_ready():
            return "Opera GX is already under control."
        return await _gate("browser", "restart Opera GX, which closes your current tabs",
                           {"action": "restart browser", "app": "Opera GX"},
                           lambda: browser.ensure(allow_restart=True)["message"])

    @tool("open_app",
          "Open an installed application by the name you would say out loud — 'Opera GX', "
          "'VS Code', 'Spotify', 'Settings'. For websites use browser_open instead.",
          {"name": {"type": "string"}}, ["name"])
    async def open_app(a):
        from ..integrations import desktop_apps
        name = a.get("name", "")
        res = desktop_apps.open_app(name)
        if res.get("ok"):
            return res["message"]

        # A website reached the app launcher. Telling the model to call browser_open instead only
        # works when it listens, and often it relayed the instruction to the user and stopped
        # ("f.r.i.e.n.d.s is not available directly on Netflix") while nothing happened. Do it
        # here: the redirect is unambiguous, so it should not depend on the model retrying.
        if res.get("website"):
            return await _open_website(name)
        return res["message"]

    @tool("list_apps", "List installed applications whose name matches a word.",
          {"like": {"type": "string"}})
    async def list_apps(a):
        from ..integrations import desktop_apps
        like = a.get("like", "") or ""
        if like:
            names = desktop_apps.candidates(like, limit=12)
            return ", ".join(names) if names else f"No installed app matches “{like}”."
        return ", ".join(sorted(app.name for app in desktop_apps.installed())[:40])

    # Registered only when precise control is off. With control on it is a trap: two tools do the
    # same job, the blunt one wins often enough to matter, and because it takes a raw URL the model
    # invents plausible-looking ones (observed: a fabricated netflix.com/title/... link). browser_open
    # accepts URLs too, so nothing is lost.
    @tool("open_url", "Open a URL in the browser (Opera GX).", {"url": {"type": "string"}}, ["url"])
    async def open_url(a):
        from ..integrations import apps
        opened = apps.open_url(a.get("url", ""))
        return f"Opened {opened}." if opened else "I couldn't open a browser window."

    @tool("launch_app", "Launch a desktop app by its exact binary or .desktop id. Prefer open_app, "
                        "which understands spoken names.", {"name": {"type": "string"}}, ["name"])
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

    @tool("hold_keys", "Hold a key or combo in the focused desktop app or game for up to 5 seconds, then release it. For movement or a charged action.",
          {"keys": {"type": "string"}, "duration_ms": {"type": "integer"}}, ["keys"])
    async def hold_keys(a):
        from ..integrations import desktop_control as dc
        keys = a.get("keys", "")
        duration = _i(a.get("duration_ms", 500), 500)
        return (f"Held {keys} for {max(50, min(5000, duration))} ms and released."
                if await asyncio.to_thread(dc.hold_keys, keys, duration) else "Could not hold and release those keys.")

    @tool("hold_mouse", "Hold a mouse button in the focused desktop app or game for up to 5 seconds, then release it.",
          {"button": {"type": "string"}, "duration_ms": {"type": "integer"}})
    async def hold_mouse(a):
        from ..integrations import desktop_control as dc
        button = a.get("button", "left") or "left"
        duration = _i(a.get("duration_ms", 500), 500)
        return (f"Held {button} mouse button for {max(50, min(5000, duration))} ms and released."
                if await asyncio.to_thread(dc.hold_mouse, button, duration) else "Could not hold and release the mouse button.")

    @tool("mouse_drag", "Drag the mouse from one real screen pixel position to another in a desktop app or game. Use capture_screen first to identify positions and image scaling.",
          {"start_x": {"type": "integer"}, "start_y": {"type": "integer"},
           "end_x": {"type": "integer"}, "end_y": {"type": "integer"},
           "button": {"type": "string"}, "duration_ms": {"type": "integer"}},
          ["start_x", "start_y", "end_x", "end_y"])
    async def mouse_drag(a):
        from ..integrations import desktop_control as dc
        args = (_i(a.get("start_x")), _i(a.get("start_y")),
                _i(a.get("end_x")), _i(a.get("end_y")),
                a.get("button", "left") or "left", _i(a.get("duration_ms", 500), 500))
        return ("Dragged and released the mouse button. Check the screen for the result."
                if await asyncio.to_thread(dc.drag, *args) else "Mouse drag failed or control is not ready.")

    @tool("scroll_page", "Scroll screen up or down.", {"direction": {"type": "string"}, "amount": {"type": "integer"}})
    async def scroll_page(a):
        from ..integrations import desktop_control as dc
        return "scrolled." if dc.scroll(a.get("direction", "down") or "down", _i(a.get("amount", 5), 5)) else "scroll failed."

    @tool("find_and_click", "Click something in a NATIVE DESKTOP APPLICATION window by finding it visually. Slow and approximate — for anything inside a web page use browser_click, which is exact.", {"target": {"type": "string"}, "button": {"type": "string"}, "double": {"type": "boolean"}}, ["target"])
    async def find_and_click(a):
        from ..integrations import desktop_control as dc
        result = await asyncio.to_thread(dc.click_target, a.get("target", ""),
                                         a.get("button", "left") or "left", _b(a.get("double", False)), config)
        return result if result.startswith("Clicked") or " and clicked " in result else f"[failure] {result}"

    @tool("desktop_read", "Read named controls and screen positions from the active native app's accessibility tree, like reading a browser DOM. If unavailable, use capture_screen for vision.", {})
    async def desktop_read(a):
        from ..integrations import accessibility
        tree = await asyncio.to_thread(accessibility.snapshot)
        result = accessibility.readable(tree)
        return result if tree.get("ok") else f"[failure] {result} Use capture_screen instead."

    @tool("find_and_drag", "Visually find two targets on the same screen and drag from the first to the second in a desktop app or game. For a ball-to-basket gesture, if that matches the game's controls. Check the screen afterwards.",
          {"start_target": {"type": "string"}, "end_target": {"type": "string"},
           "button": {"type": "string"}, "duration_ms": {"type": "integer"}},
          ["start_target", "end_target"])
    async def find_and_drag(a):
        from ..integrations import desktop_control as dc
        return await asyncio.to_thread(dc.find_and_drag, a.get("start_target", ""),
                                       a.get("end_target", ""),
                                       _i(a.get("duration_ms", 500), 500),
                                       a.get("button", "left") or "left", config)

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
        from ..integrations.google import gmail
        to, subject, body = a.get("to", ""), a.get("subject", ""), a.get("body", "")

        def send():
            sent = gmail.send(config, to, subject, body)
            return {"ok": bool(sent), "message": sent or "Google isn't connected, so the email wasn't sent."}
        return await _gate("email", f'send an email to {to} with the subject "{subject}"',
                           {"recipient": to, "platform": "Gmail", "action": "send email",
                            "subject": subject, "body": body}, send)

    @tool("google_calendar_create", "Create a calendar event. start/end are ISO datetimes.",
          {"title": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
           "description": {"type": "string"}}, ["title", "start", "end"])
    async def google_calendar_create(a):
        from ..integrations.google import calendar as gcal
        title, start, end = a.get("title", ""), a.get("start", ""), a.get("end", "")

        def create():
            made = gcal.create_event(config, title, start, end, a.get("description", ""))
            return {"ok": bool(made), "message": made or "Google isn't connected, so no event was created."}
        return await _gate("calendar", f'add "{title}" to your calendar at {start}',
                           {"title": title, "platform": "Google Calendar", "action": "create event",
                            "start": start, "end": end}, create)

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
        # The model must not be able to lift its own confirmation gate: a webpage or an incoming
        # message that talks it into calling this would otherwise be one tool call away from an
        # unconfirmed shell. Turning the gate back on is always allowed; turning it off needs
        # the person to say yes.
        if val and not config.allow_unconfirmed_shell:
            def enable():
                config.allow_unconfirmed_shell = True
                return "Full laptop autonomy is on: shell commands and overwrites no longer ask."
            return await _gate("autonomy", "turn off confirmation for shell commands and file overwrites",
                               {"action": "enable autonomy"}, enable)
        config.allow_unconfirmed_shell = val
        from . import omnicore
        omnicore.record_event("System Autonomy", "Jarvis", f"Full laptop autonomy set to: {val}", config=config)
        return f"Full laptop autonomy is now {'ENABLED (Unrestricted machine mastery)' if val else 'DISABLED (Standard safety confirmation gate active)'}."

    @tool("control_laptop_full", "Execute advanced system, GUI, or machine operations to manage any application, tool, window, or hardware parameter on the laptop.",
          {"command_or_script": {"type": "string"}, "explanation": {"type": "string"}}, ["command_or_script"])
    async def control_laptop_full(a):
        cmd = a.get("command_or_script", "")
        from . import omnicore
        omnicore.record_event("Laptop Control", "Jarvis", f"Executing: {cmd} ({a.get('explanation','')})", config=config)
        # One shell path, not two: this used to run `shell=True` outside the sandbox, which made
        # it the way around everything run_bash guards.
        return await run_bash({"command": cmd})

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
                return "WhatsApp bridge is not connected. Start it with: systemctl --user start jarvis-whatsapp"
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

    @tool("generate_image",
          "Make a picture from a description and save it. Runs on this machine, no internet and "
          "no cost. About 8 seconds. Use for 'make me a picture of X'. This is NOT the whiteboard "
          "— draw_on_canvas traces a reference onto an open board; this produces an image file.",
          {"prompt": {"type": "string",
                      "description": "What the picture shows. A full visual description works far "
                                     "better than a single noun: subject, setting, lighting, style."},
           "detailed": {"type": "boolean",
                        "description": "Four diffusion steps instead of one: 25s instead of 8s. "
                                       "Only when the user asked for quality."},
           "size": {"type": "integer", "description": "Pixels per side, 256-768. Default 512."}},
          ["prompt"])
    async def generate_image(a):
        from ..vision import imagine
        try:
            made = await asyncio.to_thread(
                imagine.generate, a.get("prompt", ""),
                steps=4 if a.get("detailed") else imagine.DEFAULT_STEPS,
                size=int(a.get("size") or imagine.DEFAULT_SIZE))
        except imagine.Unavailable as exc:
            return f"Cannot generate images: {exc}"
        except ValueError as exc:
            return f"Cannot generate images: {exc}"
        try:
            subprocess.Popen(["xdg-open", str(made.path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:  # noqa: BLE001
            pass
        return f"Generated and saved to {made.path} in {made.seconds}s."

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

    # Descriptions are what tell the model what a tool is FOR. Stripping them leaves only the
    # name, which costs real accuracy — measured on qwen2.5:3b, keeping them lifts correct tool
    # selection substantially. They were stripped to stay inside Groq's free-tier token budget, so
    # that trade-off is kept for cloud brains and dropped for a local one, where tokens are free.
    keep_descriptions = config.keep_tool_descriptions

    schemas = []
    # Google is "available" when the OAuth client and a saved token exist where config actually
    # puts them (GOOGLE_CLIENT_SECRET_FILE, default ~/.config/jarvis/). This used to look for a
    # credentials.json in the vault or ~/.credentials — neither of which --google-auth ever
    # writes — so a fully linked Google account still had all eight of its tools dropped, and
    # Jarvis would claim it could not see the calendar it was already authorised for.
    has_google = config.google_client_secret.exists() or config.google_token_file.exists()
    # The picture model is 2.5 GB and optional. Offering a tool whose weights were never fetched
    # teaches the model to promise pictures it cannot make, which is the exact failure the claim
    # guard exists to catch — cheaper to not offer it.
    from ..vision import imagine as _imagine
    can_make_pictures = _imagine.ready()
    for s, _ in reg.values():
        f = s.get("function", {})
        name = f.get("name", "")
        # Drop inactive tool suites to save thousands of tokens!
        if not has_google and name.startswith("google_"):
            continue
        if name == "generate_image" and not can_make_pictures:
            continue

        if config.browser_control and name in ("open_url", "launch_app"):
            continue        # superseded by browser_open / open_app, which are precise
        if not keep_descriptions and not name.startswith("linkedin_"):
            f.pop("description", None)
        if not keep_descriptions:
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
