"""Remote access via a Telegram bot — talk to Jarvis (full brain + memory + tools) from anywhere.

Long-polls the Telegram Bot API over plain HTTP (no extra deps). Create a bot with @BotFather,
put the token in TELEGRAM_BOT_TOKEN, and optionally restrict access with TELEGRAM_ALLOWED_CHAT_IDS.

Safety: remote turns run with no interactive confirm available, so the agent's gate denies
destructive shell and edits to your own notes (git still protects everything else).
"""

from __future__ import annotations

import asyncio

import httpx

from ..config import Config

_MAX = 3500  # keep under Telegram's 4096-char message limit


class TelegramBridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.api = f"https://api.telegram.org/bot{config.telegram_bot_token}"
        self.allowed = config.telegram_allowed_chat_ids

    async def _send(self, client: httpx.AsyncClient, chat_id: str, text: str) -> None:
        text = text or "(no reply)"
        for i in range(0, len(text), _MAX):
            try:
                await client.post(
                    f"{self.api}/sendMessage", json={"chat_id": chat_id, "text": text[i : i + _MAX]}
                )
            except Exception:  # noqa: BLE001
                pass

    async def run(self, agent) -> None:
        """Poll for messages and answer them with the given (connected) JarvisAgent."""
        offset = None
        async with httpx.AsyncClient(timeout=70.0) as client:
            try:
                await client.get(f"{self.api}/deleteWebhook")  # enable long polling
            except Exception:  # noqa: BLE001
                pass
            while True:
                params = {"timeout": 60}
                if offset is not None:
                    params["offset"] = offset
                try:
                    resp = await client.get(f"{self.api}/getUpdates", params=params)
                    updates = resp.json().get("result", [])
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(3)
                    continue

                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message") or update.get("edited_message") or {}
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "")
                    if not text or not chat_id:
                        continue
                    if self.allowed and chat_id not in self.allowed:
                        await self._send(client, chat_id, "Sorry — you're not authorised.")
                        continue
                    try:
                        await client.post(
                            f"{self.api}/sendChatAction",
                            json={"chat_id": chat_id, "action": "typing"},
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        reply = await agent.send(text)
                    except Exception as exc:  # noqa: BLE001
                        reply = f"[error] {exc}"
                    await self._send(client, chat_id, reply)
