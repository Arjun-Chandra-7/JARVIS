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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..config import Config
from ..providers import DEFAULT_GROQ_FALLBACK
from ..jobs.runner import JobRunner
from ..memory import vault as vaultmod
from . import action_claims, spoken, tool_contract, tool_router
from ..brains import choose
from .groq_tools import build_registry

ToolCallback = Callable[[str, str], None]
ConfirmCallback = Callable[[str], Awaitable[bool]]

_GROQ_ADDENDUM = """

# HOW TO OPERATE (read carefully)
You are running on Groq with function tools. Follow these rules exactly:

1. TO TALK TO THE USER, JUST WRITE TEXT. Your normal reply is spoken aloud. NEVER use `type_text`,
   `press_keys`, or `run_bash` to "say" something — those control the computer, not the conversation.
   Typing a greeting with `type_text` is WRONG.
2. NEVER STATE A FACT ABOUT THIS MACHINE OR THIS PERSON WITHOUT LOOKING IT UP. Battery, CPU,
   memory, disk, temperature, time, weather, calendar, email, messages, files, contacts, and
   anything about the user personally are things you MEASURE with a tool, not things you remember.
   You do not know the battery level until `system_stats` tells you. Answering "your battery is at
   85%" from memory is a fabrication even if it sounds plausible — call the tool, then report what
   it returned. If no tool can tell you, say you don't know.
   Chit-chat, greetings, opinions, and general knowledge need no tools — just answer those.
3. PREFER THE SPECIFIC TOOL over run_bash: system info → `system_stats`; volume → `set_volume`;
   play/pause → `media_control`; open a site → `open_url`; memory → `recall`; timers → `set_timer`.
   Use `run_bash` only for genuine shell tasks with no dedicated tool.
4. `type_text`/`press_keys`/`mouse_*` are ONLY for when the user explicitly asks you to control the
   screen / type into an app for them. For a multi-step desktop or game request, inspect the current
   screen, choose one grounded action, perform it, inspect again, and adapt. `hold_keys`,
   `hold_mouse`, `mouse_drag`, and `find_and_drag` can sustain input; their success means the input ran, not that the
   game or app goal succeeded. Never claim a basket was scored without seeing the result. Prefer
   `browser_read`/`browser_click` for web pages; use `desktop_read` and `find_and_click` for
   native controls, then screen vision when accessibility cannot read the target.
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


# How a per-turn specialist instruction is marked, so the next turn can find and remove it.
TURN_NOTE = "For this turn:"


def _is_a_turn_note(message: dict) -> bool:
    return (message.get("role") == "system"
            and str(message.get("content", "")).startswith(TURN_NOTE))


# Beyond this, the last conversation is not the conversation being had now. Long enough to go to
# lunch and carry on; short enough that overnight is a clean start.
HISTORY_STALE_AFTER_S = 4 * 3600


# --------------------------------------------------------------- a streamed reply, reassembled
# The agent loop reads `resp.choices[0].message`, branches on `.tool_calls`, and passes the
# message back into `self.messages`. A streamed answer arrives as hundreds of fragments instead,
# so it is put back into that exact shape here — which is what keeps the loop from needing a
# streaming version of itself.


class _StreamedFunction:
    __slots__ = ("name", "arguments")

    def __init__(self, name: str, arguments: str):
        self.name = name
        # The API sends arguments as a JSON string and so does this, unparsed, because that is
        # what the loop already expects to be handed.
        self.arguments = arguments


class _StreamedCall:
    __slots__ = ("id", "type", "function")

    def __init__(self, call_id: str, name: str, arguments: str):
        self.id = call_id
        self.type = "function"
        self.function = _StreamedFunction(name, arguments)


class _StreamedMessage:
    __slots__ = ("role", "content", "tool_calls")

    def __init__(self, content: str, calls):
        self.role = "assistant"
        # None rather than "" when there is nothing: an empty string beside a tool call is a
        # different thing to the API than no content at all.
        self.content = content or None
        self.tool_calls = calls or None


class _StreamedChoice:
    __slots__ = ("index", "message", "finish_reason")

    def __init__(self, message, finish_reason):
        self.index = 0
        self.message = message
        self.finish_reason = finish_reason


class _StreamedResponse:
    """What `_stream` hands back: indistinguishable from a normal completion to its reader."""

    __slots__ = ("choices",)

    def __init__(self, content: str, calls: dict, finish_reason):
        ordered = [calls[i] for i in sorted(calls)]
        built = [_StreamedCall(c["id"] or f"call_{n}", c["name"], c["arguments"])
                 for n, c in enumerate(ordered) if c["name"]]
        self.choices = [_StreamedChoice(_StreamedMessage(content, built), finish_reason)]


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
        self.fallback_model = os.environ.get("JARVIS_GROQ_FALLBACK", DEFAULT_GROQ_FALLBACK)
        self._on_fallback = False
        self._on_local = False  # switched to local Ollama after a cloud rate-limit
        # A local Ollama endpoint needs no key, and the SDK refuses to build a client with an empty
        # one — so give it a placeholder rather than crashing before Jarvis can fall back to local.
        self.client = OpenAI(base_url=base_url, api_key=api_key or "none",
                             max_retries=0, timeout=45)
        self.schemas, self.dispatch = build_registry(config, self.job_runner, confirm_fn)
        if not config.tool_routing:
            # Without routing every schema is sent on every request, so descriptions are clipped
            # to protect the rate limit. With routing only ~10 tools go out and they can be whole.
            for s in self.schemas:
                d = s["function"].get("description", "")
                if len(d) > 40:
                    s["function"]["description"] = d[:40]

        self._route_query = ""          # the utterance the tool shortlist is chosen for
        self._tools_ran_this_turn = False          # did any tool actually run this turn?
        self._any_tool_succeeded = False           # ...and did any of them actually work?
        self._failed_calls: dict[str, int] = {}     # calls that already failed this turn
        self._failed_calls_advice: dict[str, str] = {}
        self._last_outcome = None       # outcome of the most recent tool call
        if config.tool_routing:
            try:
                tool_router.build_index(self.schemas)
            except Exception:  # noqa: BLE001 - falls back to lexical routing
                pass

        self.messages: list[dict] = [{"role": "system", "content": _compact_system(config)}]
        self._turn_times: dict[int, float] = {}   # when each user turn was actually spoken
        self._restore_history()

    # --- cross-session conversation memory -------------------------------------------------
    @staticmethod
    def _history_path():
        import os
        from pathlib import Path
        return Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser() / "chat-history.json"

    def _restore_history(self, keep: int = 16) -> None:
        """Reload the last few plain user/assistant turns so Jarvis remembers the last chat.

        Only a recent one. A conversation that stopped hours ago is not the conversation being
        had now, and restoring it makes the model carry on with whatever it was last thinking
        about. Said "Good morning" the next day, it answered: "Initiate Deep Research Sequence
        for tech event team name ideas?" — the topic from the night before, offered as though the
        greeting had been a request to continue it.

        Restored turns are also fenced. Without the fence the model treats a previous answer as
        current and simply repeats it: asked the battery level twice across sessions it replays
        the earlier number verbatim instead of calling `system_stats` again, so one wrong reading
        becomes permanent.
        """
        import json
        import time

        try:
            raw = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return

        # Written as a bare list before this change; both shapes are read so an upgrade does not
        # throw away the history somebody is in the middle of.
        if isinstance(raw, dict):
            turns, saved_at = raw.get("turns", []), float(raw.get("saved_at") or 0)
        else:
            turns, saved_at = raw, 0.0

        if saved_at and (time.time() - saved_at) > HISTORY_STALE_AFTER_S:
            return                       # a different conversation; start clean

        clean = [t for t in turns if isinstance(t, dict)
                 and t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str) and t["content"].strip()]
        if not clean:
            return
        fence = {
            "role": "system",
            "content": (
                "The following turns are from an EARLIER session, kept only so you remember what "
                "was discussed. Every measurement, status, time, and number in them is STALE. If "
                "the user asks about any of it again, call the tool and report the fresh value — "
                "never repeat an old one. That conversation is over: do not continue its topic, "
                "do not re-offer what was suggested in it, and do not assume a new message is "
                "about it. Answer what is actually being said now."
            ),
        }
        self.messages[1:1] = [fence] + clean[-keep:]

    def _save_history(self, keep: int = 16) -> None:
        import json
        import time

        turns = [{"role": m["role"], "content": m["content"][:4000]}
                 for m in self.messages[1:]
                 if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str) and m["content"].strip()
                 and "tool_calls" not in m]
        try:
            p = self._history_path()
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Stamped, so the next session can tell whether this is the same conversation or
            # yesterday's. A bare list carries no such thing, which is how a greeting the next
            # morning got answered with the previous night's topic.
            p.write_text(json.dumps({"saved_at": time.time(), "turns": turns[-keep:]},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    async def __aenter__(self) -> "GroqAgent":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def _active_schemas(self):
        """The tools shown to the model this turn.

        Handing over all ~84 schemas measurably hurt: on qwen2.5:3b the same 17-command set went
        11/17 with everything visible and 15/17 with the ten most relevant, and got faster too
        (1.48 s -> 0.91 s median) because there was far less schema to re-read. Set
        JARVIS_TOOL_ROUTING=0 to send everything again.
        """
        from . import gate

        said = self._route_query or ""
        if not self.config.tool_routing or not self._route_query:
            return gate.allowed(self.schemas, said)
        specialist = getattr(self, "_specialist", None)
        try:
            chosen = tool_router.select(
                self.schemas, self._route_query, keep=self.config.tool_routing_keep
            )
            if specialist is None or not specialist.tools:
                return chosen
            # The specialist's own tools go in front, and are never crowded out by the router's
            # ranking. A coder should always be able to read a file even when the wording of the
            # question did not say so.
            wanted = [s for s in self.schemas
                      if s["function"]["name"] in specialist.tools]
            names = {s["function"]["name"] for s in wanted}
            # The gate is applied last, over everything the router and the specialist chose
            # between them. A specialist's own tools are not an exemption: the artist may always
            # reach for generate_image when a picture was asked for, and never when it was not.
            return gate.allowed(wanted + [s for s in chosen
                                          if s["function"]["name"] not in names], said)
        except Exception:  # noqa: BLE001 - routing must never block a turn
            return gate.allowed(self.schemas, said)

    # ----------------------------------------------------------------- streaming the answer
    # The reply is spoken only once it has been written in full, so the silence before Jarvis
    # says anything contains the whole generation. Streaming lets the first sentence be spoken
    # while the rest is still being written.
    #
    # The agent loop is not changed to accommodate it. It reads `resp.choices[0].message` and
    # branches on `.tool_calls`, so a streamed answer is reassembled into exactly that shape and
    # the loop cannot tell the difference. Anything else would mean two versions of a loop that
    # already handles tool rounds, rate-limit fallbacks and claim checking.
    #
    # Deltas are only forwarded while the answer is still plainly text. The moment a tool call
    # appears the callback is dropped: a turn that calls a tool has not produced an answer yet,
    # and speaking its preamble would be speaking something the user should never hear.

    def _stream(self, on_delta):
        """One completion, streamed, returned in the same shape as `_complete`."""
        specialist = getattr(self, "_specialist", None)
        stream = self.client.chat.completions.create(
            model=(specialist.model if specialist and specialist.model else self.model),
            messages=self.messages,
            tools=self._active_schemas(),
            tool_choice="auto",
            temperature=(specialist.temperature if specialist is not None
                         else self.config.temperature),
            max_tokens=512,
            stream=True,
        )

        content: list[str] = []
        calls: dict[int, dict] = {}
        speaking = on_delta is not None
        finish = None
        # Small models sometimes emit a tool call as plain text instead of calling it. The loop
        # rescues those (`_parse_calls`), but by then it would already have been read out, and
        # a JSON payload spoken aloud is not something an apology covers. So nothing is spoken
        # until the reply has proved it is prose, which the first non-space character settles.
        proven_prose = False

        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish = choice.finish_reason or finish
            delta = choice.delta
            if delta is None:
                continue

            for call in (getattr(delta, "tool_calls", None) or []):
                # A tool call means this is not the answer. Stop speaking immediately — before
                # the fragment is even recorded — so nothing already said can be added to.
                speaking = False
                slot = calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                if call.id:
                    slot["id"] = call.id
                fn = getattr(call, "function", None)
                if fn is not None:
                    if fn.name:
                        slot["name"] = fn.name
                    if fn.arguments:
                        slot["arguments"] += fn.arguments

            piece = getattr(delta, "content", None)
            if piece:
                content.append(piece)
                if speaking and not proven_prose:
                    opening = "".join(content).lstrip()
                    if not opening:
                        continue          # nothing but whitespace so far; wait for a character
                    if opening[0] in "{[":
                        speaking = False  # a tool call written as text
                    else:
                        proven_prose = True
                        # Everything held back while waiting for that first character is due
                        # now, or the answer would start from its second word.
                        on_delta(opening)
                        continue
                if speaking:
                    on_delta(piece)

        return _StreamedResponse("".join(content), calls, finish)

    def _complete_maybe_streaming(self):
        """The completion the loop asks for: streamed when anything is listening for it.

        Falling back rather than failing is deliberate. Streaming is a way of answering sooner,
        never a requirement for answering at all, so a provider that will not stream, or breaks
        halfway through one, costs the speed and not the reply.
        """
        on_delta = getattr(self, "on_reply_delta", None)
        if on_delta is None:
            return self._complete()
        try:
            return self._stream(on_delta)
        except Exception:  # noqa: BLE001
            return self._complete()

    def _complete(self):
        """One completion, under whichever specialist this turn belongs to.

        The temperature is chosen once, here. It was briefly passed twice — the specialist's
        through a **kwargs and the configured one explicitly — which is not a wrong answer but a
        TypeError, and every turn that reached this brain answered with it:

            [groq error] Completions.create() got multiple values for keyword argument
            'temperature'
        """
        specialist = getattr(self, "_specialist", None)
        return self.client.chat.completions.create(
            model=(specialist.model if specialist and specialist.model else self.model),
            messages=self.messages,
            tools=self._active_schemas(),
            tool_choice="auto",
            temperature=(specialist.temperature if specialist is not None
                         else self.config.temperature),
            max_tokens=512,
        )

    # Words that make a request plausibly about LinkedIn. Without this gate the classifier ran on
    # every single turn and pre-empted normal tool selection: "select the profile of Arjun" (a
    # Netflix profile) was answered by opening the user's LinkedIn profile, because a 3B model
    # sees "profile" and reaches for the one labelled option that contains it.
    _LINKEDIN_HINTS = (
        "linkedin", "linked in", "my network", "connection request", "invitation",
        "my post", "my posts", "publish", "scheduled post", "draft post", "impressions",
        "followers", "content copilot", "copilot",
    )

    def _looks_like_linkedin(self, user_text: str) -> bool:
        low = (user_text or "").lower()
        return any(hint in low for hint in self._LINKEDIN_HINTS)

    def _classify_linkedin_intent(self, user_text: str) -> str | None:
        """Recover semantic LinkedIn navigation when a model declines to call a tool.

        Only called for requests that already mention LinkedIn — see `_looks_like_linkedin`.
        """
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
                        "professional profile page; linkedin_top_ideas when they want fresh trending "
                        "public topics to write about; none for everything else, including drafting, "
                        "approval, networking, and general career questions."
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            temperature=0,
            max_tokens=16,
        )
        content = (response.choices[0].message.content or "").strip().lower()
        for name in (
            "linkedin_open_console",
            "linkedin_stats",
            "linkedin_open_profile",
            "linkedin_top_ideas",
        ):
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

    # A conversation left open for hours is not one conversation. Observed in the log: at 16:41
    # "How you doing?" was answered "I found several matches for 'Arnav Pandey'" — a reply to an
    # unresolved WhatsApp question from a different sitting, still sitting in the last ten
    # messages because trimming counted turns and never looked at the clock.
    STALE_TURN_S = 20 * 60

    def _drop_stale(self) -> None:
        now = time.time()
        first_live = None
        for i, message in enumerate(self.messages[1:], start=1):
            if message.get("role") != "user":
                continue
            stamped = re.match(r"\[time: .*?\]", str(message.get("content", "")))
            if not stamped:
                continue
            seen = self._turn_times.get(id(message))
            if seen is None:
                continue
            if now - seen <= self.STALE_TURN_S:
                first_live = i
                break
        if first_live is None:
            # Every remembered turn is old: start clean rather than answer from a past sitting.
            if len(self.messages) > 1 and self._turn_times:
                self.messages = [self.messages[0]]
                self._turn_times.clear()
        elif first_live > 1:
            self.messages = [self.messages[0]] + self.messages[first_live:]

    def _trim(self) -> None:
        self._drop_stale()
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
        session = getattr(self, "command_session", "local")
        direct = await handle(user_text, self.config, session)
        if direct is not None:
            return direct
        from ..approvals import MANAGER
        held_before = {a.id for a in MANAGER.pending(session)}
        self._route_query = user_text   # pick this turn's tool shortlist from what was asked
        from . import groq_tools as _tools
        _tools.CURRENT_REQUEST["text"] = user_text      # what the messaging tool checks against
        self._failed_calls.clear()
        self._failed_calls_advice.clear()
        self._tools_ran_this_turn = False
        self._any_tool_succeeded = False
        action_claims_checked = False
        # episodic journal
        clean = " ".join(l for l in user_text.splitlines() if not l.strip().startswith("["))[:140].strip()
        if clean:
            try:
                vaultmod.journal_append(self.config.vault_path, clean)
            except Exception:  # noqa: BLE001
                pass

        now = datetime.now().astimezone()
        # Gated: this classifier bypasses tool selection entirely, so it must only run when the
        # request is actually about LinkedIn.
        linkedin_intent = None
        if self._looks_like_linkedin(user_text):
            try:
                linkedin_intent = await asyncio.to_thread(self._classify_linkedin_intent, user_text)
            except Exception:  # noqa: BLE001 - normal tool routing remains available
                linkedin_intent = None
        if linkedin_intent:
            args = {"view": "dashboard"} if linkedin_intent == "linkedin_open_console" else {}
            self.on_tool(linkedin_intent, "semantic intent")
            result = await self.dispatch(linkedin_intent, args)
            vaultmod.git_autocommit(
                self.config.vault_path, f"jarvis: memory update {now:%Y-%m-%d %H:%M}"
            )
            return str(result)

        # A sentence that stops mid-phrase is the first half of a request. Completing it is the
        # model's imagination, not the user's intent, and "generate an image of a" became a
        # picture of whatever it felt like.
        from . import gate

        if gate.looks_unfinished(user_text):
            return gate.ask_for_the_rest(user_text)

        # Which specialist this turn belongs to. It changes the instruction, the temperature and
        # which tools are put in front of the model — all of which matter more to a small local
        # brain than they would to a large one.
        self._specialist, self._specialist_confidence = choose.pick(user_text)

        turn = {"role": "user", "content": f"[time: {now:%A %Y-%m-%d %H:%M %Z}] {user_text}"}
        # Last turn's note goes before this turn's is added. It says "for this turn", and it
        # meant it: left in the history they pile up, and three turns in the model is being told
        # it is a coder, a scribe and a companion at once, with the oldest instruction sitting
        # closest to the standing prompt. Trimming eventually removes them, but not for fourteen
        # messages, which is far too late to stop them contradicting each other.
        self.messages = [m for m in self.messages if not _is_a_turn_note(m)]
        if self._specialist is not None:
            # As a system note beside the turn rather than replacing the standing prompt: the
            # things that prompt establishes — who the user is, what this machine is — are true
            # whichever specialist is working.
            self.messages.append({"role": "system",
                                  "content": f"{TURN_NOTE} {self._specialist.instruction}"})
        self.messages.append(turn)
        self._turn_times[id(turn)] = time.time()

        reply = ""
        linkedin_executed = False
        rl_waits = 0  # how many times we've waited out a rate-limit this turn
        for _ in range(12):  # bounded tool rounds
            try:
                resp = await asyncio.to_thread(self._complete_maybe_streaming)
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
                if not linkedin_executed and self._looks_like_linkedin(user_text):
                    try:
                        intent = await asyncio.to_thread(self._classify_linkedin_intent, user_text)
                    except Exception:  # noqa: BLE001 - normal response remains available
                        intent = None
                    if intent:
                        args = {"view": "dashboard"} if intent == "linkedin_open_console" else {}
                        await self._execute([("semantic_linkedin", intent, args)])
                        linkedin_executed = True
                        continue
                # A reply that says it opened, launched or played something, when no tool ran this
                # turn, is not true. Push back once and make it actually act; if it still will
                # not, say plainly that nothing happened rather than delivering the claim.
                # Nothing worked this turn if no tool ran, or every tool that ran failed.
                # "click on friends" failed and was still reported as "you've clicked on
                # 'friends', which navigated to the Netflix profile".
                # Only meaningful when something was actually asked for. A question that runs no
                # tool has not failed to act; it has been answered.
                nothing_worked = (not self._any_tool_succeeded
                                  and action_claims.asks_for_an_action(self._route_query))
                # The gap that rule leaves open, found live: "See you, daddy." was answered
                # with "Message sent to Daddy." Nothing was asked for, so nothing_worked is
                # false, so none of the checks below ever looked at it — and a turn that asked
                # for nothing is exactly the turn where a claim to have acted is a fabrication
                # rather than a shortfall. There is nothing to retry, either: the tools were
                # withheld precisely because nothing was asked for.
                if (not self._any_tool_succeeded
                        and not action_claims.asks_for_an_action(self._route_query)
                        and action_claims.claims_an_action(reply)):
                    self.on_tool("brain", "claimed an action nobody asked for — replaced")
                    return ("I haven't done anything, sir — that sounded like conversation "
                            "rather than a request. Say the word if you did want it done.")

                # A promise is the same failure as a false claim, and harder to notice because
                # it sounds like progress. Both get one push back to actually act.
                #
                # Deliberately not gated on something having been asked for, which is the gap
                # this fell through. From the history: "What are their opinions on Elon Musk?"
                # was answered with "I'll look up some recent articles about Elon Musk's
                # opinions. It might take a moment." No tool ran and no moment was taken,
                # because the turn had already ended. A question left on a promise is a dead end
                # in the same way a command is, and a worse one to be on the receiving end of —
                # nothing is coming, and nothing said so.
                if (not self._any_tool_succeeded
                        and not action_claims_checked
                        and action_claims.promises_without_acting(reply)):
                    action_claims_checked = True
                    self.on_tool("brain", "promised instead of acting — retrying")
                    self.messages.append({"role": "assistant", "content": reply})
                    self.messages.append({
                        "role": "user",
                        "content": (action_claims.nudge_to_act() if nothing_worked
                                    else action_claims.nudge_to_answer()),
                    })
                    continue

                if (nothing_worked
                        and not action_claims_checked
                        and action_claims.claims_an_action(reply)):
                    action_claims_checked = True
                    self.on_tool("brain", "claimed an action without doing it — retrying")
                    self.messages.append({"role": "assistant", "content": reply})
                    self.messages.append({
                        "role": "user", "content": action_claims.correction_for(reply),
                    })
                    continue
                if (nothing_worked
                        and action_claims_checked
                        and action_claims.claims_an_action(reply)):
                    stated = (list(self._failed_calls_advice.values())[-1]
                              if self._failed_calls_advice else "")
                    from ..selfimprove import journal
                    journal.record("claimed_without_acting", self._route_query, reply[:300], "brain")
                    reply = action_claims.honest_fallback(stated)

                # A tool failed for a specific, stated reason; do not relay it as a vague fault.
                # "No installed app matches Networks" was reported as "there's a temporary
                # glitch", which hides the cause and invites the user to keep retrying.
                # A reply about brightness when nobody mentioned brightness, or one that names an
                # internal tool, is the small model casting about after garbled speech. Saying the
                # words did not come through is more use than answering a question never asked.
                if action_claims.is_a_non_sequitur(self._route_query, reply):
                    self.on_tool("brain", "answered about something that was never asked")
                    reply = action_claims.misheard_fallback()
                    self.messages.append({"role": "assistant", "content": reply})
                    break

                if self._failed_calls_advice and action_claims.invents_an_excuse(reply):
                    last = list(self._failed_calls_advice.values())[-1]
                    grounded = action_claims.real_reason(reply, last)
                    if grounded != reply:
                        self.on_tool("brain", "replaced a vague excuse with the real reason")
                        reply = grounded

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
            # A tool held something for a yes. The turn ends on the approval prompt itself: given
            # "Ready to send… say yes" back, the model told the owner "I've sent the message".
            held = [a for a in MANAGER.pending(session) if a.id not in held_before]
            if held:
                reply = " ".join(a.prompt() for a in held)
                self.messages.append({"role": "assistant", "content": reply})
                break

        vaultmod.git_autocommit(self.config.vault_path, f"jarvis: memory update {now:%Y-%m-%d %H:%M}")
        self._trim()
        self._save_history()
        # The offer of further assistance goes before the reply is spoken, written to the
        # transcript or stored in history — one place rather than three.
        return spoken.trim_trailer(reply) or "(no reply)"

    async def _execute(self, triples) -> None:
        """Append the assistant tool_calls turn + each tool result. triples = [(id, name, args)].

        Every call is checked against its registered schema first. Mechanical mistakes (a string
        where an integer belongs, `duration_seconds` for `seconds`) are repaired; anything
        ambiguous comes back to the model as a precise correction instead of being guessed at or
        run wrong. Nothing unvalidated reaches a tool.
        """
        self.messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": tid, "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}} for tid, name, args in triples],
        })
        known = [s.get("function", {}).get("name", "") for s in self.schemas]
        for tid, name, args in triples:
            # A small model that gets a tool wrong often re-issues the identical call rather than
            # taking the redirect it was given, then gives up. Refuse the repeat and point at the
            # alternative instead of spending the turn on it.
            signature = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
            if self._failed_calls.get(signature, 0) >= 1:
                advice = self._failed_calls_advice.get(signature, "")
                self.on_tool(name, "blocked — identical call already failed this turn")
                self.messages.append({
                    "role": "tool", "tool_call_id": tid,
                    "content": (f"[failure] You already called {name} with exactly these arguments "
                                f"this turn and it failed. Do not repeat it. "
                                + (advice or "Use a different tool.")),
                })
                continue

            resolved, note = tool_contract.resolve_name(name, known)
            if resolved is None:
                self.on_tool(name, f"rejected — {note}")
                self.messages.append({
                    "role": "tool", "tool_call_id": tid,
                    "content": f"[failure] {note}. Available tools are the ones in your tool list.",
                })
                self._last_outcome = tool_contract.Outcome.FAILURE
                self._failed_calls[signature] = self._failed_calls.get(signature, 0) + 1
                continue

            schema = next(
                (s for s in self.schemas if s.get("function", {}).get("name") == resolved), None
            )
            check = tool_contract.validate(schema, args) if schema else None
            if check is not None and not check.ok:
                self.on_tool(resolved, f"invalid arguments — {'; '.join(check.problems)}")
                self.messages.append({
                    "role": "tool", "tool_call_id": tid,
                    "content": check.message(resolved, schema),
                })
                self._last_outcome = tool_contract.Outcome.FAILURE
                self._failed_calls[signature] = self._failed_calls.get(signature, 0) + 1
                self._failed_calls_advice[signature] = check.message(resolved, schema)[:300]
                continue
            if check is not None:
                args = check.args
                if check.repaired:
                    self.on_tool(resolved, f"repaired arguments: {', '.join(check.repaired)}")

            self.on_tool(resolved, ", ".join(f"{k}={v}" for k, v in list(args.items())[:2]))
            try:
                result = await self.dispatch(resolved, args)
                outcome = tool_contract.Outcome.SUCCESS
            except asyncio.CancelledError:
                self.messages.append({
                    "role": "tool", "tool_call_id": tid,
                    "content": "[cancelled] the user cancelled this before it finished.",
                })
                self._last_outcome = tool_contract.Outcome.CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001 - a tool must not kill the turn
                result = f"{type(exc).__name__}: {exc}"
                outcome = tool_contract.Outcome.FAILURE
            self._last_outcome = outcome
            self._tools_ran_this_turn = True
            body = str(result)[:6000]
            # A tool that reports its own failure in the text (a redirect, "not found", a refusal)
            # counts as failed even though dispatch returned normally — otherwise the repeat-guard
            # never sees it.
            said_no = body.lstrip().upper().startswith(("WRONG TOOL", "[FAILURE]", "NO INSTALLED"))
            # Judged after said_no, not before: set_brightness returning "I can read the brightness
            # but not change it" used to mark the turn a success, which let the reply claim the
            # brightness had been set while the backlight never moved.
            if outcome is tool_contract.Outcome.SUCCESS and not said_no:
                self._any_tool_succeeded = True
            if outcome is not tool_contract.Outcome.SUCCESS or said_no:
                self._failed_calls[signature] = self._failed_calls.get(signature, 0) + 1
                self._failed_calls_advice[signature] = body[:300]
                from ..selfimprove import journal
                journal.record("tool_failed", self._route_query, body[:300], resolved)
            self.messages.append({
                "role": "tool", "tool_call_id": tid,
                "content": body if outcome is tool_contract.Outcome.SUCCESS else f"[failure] {body}",
            })
