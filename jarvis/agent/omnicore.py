"""OmniCore: Universal Activity Recorder & Autonomous Personal Assistant Shield.

Records all incoming text messages, phone calls, notifications, and events into an immutable
vault log. Automatically inspects incoming messages (e.g., from teachers or colleagues) to detect
implicit schedule items (like "tuition on 6:10"), registers them without requiring explicit instructions,
and engages in autonomous two-sided conversational PA dialogues over WhatsApp, SMS, and incoming call
interception whenever Arjun is out, busy, or in tuition.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import Config, CONFIG
from ..memory import vault as vaultmod

logger = logging.getLogger("jarvis.omnicore")

# In-memory conversational threads for PA text & call handling: {jid_or_num: [messages]}
_PA_CONVOS: Dict[str, List[Dict[str, str]]] = {}


def _get_omni_log_path(config: Optional[Config] = None) -> Path:
    cfg = config or CONFIG
    path = cfg.vault_path / "Jarvis" / "logs" / "omni_records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _get_schedule_path(config: Optional[Config] = None) -> Path:
    cfg = config or CONFIG
    path = cfg.vault_path / "Jarvis" / "automations" / "schedule.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
    return path


def record_event(category: str, source: str, content: str, metadata: Optional[Dict[str, Any]] = None, config: Optional[Config] = None) -> str:
    """Record everything: texts, calls, laptop actions, and alerts into the vault database."""
    cfg = config or CONFIG
    now = datetime.now().astimezone()
    event = {
        "timestamp": now.isoformat(),
        "category": category,
        "source": source,
        "content": content,
        "metadata": metadata or {},
    }
    path = _get_omni_log_path(cfg)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as exc:
        logger.error(f"Failed to record omni event: {exc}")
        
    # Also append high-signal events to today's Obsidian journal
    if category in ("SMS/WhatsApp", "Phone Call", "Schedule Detected", "PA Intervention"):
        try:
            vaultmod.journal_append(cfg.vault_path, f"[OmniCore: {category}] {source}: {content[:120]}")
        except Exception:
            pass
    return f"Recorded [{category}] from {source}."


def list_recent_recordings(limit: int = 15, category_filter: Optional[str] = None, config: Optional[Config] = None) -> str:
    """Retrieve recent recorded texts, calls, and activities."""
    path = _get_omni_log_path(config)
    if not path.exists():
        return "No activity recorded yet."
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line.strip())
                if category_filter and data.get("category", "").lower() != category_filter.lower():
                    continue
                ts_str = data.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(ts_str)
                    ts = dt.strftime("%H:%M (%Y-%m-%d)")
                except Exception:
                    ts = ts_str[:16]
                lines.append(f"[{ts}] [{data.get('category')}] {data.get('source')}: {data.get('content')}")
        return "\n".join(lines[-limit:]) if lines else "No matching recordings found."
    except Exception as exc:
        return f"Error reading records: {exc}"


def load_schedules(config: Optional[Config] = None) -> List[Dict[str, Any]]:
    path = _get_schedule_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_schedules(schedules: List[Dict[str, Any]], config: Optional[Config] = None) -> None:
    path = _get_schedule_path(config)
    path.write_text(json.dumps(schedules, indent=2), encoding="utf-8")


def add_schedule_event(event_title: str, start_time_str: str, end_time_str: str, source: str = "Manual", config: Optional[Config] = None) -> str:
    """Register a scheduled event (like tuition or going out)."""
    schedules = load_schedules(config)
    event = {
        "id": f"evt-{len(schedules)+1}-{datetime.now().strftime('%M%S')}",
        "title": event_title,
        "start": start_time_str,
        "end": end_time_str,
        "source": source,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    schedules.append(event)
    save_schedules(schedules, config)
    record_event("Schedule Detected", source, f"Registered '{event_title}' from {start_time_str} to {end_time_str}", config=config)
    return f"Registered schedule: {event_title} ({start_time_str} to {end_time_str}) [Source: {source}]."


async def analyze_text_for_schedule(sender: str, text: str, config: Optional[Config] = None) -> Optional[str]:
    """Inspect incoming texts (e.g. 'tuition on 6:10') to automatically register events without explicit commands."""
    cfg = config or CONFIG
    now = datetime.now().astimezone()
    prompt = (
        f"Analyze this text message received by Arjun from '{sender}': \"{text}\"\n"
        f"Current system datetime: {now.isoformat()} (Date: {now:%Y-%m-%d}, Time: {now:%H:%M:%S %Z}).\n"
        "If this message implies or notifies an event, class, tuition, meeting, or period of unavailability (e.g. 'tuition on 6:10'), return ONLY a valid JSON object with exact keys: 'event', 'start', and 'end'. Formats must be strict ISO 8601 datetimes (YYYY-MM-DDTHH:MM:SS+05:30) matching today's date and 24-hour time.\n"
        "Example: {\"event\": \"Mathematics Tuition\", \"start\": \"2026-08-04T23:20:00+05:30\", \"end\": \"2026-08-05T00:30:00+05:30\"}\n"
        "If NO scheduled event or time is present in the text, return exactly: {\"event\": \"NONE\"}"
    )
    
    def _call():
        from openai import OpenAI
        base_url, key, model = cfg.llm_params()
        client = OpenAI(base_url=base_url, api_key=key or "x", max_retries=0, timeout=15)
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"} if "groq" in base_url.lower() or "openai" in base_url.lower() else None
        )
        return (r.choices[0].message.content or "").strip()

    try:
        ans = await asyncio.to_thread(_call)
        if not ans or "NONE" in ans:
            return None
        m = re.search(r'\{.*\}', ans, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            event_title = data.get("event") or data.get("title")
            st_raw = data.get("start") or data.get("start_time")
            et_raw = data.get("end") or data.get("end_time")
            
            if event_title and str(event_title).upper() != "NONE" and st_raw:
                # Helper to normalize time strings to ISO if model returned plain clock time
                def _to_iso(ts_str: str) -> str:
                    ts = str(ts_str).strip()
                    if ("T" in ts or "-" in ts) and len(ts) >= 16:
                        return ts
                    # Parse simple "11:20 PM" or "23:20"
                    from datetime import datetime as dt
                    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
                        try:
                            parsed = dt.strptime(ts, fmt)
                            combined = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                            if combined < now - timedelta(hours=4):  # assume next day if early morning
                                combined += timedelta(days=1)
                            return combined.isoformat()
                        except ValueError:
                            continue
                    return now.isoformat()

                st_iso = _to_iso(st_raw)
                if et_raw:
                    et_iso = _to_iso(et_raw)
                else:
                    try:
                        et_iso = (datetime.fromisoformat(st_iso) + timedelta(hours=1)).isoformat()
                    except Exception:
                        et_iso = (now + timedelta(hours=1)).isoformat()
                        
                return add_schedule_event(event_title, st_iso, et_iso, source=f"Message from {sender}", config=cfg)
    except Exception as exc:
        logger.warning(f"Schedule extraction failed: {exc}")
    return None



def get_current_status(config: Optional[Config] = None) -> Dict[str, Any]:
    """Check if Arjun is currently away, out, or occupied in a scheduled event (tuition/meeting)."""
    cfg = config or CONFIG
    now = datetime.now().astimezone()
    
    # 1) Check manual away mode first
    try:
        from . import away
        if away.is_away():
            return {"busy": True, "reason": away.reason() or "Away from desk", "source": "Manual Away Mode", "until": "Until further notice"}
    except Exception:
        pass

    # 2) Check autonomous schedules
    schedules = load_schedules(cfg)
    for evt in schedules:
        try:
            st_str, et_str = evt["start"], evt["end"]
            # Handle timestamps without offset
            dt_start = datetime.fromisoformat(st_str)
            if dt_start.tzinfo is None:
                dt_start = dt_start.astimezone()
            dt_end = datetime.fromisoformat(et_str)
            if dt_end.tzinfo is None:
                dt_end = dt_end.astimezone()
                
            if dt_start <= now <= dt_end:
                t_diff = int((dt_end - now).total_seconds() // 60)
                return {
                    "busy": True,
                    "reason": evt["title"],
                    "source": evt.get("source", "Autonomous Schedule"),
                    "until": dt_end.strftime("%H:%M") + (f" (in {t_diff} mins)" if t_diff > 0 else ""),
                }
        except Exception:
            continue
            
    return {"busy": False, "reason": "Available", "source": "", "until": ""}


async def execute_pa_dialogue(contact_id: str, sender_name: str, incoming_text: str, channel: str = "WhatsApp", config: Optional[Config] = None) -> Optional[str]:
    """Generate a intelligent 2-sided Personal Assistant conversational response when Arjun is busy."""
    cfg = config or CONFIG
    status = get_current_status(cfg)
    if not status.get("busy"):
        return None  # Arjun is free; let him answer personally unless away mode is explicitly forced
        
    reason_str = status.get("reason", "busy")
    until_str = status.get("until", "soon")
    
    hist = _PA_CONVOS.setdefault(contact_id, [])
    hist.append({"role": "user", "content": f"[{channel}] {incoming_text}"})
    
    sys_prompt = (
        f"You are Jarvis, Tony Stark / Arjun's highly professional, eloquent AI Personal Assistant. "
        f"Right now, Arjun is OCCUPIED / AWAY: {reason_str} until {until_str}. "
        f"You are conducting a live two-sided conversation on {channel} with {sender_name} on Arjun's behalf. "
        f"Rules:\n"
        f"1. Be immensely articulate, respectful, and sharp—a dependable executive assistant.\n"
        f"2. Inform them of Arjun's current activity ({reason_str} until {until_str}) if appropriate, without divulging sensitive secrets.\n"
        f"3. Actively converse: ask if there is an urgent matter or detail you should note down for Arjun's immediate attention upon his return.\n"
        f"4. Keep replies concise (1 to 3 sentences max) and natural for {channel}.\n"
        f"5. Do NOT pretend to be Arjun—always represent yourself as Jarvis, his PA."
    )
    
    messages = [{"role": "system", "content": sys_prompt}] + hist[-12:]

    def _call():
        from openai import OpenAI
        base_url, key, model = cfg.llm_params()
        client = OpenAI(base_url=base_url, api_key=key or "x", max_retries=0, timeout=20)
        r = client.chat.completions.create(model=model, messages=messages, temperature=0.5, max_tokens=220)
        return (r.choices[0].message.content or "").strip()

    try:
        reply = await asyncio.to_thread(_call)
    except Exception as exc:
        reply = f"Hello {sender_name}, this is Jarvis, Arjun's AI Assistant. Arjun is currently occupied ({reason_str} until {until_str}). I have logged your communication for his return."
        
    hist.append({"role": "assistant", "content": reply})
    record_event("PA Intervention", f"{sender_name} ({channel})", f"Jarvis replied: {reply}", config=cfg)
    
    # Actually send the reply over WhatsApp if channel is WhatsApp
    if "whatsapp" in channel.lower() or "sms" in channel.lower() or "call" in channel.lower():
        try:
            from ..integrations import whatsapp
            whatsapp.smart_send(contact_id or sender_name, reply)
        except Exception as exc:
            logger.error(f"Failed to transmit PA response over {channel}: {exc}")
            
    return reply


async def handle_incoming_text(sender_name: str, sender_id: str, text: str, config: Optional[Config] = None) -> Dict[str, Any]:
    """Master entry point for every incoming message. Records everything, extracts schedules, and defends line if busy."""
    cfg = config or CONFIG
    # 1) Record everything
    record_event("SMS/WhatsApp", f"{sender_name} ({sender_id})", text, config=cfg)
    
    # 2) Autonomous Schedule Detection
    sched_msg = await analyze_text_for_schedule(sender_name, text, config=cfg)
    
    # 3) Check if PA needs to intercept and hold a 2-sided conversation
    pa_reply = await execute_pa_dialogue(sender_id or sender_name, sender_name, text, channel="WhatsApp", config=cfg)
    
    return {
        "recorded": True,
        "schedule_detected": sched_msg,
        "pa_intervened": bool(pa_reply),
        "pa_reply": pa_reply,
    }


async def handle_incoming_call(caller_name: str, caller_num: str, config: Optional[Config] = None) -> Dict[str, Any]:
    """Master entry point for incoming phone calls. Records call and engages 2-sided conversational PA interception when busy."""
    cfg = config or CONFIG
    record_event("Phone Call", f"{caller_name} ({caller_num})", "Incoming call received", config=cfg)
    
    status = get_current_status(cfg)
    if not status.get("busy"):
        return {"recorded": True, "intervened": False, "reason": "User is available; letting phone ring."}
        
    reason_str = status.get("reason", "occupied")
    until_str = status.get("until", "soon")
    
    # Try silencing or rejecting physical ring via KDE connect / apps so Arjun isn't interrupted in tuition
    try:
        from ..integrations import apps
        # We can trigger phone ring stop or notify
        apps._kc(cfg.kde_device_id or None, "--stop-ringing")
    except Exception:
        pass
        
    # Immediately dispatch a conversational 2-sided PA text/WhatsApp to the caller!
    initial_text = f"Incoming call intercepted while Arjun is {reason_str}."
    pa_reply = await execute_pa_dialogue(caller_num or caller_name, caller_name or "Caller", initial_text, channel="Phone Call Interception", config=cfg)
    
    return {
        "recorded": True,
        "intervened": True,
        "pa_reply": pa_reply,
        "status": f"Intercepted call from {caller_name} while {reason_str}. PA conversation initiated via text/bridge."
    }
