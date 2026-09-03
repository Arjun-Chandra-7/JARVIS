"""n8n Automation Engine & Webhook Integration.

Connects Jarvis to n8n (open-source workflow automation) so Jarvis can trigger pipelines across
thousands of external apps (Notion, Slack, Discord, IoT, spreadsheets, databases, CRMs, etc.)
via simple Webhook calls. Also persists known workflow aliases in the Obsidian memory vault.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from ..config import Config, CONFIG

logger = logging.getLogger("jarvis.n8n")

_DEFAULT_N8N_BASE = "http://localhost:5678/webhook"


def get_n8n_base_url(config: Optional[Config] = None) -> str:
    cfg = config or CONFIG
    # Can be overridden via .env N8N_WEBHOOK_URL or N8N_BASE_URL
    url = getattr(cfg, "n8n_webhook_url", os.environ.get("N8N_WEBHOOK_URL", os.environ.get("N8N_BASE_URL", _DEFAULT_N8N_BASE)))
    return url.rstrip("/")


def _automations_path(config: Optional[Config] = None) -> Path:
    cfg = config or CONFIG
    path = cfg.vault_path / "Jarvis" / "automations" / "workflows.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("{}", encoding="utf-8")
    return path


def load_workflows(config: Optional[Config] = None) -> Dict[str, str]:
    """Load remembered workflow aliases -> webhook URLs or IDs from the vault."""
    path = _automations_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def register_workflow(alias: str, webhook_id_or_url: str, description: str = "", config: Optional[Config] = None) -> str:
    """Save an n8n workflow webhook under an easy-to-remember name in the vault."""
    alias_clean = alias.strip().lower()
    workflows = load_workflows(config)
    workflows[alias_clean] = {
        "url_or_id": webhook_id_or_url.strip(),
        "description": description.strip() or f"n8n webhook for {alias}",
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    path = _automations_path(config)
    path.write_text(json.dumps(workflows, indent=2), encoding="utf-8")
    return f"Saved n8n automation workflow '{alias_clean}' -> {webhook_id_or_url}."


def unregister_workflow(alias: str, config: Optional[Config] = None) -> str:
    alias_clean = alias.strip().lower()
    workflows = load_workflows(config)
    if alias_clean in workflows:
        del workflows[alias_clean]
        _automations_path(config).write_text(json.dumps(workflows, indent=2), encoding="utf-8")
        return f"Removed workflow '{alias_clean}'."
    return f"No workflow found with name '{alias_clean}'."


def list_workflows(config: Optional[Config] = None) -> str:
    workflows = load_workflows(config)
    if not workflows:
        return "No n8n automations registered yet. You can tell me to remember a new webhook alias anytime using remember_automation."
    lines = ["Registered n8n Automations:"]
    for alias, meta in sorted(workflows.items()):
        url_or_id = meta if isinstance(meta, str) else meta.get("url_or_id", "")
        desc = "" if isinstance(meta, str) else meta.get("description", "")
        lines.append(f"  • {alias}: {url_or_id}" + (f" ({desc})" if desc else ""))
    return "\n".join(lines)


def resolve_webhook_url(workflow_target: str, config: Optional[Config] = None) -> str:
    """Turn a workflow name, UUID, or relative path into a full HTTP URL."""
    target_clean = workflow_target.strip()
    workflows = load_workflows(config)
    
    # Check if it's a known alias in our vault
    alias = target_clean.lower()
    if alias in workflows:
        entry = workflows[alias]
        target_clean = entry if isinstance(entry, str) else entry.get("url_or_id", target_clean)
        
    # If it's already a full http/https URL, use it directly
    if target_clean.startswith("http://") or target_clean.startswith("https://"):
        return target_clean
        
    # Otherwise, combine with base URL (e.g., http://localhost:5678/webhook/{id})
    base = get_n8n_base_url(config)
    if not target_clean.startswith("/"):
        target_clean = "/" + target_clean
    # If base already ends with /webhook and target starts with /webhook, avoid duplication
    if base.endswith("/webhook") and target_clean.startswith("/webhook/"):
        return base[:-8] + target_clean
    return base + target_clean


def trigger_workflow(workflow: str, payload_data: Optional[Dict[str, Any]] = None, config: Optional[Config] = None) -> str:
    """Send a POST webhook payload to an n8n workflow and return the response."""
    url = resolve_webhook_url(workflow, config)
    payload = payload_data or {}
    payload["triggered_by"] = "jarvis"
    payload["timestamp"] = datetime.now().astimezone().isoformat()
    
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            
        if resp.status_code == 404:
            return (f"n8n webhook returned 404 Not Found ({url}). Is n8n running and is the workflow "
                    "activated in Production mode (or did you use the /webhook-test/ endpoint)?")
                    
        if resp.status_code >= 400:
            return f"n8n workflow error (HTTP {resp.status_code}): {resp.text[:500]}"
            
        # Parse JSON response if n8n returned structured data
        try:
            res_json = resp.json()
            if isinstance(res_json, dict) and "message" in res_json:
                return f"n8n success: {res_json['message']}"
            return f"n8n executed successfully: {json.dumps(res_json)[:400]}"
        except Exception:
            text = resp.text.strip()
            return f"n8n workflow executed successfully! {text[:200]}".strip()
            
    except httpx.ConnectError:
        return (f"Could not connect to n8n server at {url}. If n8n is running locally, make sure "
                "the service is started (e.g. via `bash scripts/n8n.sh` or `npx n8n`).")
    except Exception as exc:
        return f"Failed to trigger n8n workflow: {exc}"
