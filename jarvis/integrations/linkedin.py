"""LinkedIn Content Copilot — Jarvis integration.

Talks to the Linkdin-Bot backend (a separate project, normally at
~/Dev/Linkdin) over its local HTTP API, opens its desktop console, and turns
its state into something Jarvis can say out loud.

Two deliberate limits, inherited from that project and kept here:

* **Nothing publishes without the user seeing or hearing it.** A draft can be
  approved by voice only after Jarvis has read it aloud in this session, and
  the approval carries the content hash captured at the moment it was read -
  so if the draft changed in between, the backend refuses it.
* **No invitation is ever sent automatically.** Jarvis can tell the user who
  is worth connecting with and open the queue; the user connects by hand.
  Automating that breaches LinkedIn's terms and risks the account.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import apps

DEFAULT_URL = "http://127.0.0.1:8000"
PROJECT_DIR = Path(os.environ.get("LINKEDIN_COPILOT_DIR", str(Path.home() / "Dev" / "Linkdin" / "repo")))
TIMEOUT = 20

_token: str | None = None
# draft_id -> content hash, for drafts Jarvis has read aloud this session.
_read_aloud: dict[int, str] = {}


def base_url() -> str:
    return os.environ.get("LINKEDIN_COPILOT_URL", DEFAULT_URL).rstrip("/")


# --------------------------------------------------------------- transport --


def _request(path: str, method: str = "GET", body: dict | None = None, auth: bool = True) -> Any:
    global _token
    for attempt in range(2):
        url = f"{base_url()}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth and _token:
            req.add_header("Authorization", f"Bearer {_token}")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode()
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if not (auth and exc.code == 401 and attempt == 0):
                raise
            # A service restart can invalidate the process-local token. Renew
            # once so the next request does not require a Jarvis restart.
            _token = None
            _connect()
    return None  # pragma: no cover - the retry either returns or raises


def _connect() -> None:
    """Get a token. The backend only issues these to callers on this machine."""
    global _token
    if _token:
        return
    session = _request("/api/v1/auth/desktop-session", method="POST", auth=False)
    _token = session["token"]


def is_running() -> bool:
    try:
        with urllib.request.urlopen(f"{base_url()}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_backend() -> bool:
    """Start the copilot backend if it isn't already up.

    Returns True once it answers, False if it could not be started - the user
    is told rather than left with a silent failure.
    """
    if is_running():
        return True
    script = PROJECT_DIR / "scripts" / "dev.sh"
    if not script.exists():
        return False
    try:
        subprocess.Popen(
            ["bash", str(script)],
            cwd=str(PROJECT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False
    for _ in range(30):
        time.sleep(1)
        if is_running():
            return True
    return False


def _ensure() -> str | None:
    """Make sure the backend is up and we have a token. Returns an error line."""
    global _token
    if not is_running():
        if not start_backend():
            return (
                "The LinkedIn copilot backend isn't running and I couldn't start it. "
                f"Try ./scripts/dev.sh in {PROJECT_DIR}."
            )
        _token = None
    try:
        _connect()
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't get a session from the LinkedIn copilot ({type(exc).__name__})."
    return None


# ------------------------------------------------------------------ console --


def open_console(view: str = "dashboard") -> bool:
    """Open the desktop console, on a specific screen."""
    url = f"{base_url()}/app/?view={view}"
    return apps.open_url(url, browser="opera", new_window=True) is not None


def open_profile() -> str:
    """Open the account profile stored by the LinkedIn copilot."""
    error = _ensure()
    if error:
        return error
    try:
        data = _request("/api/v1/settings")
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the LinkedIn profile setting ({type(exc).__name__})."

    profile_url = str((data.get("values") or {}).get("linkedin_profile_url") or "").strip()
    if not profile_url:
        return "No LinkedIn profile URL is stored in the copilot settings."
    opened = apps.open_url(profile_url, browser="opera", new_window=True)
    return "Opened your LinkedIn profile." if opened else "I couldn't open a browser window."


# -------------------------------------------------------------------- reads --


def briefing() -> dict | str:
    error = _ensure()
    if error:
        return error
    try:
        return _request("/api/v1/briefing")
    except urllib.error.HTTPError as exc:
        return f"The copilot returned an error ({exc.code})."
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the LinkedIn stats ({type(exc).__name__})."


def stats(open_gui: bool = True) -> str:
    """Spoken stats, with the console opened alongside."""
    data = briefing()
    if isinstance(data, str):
        return data
    opened = open_console("dashboard") if open_gui else False
    speech = data["speech"]
    if open_gui:
        speech += " I've put the console on screen." if opened else " I couldn't open the console window."
    return speech


def pending_drafts() -> str:
    error = _ensure()
    if error:
        return error
    try:
        data = _request("/api/v1/drafts")
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the approval queue ({type(exc).__name__})."

    items = data.get("items", [])
    if not items:
        return "Nothing is waiting for approval. Weak drafts get rejected before they reach you."

    lines = [f"{len(items)} draft{'s' if len(items) != 1 else ''} waiting:"]
    for index, item in enumerate(items, 1):
        quality = item.get("quality_score")
        lines.append(
            f"{index}. {item.get('hook') or item.get('title')}"
            + (f" — quality {quality}" if quality is not None else "")
        )
    lines.append("Say “read the first one” and I'll read it out before you decide.")
    return " ".join(lines)


def delete_scheduled(position: int = 1) -> str:
    """Cancel one pending scheduled post selected by its displayed position."""
    error = _ensure()
    if error:
        return error
    try:
        slots = _request("/api/v1/schedule") or []
        index = max(1, position) - 1
        if index >= len(slots):
            return f"There are only {len(slots)} scheduled posts."
        slot = slots[index]
        _request(f"/api/v1/schedule/{slot['id']}", method="DELETE")
    except urllib.error.HTTPError as exc:
        return f"The copilot refused to cancel that post ({exc.code})."
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't cancel that scheduled post ({type(exc).__name__})."
    return f"Cancelled scheduled post {position}: {slot.get('title') or 'untitled'}."


def top_ideas() -> str:
    """Return the ten freshest ranked public-source topics for future posts."""
    error = _ensure()
    if error:
        return error
    try:
        data = _request("/api/v1/ideas/top") or {}
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't refresh the public topic list ({type(exc).__name__})."
    items = data.get("items") or []
    if not items:
        return "The public feeds are refreshing, but there are no ranked topics yet. Ask again shortly."
    lines = ["Top topics to turn into posts:"]
    for item in items[:10]:
        summary = " ".join((item.get("summary") or "").split())
        detail = f" — {summary[:180]}" if summary else ""
        lines.append(
            f"{item.get('rank', len(lines))}. {item.get('topic', 'Untitled')}{detail} "
            f"({item.get('source', 'public feed')})"
        )
    if data.get("refresh_queued"):
        lines.append("I also queued a fresh public-feed scan; the next request will include new items.")
    lines.append("I queued research and drafts for these topics; approval is still required before anything publishes.")
    return " ".join(lines)


def read_draft(position: int = 1) -> str:
    """Read a queued draft aloud and remember exactly what was read."""
    error = _ensure()
    if error:
        return error
    try:
        items = _request("/api/v1/drafts").get("items", [])
        if not items:
            return "There's nothing in the queue to read."
        index = max(1, position) - 1
        if index >= len(items):
            return f"There are only {len(items)} drafts in the queue."
        detail = _request(f"/api/v1/drafts/{items[index]['id']}")
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read that draft ({type(exc).__name__})."

    version = detail.get("current_version") or {}
    content = version.get("content", "")
    if not content:
        return "That draft has no text yet."

    # Remember the exact bytes read, so an approval can be bound to them.
    _read_aloud[detail["id"]] = version["content_hash"]

    quality = (version.get("quality") or {}).get("quality")
    header = f"Draft {position}, {detail.get('post_type', '').replace('_', ' ').lower()}"
    if quality is not None:
        header += f", quality {quality}"
    return (
        f"{header}. Here it is. {content} "
        "That's the end of it. Say “approve it” if you're happy, or open the console to edit."
    )


def approve_read_draft(position: int = 1) -> str:
    """Approve a draft, but only one that was actually read aloud first."""
    error = _ensure()
    if error:
        return error
    try:
        items = _request("/api/v1/drafts").get("items", [])
        if not items:
            return "There's nothing in the queue."
        index = max(1, position) - 1
        if index >= len(items):
            return f"There are only {len(items)} drafts in the queue."
        draft_id = items[index]["id"]
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't reach the queue ({type(exc).__name__})."

    known_hash = _read_aloud.get(draft_id)
    if not known_hash:
        return (
            "I haven't read that one to you yet, and I won't approve a post you haven't heard. "
            "Say “read it” first, or approve it in the console."
        )

    try:
        result = _request(
            "/api/v1/approvals",
            method="POST",
            body={
                "draft_id": draft_id,
                "action": "APPROVE",
                "expected_content_hash": known_hash,
                "client_action_id": f"jarvis-{draft_id}-{int(time.time())}",
            },
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            _read_aloud.pop(draft_id, None)
            return (
                "That draft changed after I read it to you, so I didn't approve it. "
                "Ask me to read it again."
            )
        return f"The copilot refused the approval ({exc.code})."
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't submit the approval ({type(exc).__name__})."

    _read_aloud.pop(draft_id, None)
    when = result.get("scheduled_at")
    if when:
        from datetime import datetime

        try:
            stamp = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone()
            return f"Approved. It goes out {stamp.strftime('%A at %-I:%M %p').lower()}."
        except ValueError:
            pass
    return result.get("message", "Approved.")


def networking(open_gui: bool = False) -> str:
    error = _ensure()
    if error:
        return error
    try:
        people = _request("/api/v1/network")
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't read the networking queue ({type(exc).__name__})."

    if not people:
        return "No connection suggestions right now."
    if open_gui:
        open_console("network")

    lines = [f"{len(people)} {'person' if len(people) == 1 else 'people'} worth connecting with."]
    for person in people[:4]:
        who = person["name"]
        role = " ".join(filter(None, [person.get("role"), person.get("company")]))
        lines.append(f"{who}{', ' + role if role else ''}. {person['reason_for_recommendation']}")
    lines.append(
        "I've opened the queue — one keystroke opens a profile with your note copied. "
        "You send the invitations yourself; I don't automate that."
        if open_gui
        else "Open the console to work through them."
    )
    return " ".join(lines)


def capture_idea(topic: str, notes: str = "", category: str = "BUILD_LOG") -> str:
    """Turn something the user just said into a draft."""
    error = _ensure()
    if error:
        return error
    try:
        draft = _request(
            "/api/v1/drafts/from-idea",
            method="POST",
            body={"topic": topic, "notes": notes, "category": category},
        )
    except Exception as exc:  # noqa: BLE001
        return f"I couldn't write that up ({type(exc).__name__})."

    if draft.get("status") == "READY_FOR_REVIEW":
        quality = draft.get("quality_score")
        return (
            f"Written and it passed the quality gate{f' at {quality}' if quality else ''}. "
            "Say “read it” and I'll read it to you."
        )
    return (
        "I wrote it, but the quality gate held it back — it wasn't specific enough. "
        "Give me more detail about what actually happened and I'll try again."
    )
