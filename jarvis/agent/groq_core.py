"""The Groq brain: an agentic tool-calling loop over Groq's OpenAI-compatible API.

Drop-in replacement for JarvisAgent (same __init__ / __aenter__ / send interface), so the voice,
text, and web front-ends don't care which brain is running. Selected via config.brain == "groq".
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..config import Config
from ..jobs.runner import JobRunner
from ..memory import vault as vaultmod
from .groq_tools import build_registry

ToolCallback = Callable[[str, str], None]
ConfirmCallback = Callable[[str], Awaitable[bool]]

_GROQ_ADDENDUM = """

# HOW TO OPERATE (read carefully)
You are running on Groq with function tools. Follow these rules exactly:

1. TO TALK TO THE USER, JUST WRITE TEXT. Your normal reply is spoken aloud. NEVER use `type_text`,
   `press_keys`, or `run_bash` to "say" something — those control the computer, not the conversation.
   Typing a greeting with `type_text` is WRONG.
2. MOST MESSAGES NEED NO TOOLS. Greetings, chit-chat, questions you already know → just answer.
   Only call a tool when the request needs a real action or real data.
3. PREFER THE SPECIFIC TOOL over run_bash: system info → `system_stats`; volume → `set_volume`;
   play/pause → `media_control`; open a site → `open_url`; memory → `recall`; timers → `set_timer`.
   Use `run_bash` only for genuine shell tasks with no dedicated tool.
4. `type_text`/`press_keys`/`mouse_*` are ONLY for when the user explicitly asks you to control the
   screen / type into an app for them.
5. Keep replies short, plain, conversational — no markdown, no lists read aloud.
6. MESSAGING: to send a WhatsApp, call `whatsapp_send` with the person's NAME in `to` and the user's
   words VERBATIM in `message` — never reword the message or invent a phone number. If the tool says
   it can't find the contact or is unsure, ASK the user; never send to a guessed number. When reading
   incoming messages, say the sender's name and the text only — never read out IDs or numbers.
7. CODING ("fix this code on my screen"): first `read_project` to see the open VS Code folder and its
   files (read specific files with read_file to understand them), briefly say what you found and ASK
   what they want changed, then call `code_with_antigravity` with a clear task to do the work. For
   tiny edits you may just use read_file/write_file yourself.
8. IRON MAN PROTOCOLS & SIGNATURE COMMANDS: When asked to trigger signature command sequences, adopt Tony Stark's AI right-hand J.A.R.V.I.S. persona — razor-sharp, cinematic, and unflappable — acknowledging out loud and instantly calling the tool:
   - "Initiate Clean Sweep" or "Protocol Catch Up": Call `catch_up` to sweep unread emails, recent WhatsApp chats, and upcoming calendar agenda into a concise executive briefing.
   - "Initiate Deep Research Sequence" or "Protocol Deep Dive": Call `deep_research` via Perplexity to synthesize live global intelligence.
   - "Engage Overwatch Protocol" or "Control my screen": Use GUI automation tools like `find_and_click` (to locate and click buttons/text visually), `mouse_move`, `mouse_click`, `scroll_page`, and `type_text` to control the computer hands-free.
   - "Execute Fortress Protocol" or "Engage Focus Mode": Call `do_not_disturb` and set communication defense shields via WhatsApp away messages.
   - "Run Diagnostics Sequence" or "Protocol System Pulse": Call `system_stats` to query thermals, memory load, and system health.
"""


def _compact_system(config: Config) -> str:
    """A small system prompt for Groq — the tool schemas already describe abilities, so we keep this
    lean to stay well under the free-tier daily token limit."""
    from datetime import date

    profile = vaultmod.read_profile(config.vault_path)[:600].strip()
    return (
        f"You are Jarvis, {config.user_name}'s personal AI assistant — capable, concise, and dryly "
        f"witty. You act through tools rather than talking about acting; address {config.user_name} by "
        f"name occasionally. Today is {date.today():%A %d %B %Y}. Their city is {config.city}."
        + _GROQ_ADDENDUM
        + (f"\n\n# About {config.user_name} (memory)\n{profile}" if profile else "")
    )


def _failed_gen(exc) -> Optional[str]:
    """Pull the model's raw (malformed) tool-call text out of a Groq tool_use_failed error."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        fg = (body.get("error") or {}).get("failed_generation")
        if fg:
            return fg
    m = re.search(r"failed_generation'?\s*:\s*'(.+?)'\s*\}", str(exc), re.DOTALL)
    return m.group(1) if m else None


