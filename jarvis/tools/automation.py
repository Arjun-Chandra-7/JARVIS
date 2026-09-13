"""n8n webhooks — the bridge to a few thousand other apps.

Migrated out of the single 831-line `build_registry` closure. Each tool states its own
concurrency and side-effect facts, so nothing has to be remembered elsewhere.
"""

from __future__ import annotations

from .base import as_bool, as_int, tool

@tool("trigger_automation", "Trigger an n8n automation workflow via webhook alias or URL. Pass extra JSON fields in 'payload_json'.",
      {"workflow": {"type": "string"}, "payload_json": {"type": "string"}}, ["workflow"],
          side_effects=True)
async def trigger_automation(ctx, a):
    from ..integrations import n8n
    import json
    data = {}
    if a.get("payload_json"):
        try:
            data = json.loads(a["payload_json"])
        except Exception:
            data = {"data": a["payload_json"]}
    return n8n.trigger_workflow(a.get("workflow", ""), data, config=ctx.config)

@tool("remember_automation", "Save an n8n webhook ID/URL under an easy alias (e.g. alias='notion-sync') so you can trigger it anytime.",
      {"alias": {"type": "string"}, "url_or_id": {"type": "string"}, "description": {"type": "string"}}, ["alias", "url_or_id"],
          side_effects=True)
async def remember_automation(ctx, a):
    from ..integrations import n8n
    return n8n.register_workflow(a.get("alias", ""), a.get("url_or_id", ""), a.get("description", ""), config=ctx.config)

@tool("list_automations", "List all remembered n8n automations and workflows.", {},
          parallel_safe=True)
async def list_automations(ctx, a):
    from ..integrations import n8n
    return n8n.list_workflows(config=ctx.config)
