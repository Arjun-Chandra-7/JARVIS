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
   - "Initiate Protocol Nexus" or automation requests: Call `trigger_automation` to activate n8n webhook workflows that connect to thousands of external apps and services, or use `list_automations`/`remember_automation` to manage them.
   - "Initiate Protocol Guardian" or OmniCore PA Shield: Use `process_incoming_communication`, `check_pa_status`, or `set_pa_status` to record everything (all texts/calls), detect implicit schedules (e.g. "tuition on 6:10"), and conduct autonomous 2-sided conversational PA interception when Arjun is out or in tuition.
   - "Engage Omni-Control" or full laptop control: Use `enable_full_laptop_autonomy` and `control_laptop_full` (along with GUI tools like `find_and_click` and `run_bash`) to command and automate all tools across the entire laptop without friction.
9. LINKEDIN: Route by semantic intent, never by matching a fixed phrase. Only an explicit request
   for the public profile page uses `linkedin_open_profile`; performance or metrics use
   `linkedin_stats`; publishing, scheduling, content, and general management use
   `linkedin_open_console`. Never claim a page opened unless the tool confirms it.
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
    if not out:
        # ChatGPT sometimes renders a selected LinkedIn tool as a display label
        # instead of function syntax. Normalize those model-generated labels so
        # a claimed action is always backed by a real dispatch.
        for plain in re.finditer(
            r"^\s*LinkedIn\s+(.+?)\s*[-:]\s*(.*)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        ):
            label = re.sub(r"\s+", " ", plain.group(1).strip().lower())
            detail = plain.group(2)
            if "profile" in label:
                out.append(("linkedin_open_profile", {}))
                continue
            if any(word in label for word in ("stat", "performance", "metric", "overview", "insight")):
                out.append(("linkedin_stats", {}))
                continue
            if any(
                word in label
                for word in ("console", "dashboard", "content", "calendar", "network", "analytics", "approval", "settings")
            ):
                view = re.search(
                    r"\b(dashboard|approvals|calendar|network|analytics|profile|settings)\b",
                    f"{label} {detail}",
                    re.IGNORECASE,
                )
                out.append(
                    (
                        "linkedin_open_console",
                        {"view": view.group(1).lower() if view else "dashboard"},
                    )
                )
    return out


def _is_rate_limit(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    s = str(exc).lower()
    return "429" in s or "rate limit" in s or "rate_limit" in s


def _retry_after(exc: Exception) -> Optional[float]:
    """Seconds to wait before retrying a 429, from the Retry-After header or the message text."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            ra = (resp.headers or {}).get("retry-after")
            if ra:
                return float(ra)
        except Exception:  # noqa: BLE001
            pass
    text = str(exc)
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", text, re.I)
    if m:
        return (int(m.group(1)) * 60 if m.group(1) else 0) + float(m.group(2))
    return None


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
        self.fallback_model = os.environ.get("JARVIS_GROQ_FALLBACK", "openai/gpt-oss-20b")
        self._on_fallback = False
        self._on_local = False  # switched to local Ollama after a cloud rate-limit
        self.client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0, timeout=45)
        self.schemas, self.dispatch = build_registry(config, self.job_runner, confirm_fn)
        for s in self.schemas:  # trim descriptions hard — tool names are self-explanatory, and every
            d = s["function"].get("description", "")   # token here is sent on EVERY request (rate limits)
            if len(d) > 40:
                s["function"]["description"] = d[:40]

        self.messages: list[dict] = [{"role": "system", "content": _compact_system(config)}]
        self._restore_history()

    # --- cross-session conversation memory -------------------------------------------------
    @staticmethod
    def _history_path():
        import os
        from pathlib import Path
        return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "chat-history.json"

    def _restore_history(self, keep: int = 16) -> None:
        """Reload the last few plain user/assistant turns so Jarvis remembers the last chat."""
        import json
        try:
            turns = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        clean = [t for t in turns if isinstance(t, dict)
                 and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str) and t["content"].strip()]
        if clean:
            self.messages[1:1] = clean[-keep:]

    def _save_history(self, keep: int = 16) -> None:
        import json
        turns = [{"role": m["role"], "content": m["content"][:4000]}
                 for m in self.messages[1:]
                 if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()
                 and "tool_calls" not in m]
        try:
            p = self._history_path()
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            p.write_text(json.dumps(turns[-keep:], ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

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

    def _classify_linkedin_intent(self, user_text: str) -> str | None:
        """Recover semantic LinkedIn navigation when a model declines to call a tool."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the user's meaning. Reply with exactly one label: "
                        "linkedin_open_console when they want the interface that manages, schedules, "
                        "or reviews professional social posts; linkedin_stats when they want performance "
                        "or publishing progress; linkedin_open_profile when they want their public "
                        "professional profile page; none for everything else, including drafting, "
                        "approval, networking, and general career questions."
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            temperature=0,
            max_tokens=16,
        )
        content = (response.choices[0].message.content or "").strip().lower()
        for name in ("linkedin_open_console", "linkedin_stats", "linkedin_open_profile"):
            if re.fullmatch(rf"(?:`)?{name}(?:`)?[.!]?", content):
                return name
        return None

    def _try_local_fallback(self) -> bool:
        """Repoint at a local Ollama model so chat survives an exhausted cloud rate limit."""
        if self._on_local:
            return False
        import httpx
        from openai import OpenAI

        base = getattr(self.config, "ollama_base", "http://localhost:11434/v1")
        model = getattr(self.config, "ollama_model", "qwen2.5:3b")
        try:
            httpx.get(base.rsplit("/v1", 1)[0] + "/api/tags", timeout=2).raise_for_status()
        except Exception:  # noqa: BLE001 - no local Ollama, nothing to fall back to
            return False
        self.client = OpenAI(base_url=base, api_key="ollama", max_retries=0, timeout=120)
        self.model, self._on_local = model, True
        return True

    def _trim(self) -> None:
        # Keep the system message + a suffix that starts on a clean 'user' turn (never split a
        # tool_calls/tool pair, which the API rejects).
        if len(self.messages) <= 14:
            return
        keep_from = len(self.messages) - 10
        while keep_from < len(self.messages) and self.messages[keep_from].get("role") != "user":
            keep_from += 1
        if keep_from < len(self.messages):
            self.messages = [self.messages[0]] + self.messages[keep_from:]

    async def send(self, user_text: str) -> str:
        from ..commands import handle
        direct = await handle(user_text, self.config, getattr(self, "command_session", "local"))
        if direct is not None:
            return direct
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
        linkedin_executed = False
        rl_waits = 0  # how many times we've waited out a rate-limit this turn
        for _ in range(12):  # bounded tool rounds
            try:
                resp = await asyncio.to_thread(self._complete)
            except Exception as exc:  # noqa: BLE001
                if _is_rate_limit(exc) and not self._on_local and self.config.brain in ("groq", "gemini"):
                    # 1) Groq only: switch to the high-limit fallback model (once)
                    if self.config.brain == "groq" and self.model != self.fallback_model:
                        self.model = self.fallback_model
                        self._on_fallback = True
                        continue
                    # 2) short per-minute cap → wait it out and retry
                    wait = _retry_after(exc)
                    if wait is not None and wait <= 20 and rl_waits < 2:
                        rl_waits += 1
                        await asyncio.sleep(wait + 0.5)
                        continue
                    # 3) genuinely exhausted → drop to the local Ollama model so chat continues
                    if self._try_local_fallback():
                        self.on_tool("brain", f"rate-limited — switched to local {self.model}")
                        continue
                    # 4) no local model available → say so instead of erroring out
                    return (f"I've hit the {self.config.brain} rate limit, sir, and no local model is "
                            "running. Start `ollama serve` or try again in a minute.")
                salvaged = _parse_calls(_failed_gen(exc) or "")  # rescue a malformed tool call
                if not salvaged:
                    return f"[groq error] {exc}"
                await self._execute([(f"call_{i}", n, a) for i, (n, a) in enumerate(salvaged)])
                continue

            msg = resp.choices[0].message
            calls = msg.tool_calls or []
            if not calls:
                reply = (msg.content or "").strip()
                salvaged = _parse_calls(reply)
                if salvaged:
                    await self._execute(
                        [(f"text_call_{i}", name, args) for i, (name, args) in enumerate(salvaged)]
                    )
                    linkedin_executed = any(name.startswith("linkedin_") for name, _ in salvaged)
                    continue
                if not linkedin_executed:
                    try:
                        intent = await asyncio.to_thread(self._classify_linkedin_intent, user_text)
                    except Exception:  # noqa: BLE001 - normal response remains available
                        intent = None
                    if intent:
                        args = {"view": "dashboard"} if intent == "linkedin_open_console" else {}
                        await self._execute([("semantic_linkedin", intent, args)])
                        linkedin_executed = True
                        continue
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
            linkedin_executed = linkedin_executed or any(
                name.startswith("linkedin_") for _, name, _ in triples
            )

        vaultmod.git_autocommit(self.config.vault_path, f"jarvis: memory update {now:%Y-%m-%d %H:%M}")
        self._trim()
        self._save_history()
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