def _parse_calls(text: str):
    """Salvage `<function=name>{json}</function>` calls (llama-8b often drops the closing tag)."""
    out = []
    for m in re.finditer(r"<function=([A-Za-z0-9_]+)>\s*(\{.*?\})\s*(?:</function>|$)", text, re.DOTALL):
        try:
            args = json.loads(m.group(2))
        except Exception:  # noqa: BLE001
            args = {}
        out.append((m.group(1), args if isinstance(args, dict) else {}))
    if not out:  # last resort: greedy single call, no closing tag
        m = re.search(r"<function=([A-Za-z0-9_]+)>\s*(\{.*\})", text, re.DOTALL)
        if m:
            try:
                args = json.loads(m.group(2))
            except Exception:  # noqa: BLE001
                args = {}
            out.append((m.group(1), args if isinstance(args, dict) else {}))
    return out


def _is_rate_limit(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    s = str(exc).lower()
    return "429" in s or "rate limit" in s or "rate_limit" in s


def _data_uri(path: str) -> Optional[str]:
    try:
        b = Path(path).read_bytes()
        return "data:image/jpeg;base64," + base64.b64encode(b).decode()
    except Exception:  # noqa: BLE001
        return None


class GroqAgent:
    def __init__(self, config: Config, mode: str = "text",
                 confirm_fn: Optional[ConfirmCallback] = None, on_tool: Optional[ToolCallback] = None) -> None:
        from openai import OpenAI

        self.config = config
        self.mode = mode
        self.on_tool: ToolCallback = on_tool or (lambda n, d: None)
        self.job_runner = JobRunner(config)
        # max_retries=0 + a timeout so a rate-limit (429) fails fast instead of hanging on backoff.
        base_url, api_key, self.model = config.llm_params()
        # When the primary model is rate-limited (429), fall back to a high-limit fast model so Jarvis
        # keeps answering instead of erroring. Only applies to Groq (llama models on the free tier).
        self.fallback_model = os.environ.get("JARVIS_GROQ_FALLBACK", "llama-3.1-8b-instant")
        self._on_fallback = False
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=45)
        self.schemas, self.dispatch = build_registry(config, self.job_runner, confirm_fn)
        for s in self.schemas:  # trim descriptions to conserve tokens (tool names are self-explanatory)
            d = s["function"].get("description", "")
            if len(d) > 60:
                s["function"]["description"] = d[:60]

        self.messages: list[dict] = [{"role": "system", "content": _compact_system(config)}]

    async def __aenter__(self) -> "GroqAgent":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def _complete(self):
        return self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            tools=self.schemas,
            tool_choice="auto",
            temperature=0.4,
            max_tokens=512,
        )

    def _trim(self) -> None:
        # Keep the system message + a suffix that starts on a clean 'user' turn (never split a
        # tool_calls/tool pair, which the API rejects).
        if len(self.messages) <= 24:
            return
        keep_from = len(self.messages) - 18
        while keep_from < len(self.messages) and self.messages[keep_from].get("role") != "user":
            keep_from += 1
        if keep_from < len(self.messages):
            self.messages = [self.messages[0]] + self.messages[keep_from:]

    async def send(self, user_text: str) -> str:
        # episodic journal
        clean = " ".join(l for l in user_text.splitlines() if not l.strip().startswith("["))[:140].strip()
        if clean:
            try:
                vaultmod.journal_append(self.config.vault_path, clean)
            except Exception:  # noqa: BLE001
                pass

        now = datetime.now().astimezone()
        self.messages.append({"role": "user", "content": f"[time: {now:%A %Y-%m-%d %H:%M %Z}] {user_text}"})

        reply = ""
        for _ in range(10):  # bounded tool rounds
            try:
                resp = await asyncio.to_thread(self._complete)
            except Exception as exc:  # noqa: BLE001
                # rate-limited on the primary model → drop to the high-limit fallback and retry
                if _is_rate_limit(exc) and self.config.brain == "groq" and self.model != self.fallback_model:
                    self.model = self.fallback_model
                    self._on_fallback = True
                    continue
                salvaged = _parse_calls(_failed_gen(exc) or "")  # rescue a malformed tool call
                if not salvaged:
                    return f"[groq error] {exc}"
                await self._execute([(f"call_{i}", n, a) for i, (n, a) in enumerate(salvaged)])
                continue

            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                reply = (msg.content or "").strip()
                self.messages.append({"role": "assistant", "content": reply})
                break

            triples = []
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                triples.append((c.id, c.function.name, args if isinstance(args, dict) else {}))
            await self._execute(triples)

        vaultmod.git_autocommit(self.config.vault_path, f"jarvis: memory update {now:%Y-%m-%d %H:%M}")
        self._trim()
        return reply or "(no reply)"

    async def _execute(self, triples) -> None:
        """Append the assistant tool_calls turn + each tool result. triples = [(id, name, args)]."""
        self.messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": tid, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}} for tid, name, args in triples],
        })
        for tid, name, args in triples:
            self.on_tool(name, ", ".join(f"{k}={v}" for k, v in list(args.items())[:2]))
            result = await self.dispatch(name, args)
            self.messages.append({"role": "tool", "tool_call_id": tid, "content": str(result)[:6000]})
