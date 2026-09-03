from __future__ import annotations
import asyncio
import httpx
import os
from ..config import Config

class WhatsAppBridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.port = os.environ.get('WA_PORT', '8765')
        self.api = f"http://127.0.0.1:{self.port}"
        self.seen_ts = set()

    async def _send(self, client: httpx.AsyncClient, to: str, text: str) -> None:
        text = text or "(no reply)"
        try:
            await client.post(f"{self.api}/send", json={"to": to, "text": text}, timeout=10)
        except Exception:
            pass

    async def run(self, agent) -> None:
        print("Listening for commands via WhatsApp 'Message Yourself'...")
        async with httpx.AsyncClient() as client:
            try:
                inbox = (await client.get(f"{self.api}/inbox", timeout=5)).json()
                for m in inbox:
                    self.seen_ts.add(m.get("ts", 0))
            except Exception:
                pass

            while True:
                try:
                    inbox = (await client.get(f"{self.api}/inbox", timeout=5)).json()
                except Exception:
                    await asyncio.sleep(3)
                    continue

                for msg in inbox:
                    ts = msg.get("ts", 0)
                    if ts in self.seen_ts:
                        continue
                    self.seen_ts.add(ts)
                    
                    sender = msg.get("from", "")
                    text = msg.get("text", "")
                    if not text or not sender:
                        continue

                    # Security: Only process commands sent from the user's OWN phone to themselves
                    if not msg.get("fromMe"):
                        continue

                    try:
                        reply = await agent.send(text)
                    except Exception as exc:
                        reply = f"[error] {exc}"
                    
                    await self._send(client, sender, reply)

                await asyncio.sleep(2)
