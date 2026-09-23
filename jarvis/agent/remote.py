"""A thin agent that forwards to the running --web brain instead of holding its own.

The ChatGPT brain drives a real browser session, and we only ever want ONE of those. So the voice
loop doesn't spin up its own brain/browser — it uses this RemoteAgent, which POSTs each utterance to
the --web server's /chat (the single shared ChatGPTAgent). Voice and the HUD then share one browser,
one conversation, and one memory. Same async interface as the real agents.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# Brevity applies to the SPOKEN REPLY, never to whether to act. Worded only as "reply in one or
# two short sentences", a small model reads it as "just answer" and narrates the action instead of
# performing it — observed live: "open opera gx" -> "Opening Opera GX..." with no tool call at all,
# and once that lands in history it repeats for everything.
_ACT_SUFFIX = (
    "\n\n(Spoken request. DO the action with a tool first — actually call it; saying you are "
    "opening something is not opening it. Then reply in one or two short spoken sentences, plain "
    "speech, no markdown or lists.)"
)

# The note above was attached to every spoken turn, including the ones that asked for nothing.
# Told to act when there is no action, a model describes one. Straight from the history:
#
#     you> What are their opinions on Elon Musk?
#     jarvis> I'll look up some recent articles about Elon Musk's opinions. It might take a
#             moment. How can I assist you further?
#
# No tool was called and no moment was taken, because the turn had already ended. The same shape
# turned up for "what colour is the sky" and "what are some good names for a tech company" — all
# of them questions a model can simply answer.
#
# So a question gets told it is a question. Searching is still available when the answer really
# does depend on something current; what is forbidden is announcing a search instead of doing one.
_ANSWER_SUFFIX = (
    "\n\n(Spoken question, not a request to do anything. Answer it yourself from what you "
    "already know. Do not reach for a tool unless the answer genuinely depends on something "
    "current that you cannot know — and if it does, look it up in this turn and give the answer; "
    "never say you are about to look something up, because the turn ends when you reply. Give "
    "your own view when asked for one. Reply in one or two short spoken sentences, plain speech, "
    "no markdown or lists, and do not end by offering further assistance.)"
)


def voice_note(user_text: str) -> str:
    """Which instruction this turn should carry: how to act, or how to answer."""
    from .gate import wants_something_done

    return _ACT_SUFFIX if wants_something_done(user_text) else _ANSWER_SUFFIX


def web_base() -> str:
    return f"http://127.0.0.1:{os.environ.get('JARVIS_WEB_PORT', '8770')}"


def web_reachable(timeout: float = 1.0) -> bool:
    try:
        return httpx.get(f"{web_base()}/health", timeout=timeout).status_code == 200
    except Exception:  # noqa: BLE001
        return False


class RemoteAgent:
    def __init__(self, mode: str = "voice") -> None:
        self.mode = mode
        self.base = web_base()

    async def __aenter__(self) -> "RemoteAgent":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def send(self, user_text: str, event_id: str = "") -> str:
        msg = user_text + (voice_note(user_text) if self.mode == "voice" else "")
        # One id per utterance, so the server acts on it once however many times it arrives.
        event_id = event_id or getattr(self, "event_id", "") or ""
        self.event_id = ""
        # generous enough for a deep-research turn, but bounded so a hung call can't wedge the
        # voice loop (which can't listen while it's waiting on a reply).
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
                r = await client.post(f"{self.base}/chat", json={"message": msg, "session_id": self.mode,
                                                                 "event_id": event_id})
                return (r.json() or {}).get("reply", "") or "(no reply)"
        except httpx.TimeoutException:
            return "That one took too long, sir — let me know if you'd like me to try again."
        except Exception as exc:  # noqa: BLE001
            return f"[voice can't reach the brain — is --web running? {exc}]"
