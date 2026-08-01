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

_VOICE_SUFFIX = "\n\n(Reply in one or two short spoken sentences — plain speech, no markdown or lists.)"


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

    async def send(self, user_text: str) -> str:
        msg = user_text + (_VOICE_SUFFIX if self.mode == "voice" else "")
        # generous enough for a deep-research turn, but bounded so a hung call can't wedge the
        # voice loop (which can't listen while it's waiting on a reply).
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
                r = await client.post(f"{self.base}/chat", json={"message": msg})
                return (r.json() or {}).get("reply", "") or "(no reply)"
        except httpx.TimeoutException:
            return "That one took too long, sir — let me know if you'd like me to try again."
        except Exception as exc:  # noqa: BLE001
            return f"[voice can't reach the brain — is --web running? {exc}]"
