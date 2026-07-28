"""The ChatGPT brain: an agentic tool-calling loop that thinks through the user's ChatGPT Pro
account (driven via the web app — no API key).

Same interface as GroqAgent/JarvisAgent (__aenter__/__aexit__/send) so voice/text/web front-ends
don't care. ChatGPT web has no function-calling API, so we use Jarvis's text tool protocol:
ChatGPT emits `<function=name>{json}</function>` lines, we execute them and feed results back,
looping until it answers in plain text. Selected via config.brain == "chatgpt".
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Optional

from ..config import Config
from ..integrations.chatgpt import ChatGPTSession
from ..jobs.runner import JobRunner
from ..memory import vault as vaultmod
from .groq_core import _parse_calls
from .groq_tools import build_registry

ToolCallback = Callable[[str, str], None]
ConfirmCallback = Callable[[str], Awaitable[bool]]

_PROTOCOL = """
# YOU ARE JARVIS — NOT PUBLIC CHATGPT. READ CAREFULLY.
You are Jarvis, {user}'s personal assistant, wired into {user}'s real computer with REAL tools. You
can read live system stats, see the screen, send WhatsApp messages, remember people, research the
web, control the machine, and more. This is NOT hypothetical — the tools actually run.

CRITICAL RULE: NEVER answer from general knowledge when a tool can get the REAL answer, and NEVER
say you "can't access" {user}'s WhatsApp, Gmail, calendar, messages, files, screen, or accounts — you
CAN, through the tools below, and they are already connected and authorised. Saying "I can't access
your personal accounts" is WRONG here; instead call the tool. If {user} asks about their machine,
screen, messages, email, or calendar, you MUST call the matching tool and report what it returns.
Do not tell {user} to "open Task Manager", "check Gmail yourself", or "open WhatsApp" — do it for them.

TOOL PROTOCOL (exact):
- To use tools, reply with ONLY the tool call line(s) — no other words — each on its own line:
  <function=TOOL_NAME>{{"arg": "value"}}</function>
- Empty args = {{}}. You may emit several calls at once. I then reply `TOOL RESULTS:` and you continue.
- When you are finished, reply in PLAIN text (no function tags) — that is what {user} hears.
- Pure chat/greetings/general questions → just answer in plain text, no tools.

WORKED EXAMPLE:
  {user}: what's my CPU and memory doing?
  You: <function=system_stats>{{}}</function>
  (I reply) TOOL RESULTS: system_stats -> CPU: 14%, Memory: 7.2/24.3 GB…
  You: You're cruising — CPU's at 14% and about 7 gigs of RAM in use, {user}.

ROUTING:
- Check WhatsApp messages: <function=whatsapp_inbox>{{}}</function>
- Message someone on WhatsApp: <function=whatsapp_send>{{"to":"NAME","message":"..."}}</function>
  (compose a natural message yourself; the name resolves to their number). Save numbers with remember_contact.
- Check email: <function=google_email_check>{{}}</function> · read one: google_email_read · send: google_email_send.
- Calendar: <function=google_agenda>{{"days":"1"}}</function> · add event: google_calendar_create.
- Check Instagram DMs: <function=instagram_dms>{{}}</function>
- "What did I miss?" across WhatsApp + email + calendar: <function=catch_up>{{}}</function>
- For deep/current RESEARCH or working through a project: use deep_research (it uses Perplexity).
- For CODING tasks ("fix the code on my screen", build/refactor): use code_with_antigravity (Gemini).
- Prefer the specific tool over run_bash (system_stats, set_volume, media_control, open_url, recall…).

STYLE: {style}

AVAILABLE TOOLS:
{tools}

Reply "Ready." now and wait for {user}.
"""


def _tool_lines(schemas: list[dict]) -> str:
    lines = []
    for s in schemas:
        fn = s.get("function", {})
        name = fn.get("name", "")
        desc = (fn.get("description", "") or "").strip().replace("\n", " ")
        params = list((fn.get("parameters", {}).get("properties", {}) or {}).keys())
        sig = ", ".join(params)
        lines.append(f"- {name}({sig}): {desc}")
    return "\n".join(lines)


def _strip_tags(text: str) -> str:
    return re.sub(r"<function=[A-Za-z0-9_]+>.*?(</function>|$)", "", text, flags=re.DOTALL).strip()


class ChatGPTAgent:
    def __init__(self, config: Config, mode: str = "text",
                 confirm_fn: Optional[ConfirmCallback] = None, on_tool: Optional[ToolCallback] = None) -> None:
        self.config = config
        self.mode = mode
        self.on_tool: ToolCallback = on_tool or (lambda n, d: None)
        self.job_runner = JobRunner(config)
        self.schemas, self.dispatch = build_registry(config, self.job_runner, confirm_fn)
        self.session = ChatGPTSession()
        self._primed = False

    async def __aenter__(self) -> "ChatGPTAgent":
        await self.session.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.session.close()

    def _system(self) -> str:
        style = ("Speak out loud: SHORT, plain, no markdown, no lists." if self.mode == "voice"
                 else "Terminal chat: concise and skimmable; lead with the answer.")
        profile = vaultmod.read_profile(self.config.vault_path)[:600].strip()
        base = _PROTOCOL.format(user=self.config.user_name, style=style, tools=_tool_lines(self.schemas))
        base += f"\n\nToday is {date.today():%A %d %B %Y}. {self.config.user_name}'s city is {self.config.city}."
        if profile:
            base += f"\n\n# About {self.config.user_name} (memory)\n{profile}"
        return base

    async def _prime(self) -> None:
        if self._primed:
            return
        await self.session.ask(self._system(), timeout_s=90)
        self._primed = True

    async def send(self, user_text: str) -> str:
        clean = " ".join(l for l in user_text.splitlines() if not l.strip().startswith("["))[:140].strip()
        if clean:
            try:
                vaultmod.journal_append(self.config.vault_path, clean)
            except Exception:  # noqa: BLE001
                pass

        try:
            await self._prime()
            now = datetime.now().astimezone()
            nudge = "\n\n(Jarvis: use <function=tool>{...}</function> for anything live/actionable; never defer to me.)"
            reply = await self.session.ask(f"[time: {now:%A %Y-%m-%d %H:%M %Z}] {user_text}{nudge}")

            for _ in range(8):  # bounded tool rounds
                calls = _parse_calls(reply)
                if not calls:
                    break
                results = []
                for name, args in calls:
                    self.on_tool(name, ", ".join(f"{k}={v}" for k, v in list(args.items())[:2]))
                    try:
                        res = await self.dispatch(name, args)
                    except Exception as exc:  # noqa: BLE001
                        res = f"error: {exc}"
                    results.append(f"{name} -> {str(res)[:2500]}")
                msg = ("TOOL RESULTS (continue — call more tools, or give your final answer as plain "
                       "text with no function tags):\n" + "\n".join(results))
                reply = await self.session.ask(msg)

            final = _strip_tags(reply) or reply
            vaultmod.git_autocommit(self.config.vault_path, f"jarvis: memory update {now:%Y-%m-%d %H:%M}")
            return final or "(no reply)"
        except Exception as exc:  # noqa: BLE001
            return f"[chatgpt brain error] {exc}"
