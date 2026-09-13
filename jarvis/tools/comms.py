"""Messaging people: WhatsApp, Instagram, contacts and calls.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool


async def compose_message(config, name: str, about: str) -> str:
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

@tool("whatsapp_send",
      "Send a WhatsApp message. 'to' = a contact NAME, a phone number, or a JID. Put the user's "
      "message VERBATIM in 'message' — do not paraphrase or add words.",
      {"to": {"type": "string"}, "message": {"type": "string"}}, ["to", "message"],
          side_effects=True)
async def whatsapp_send(ctx, a):
    from ..integrations import whatsapp
    return whatsapp.smart_send(a.get("to", ""), a.get("message", ""))["message"]

@tool("instagram_dms", "Read the user's recent Instagram direct-message threads (their account).", {},
          side_effects=True)
async def instagram_dms(ctx, a):
    from ..integrations import instagram
    r = await instagram.dms()
    return r.get("text", "Couldn't read Instagram DMs.")

@tool("whatsapp_inbox", "Recent incoming WhatsApp messages (sender name + text; no IDs).", {},
          parallel_safe=True)
async def whatsapp_inbox(ctx, a):
    from ..integrations import whatsapp
    m = whatsapp.inbox()
    return "\n".join(f"{x.get('name')}: {x.get('text')}" for x in m[-15:]) or "No new messages."
