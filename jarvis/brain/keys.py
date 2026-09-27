"""The owner's own API keys: stored in the OS keyring, chosen responsibly, never shown again.

Storage
    Secret Service (gnome-keyring / KWallet's compatibility service) over D-Bus, one item per key,
    attributes ``{"application": "jarvis", "jarvis-key-id": <id>}``. The item's label names the
    provider and the owner's label — never any part of the key.
    When no Secret Service answers, a 0600 file in the config directory, and the Brain tab says so
    plainly: "file (not encrypted)". Tests use ``MemoryBackend``.

    Keys already in ``.env`` appear as read-only entries (``source: env``). "Move to keyring"
    copies the value into the keyring and disables the env entry; the ``.env`` line is never
    edited or deleted by Jarvis.

What leaves this module
    ``public()``: id, label, priority, enabled, source, a six-character fingerprint of a hash of
    the key (so two keys can be told apart without revealing either), status. Never the key, never
    a prefix or suffix of it.

When another key is chosen (and only then)
    the owner's priority order; a key quarantined by 401/403 (until the owner tests or replaces
    it); a key backing off after 429 (for as long as the provider asked); a key whose quota the
    provider reported exhausted; round-robin across equal-priority keys when the owner turned on
    load distribution. A network failure says nothing about a key and changes nothing.
    ``executor`` caps attempts per request, so one request never walks every key.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets as _secrets
import threading
import time
from pathlib import Path
from typing import Optional

from . import adapters
from .registry import BrainSettings, BrainState

_APP = "jarvis"
BACKOFF_DEFAULT_S = 60.0
QUOTA_BACKOFF_S = 6 * 3600.0
MAX_KEYS_PER_PROVIDER = 12
_PENDING_DELETE: dict[str, tuple[str, float]] = {}


def fingerprint(secret: str) -> str:
    return hashlib.sha256(("jarvis-key:" + secret).encode()).hexdigest()[:6]


# --------------------------------------------------------------------------- backends

class MemoryBackend:
    name, label = "memory", "memory (tests)"

    def __init__(self):
        self._items: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def get(self, kid: str) -> Optional[str]:
        return self._items.get(kid)

    def set(self, kid: str, secret: str, label: str) -> None:
        self._items[kid] = secret

    def delete(self, kid: str) -> None:
        self._items.pop(kid, None)


class FileBackend:
    """Last resort when no keyring answers. Readable only by this user; not encrypted."""

    name, label = "file", "file (not encrypted)"

    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.environ.get("JARVIS_CONFIG_DIR", "~/.config/jarvis")).expanduser() / "brain-keys.json"

    def available(self) -> bool:
        return True

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, kid: str) -> Optional[str]:
        return self._load().get(kid)

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path) + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(str(self.path) + ".tmp", self.path)

    def set(self, kid: str, secret: str, label: str) -> None:
        data = self._load()
        data[kid] = secret
        self._write(data)

    def delete(self, kid: str) -> None:
        data = self._load()
        if data.pop(kid, None) is not None:
            self._write(data)


class SecretServiceBackend:
    """org.freedesktop.secrets over the session bus, using dbus_next.

    Secrets travel with the "plain" algorithm over the *session* bus — a private per-user socket —
    which is what libsecret itself falls back to. Each call runs on its own short-lived event loop
    in a worker thread so it can be used from sync and async code alike.
    """

    name, label = "secret_service", "system keyring"
    _BUS = "org.freedesktop.secrets"
    _PATH = "/org/freedesktop/secrets"

    def __init__(self):
        self._ok: Optional[bool] = None

    def _run(self, coro_fn, timeout: float = 6.0):
        box: dict = {}

        def worker():
            loop = asyncio.new_event_loop()
            try:
                box["v"] = loop.run_until_complete(asyncio.wait_for(coro_fn(), timeout))
            except BaseException as exc:  # noqa: BLE001
                box["e"] = exc
            finally:
                loop.close()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout + 1)
        if "e" in box:
            raise box["e"]
        return box.get("v")

    async def _session(self):
        from dbus_next.aio import MessageBus
        from dbus_next import Variant

        bus = await MessageBus().connect()
        intro = await bus.introspect(self._BUS, self._PATH)
        service = bus.get_proxy_object(self._BUS, self._PATH, intro).get_interface("org.freedesktop.Secret.Service")
        _, session = await service.call_open_session("plain", Variant("s", ""))
        return bus, service, session

    async def _collection(self, bus, service):
        path = await service.call_read_alias("default")
        if path == "/":
            raise RuntimeError("no default keyring collection")
        intro = await bus.introspect(self._BUS, path)
        return path, bus.get_proxy_object(self._BUS, path, intro).get_interface("org.freedesktop.Secret.Collection")

    async def _find(self, service, kid: str):
        unlocked, locked = await service.call_search_items({"application": _APP, "jarvis-key-id": kid})
        if locked:
            await service.call_unlock(locked)
        return list(unlocked) + list(locked)

    def available(self) -> bool:
        if self._ok is None:
            async def probe():
                bus, service, _ = await self._session()
                try:
                    await self._collection(bus, service)
                finally:
                    bus.disconnect()
                return True
            try:
                self._ok = bool(self._run(probe, 3.0))
            except Exception:  # noqa: BLE001
                self._ok = False
        return self._ok

    def get(self, kid: str) -> Optional[str]:
        async def go():
            bus, service, session = await self._session()
            try:
                items = await self._find(service, kid)
                if not items:
                    return None
                got = await service.call_get_secrets(items[:1], session)
                for _, (_, _, value, _) in got.items():
                    return bytes(value).decode()
                return None
            finally:
                bus.disconnect()
        return self._run(go)

    def set(self, kid: str, secret: str, label: str) -> None:
        from dbus_next import Variant

        async def go():
            bus, service, session = await self._session()
            try:
                _, coll = await self._collection(bus, service)
                props = {"org.freedesktop.Secret.Item.Label": Variant("s", label),
                         "org.freedesktop.Secret.Item.Attributes": Variant("a{ss}", {"application": _APP,
                                                                                      "jarvis-key-id": kid})}
                await coll.call_create_item(props, [session, b"", secret.encode(), "text/plain"], True)
            finally:
                bus.disconnect()
        self._run(go)

    def delete(self, kid: str) -> None:
        async def go():
            bus, service, _ = await self._session()
            try:
                for path in await self._find(service, kid):
                    intro = await bus.introspect(self._BUS, path)
                    item = bus.get_proxy_object(self._BUS, path, intro).get_interface("org.freedesktop.Secret.Item")
                    await item.call_delete()
            finally:
                bus.disconnect()
        self._run(go)


def default_backend():
    if os.environ.get("JARVIS_KEY_BACKEND") == "memory":
        return MemoryBackend()
    ss = SecretServiceBackend()
    return ss if ss.available() else FileBackend()


# --------------------------------------------------------------------------- the store

class KeyError_(Exception):
    pass


class KeyStore:
    def __init__(self, settings: BrainSettings, state: BrainState, backend=None, env: dict | None = None):
        self.settings, self.state = settings, state
        self.backend = backend or default_backend()
        self.env = os.environ if env is None else env
        self._rr: dict[str, int] = {}
        self._pending_delete = _PENDING_DELETE          # process-wide: survives a settings reload

    # -- metadata --------------------------------------------------------------------------
    def _meta(self, pid: str) -> list[dict]:
        return self.settings["keys"].get(pid, [])          # reads never add an entry

    def _env_entries(self, pid: str, env_var: str) -> list[dict]:
        if not env_var or not self.env.get(env_var):
            return []
        disabled = any(k.get("migrated_from_env") == env_var for k in self._meta(pid))
        override = self.settings["keys"].get(f"{pid}.__env__", {})
        return [{"id": f"env-{pid}", "provider": pid, "label": f"From .env ({env_var})", "source": "env",
                 "env_var": env_var, "priority": int(override.get("priority", 100)),
                 "enabled": bool(override.get("enabled", not disabled))}]

    def entries(self, pid: str, env_var: str = "") -> list[dict]:
        rows = [dict(k) for k in self._meta(pid)] + self._env_entries(pid, env_var)
        return sorted(rows, key=lambda k: (int(k.get("priority", 50)), k.get("added_at", 0)))

    def secret(self, entry: dict) -> str:
        """The key itself. For the adapter only — never returned by the API."""
        if entry.get("source") == "env":
            return self.env.get(entry.get("env_var", ""), "")
        return self.backend.get(entry["id"]) or ""

    def status(self, kid: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        row = self.state.data["keys"].get(kid, {})
        if row.get("quarantined"):
            return {"state": "quarantined", "reason": row["quarantined"]}
        if float(row.get("until", 0)) > now:
            return {"state": "backoff", "reason": row.get("why", ""), "seconds": int(row["until"] - now)}
        if row.get("last_ok"):
            return {"state": "ok", "last_ok": row["last_ok"]}
        return {"state": "untested"}

    def public(self, pid: str, env_var: str = "") -> list[dict]:
        out = []
        for e in self.entries(pid, env_var):
            fp = fingerprint(self.secret(e)) if e.get("source") == "env" else e.get("fingerprint", "")
            out.append({"id": e["id"], "label": e.get("label", ""), "priority": e.get("priority", 50),
                        "enabled": e.get("enabled", True), "source": e.get("source", self.backend.name),
                        "storage": "the .env file (read-only here)" if e.get("source") == "env" else self.backend.label,
                        "fingerprint": fp, "status": self.status(e["id"])})
        return out

    # -- changes ---------------------------------------------------------------------------
    def add(self, pid: str, secret: str, label: str = "") -> dict:
        secret = (secret or "").strip()
        if len(secret) < 8 or len(secret) > 512 or any(c.isspace() for c in secret):
            raise KeyError_("That doesn't look like an API key.")
        meta = self.settings["keys"].setdefault(pid, [])
        if len(meta) >= MAX_KEYS_PER_PROVIDER:
            raise KeyError_("Too many keys for one provider.")
        fp = fingerprint(secret)
        if any(k.get("fingerprint") == fp for k in meta):
            raise KeyError_("That key is already saved.")
        kid = "k_" + _secrets.token_hex(4)
        self.backend.set(kid, secret, f"Jarvis — {pid} — {label or kid}")
        row = {"id": kid, "provider": pid, "label": (label or f"Key {len(meta) + 1}")[:40], "source": self.backend.name,
               "priority": (max([int(k.get("priority", 50)) for k in meta] or [0]) + 10), "enabled": True,
               "fingerprint": fp, "added_at": time.time()}
        meta.append(row)
        self.settings.save()
        return {k: v for k, v in row.items()}

    def _find(self, pid: str, kid: str) -> dict:
        for k in self._meta(pid):
            if k["id"] == kid:
                return k
        raise KeyError_("No such key.")

    def rename(self, pid: str, kid: str, label: str) -> None:
        if kid.startswith("env-"):
            raise KeyError_("Keys from .env are named by their variable.")
        self._find(pid, kid)["label"] = (label or "").strip()[:40] or kid
        self.settings.save()

    def set_enabled(self, pid: str, kid: str, enabled: bool) -> None:
        if kid.startswith("env-"):
            self.settings["keys"].setdefault(f"{pid}.__env__", {})["enabled"] = bool(enabled)
        else:
            self._find(pid, kid)["enabled"] = bool(enabled)
        self.settings.save()

    def reorder(self, pid: str, ordered_ids: list[str]) -> None:
        for i, kid in enumerate(ordered_ids):
            if kid.startswith("env-"):
                self.settings["keys"].setdefault(f"{pid}.__env__", {})["priority"] = (i + 1) * 10
            else:
                self._find(pid, kid)["priority"] = (i + 1) * 10
        self.settings.save()

    def request_delete(self, pid: str, kid: str) -> str:
        """Step one of two: a token the confirmation must echo back within two minutes."""
        if kid.startswith("env-"):
            raise KeyError_("Keys in .env are not deleted by Jarvis — disable it here, or edit .env yourself.")
        self._find(pid, kid)
        token = _secrets.token_urlsafe(8)
        self._pending_delete[kid] = (token, time.time() + 120)
        return token

    def delete(self, pid: str, kid: str, token: str) -> None:
        want = self._pending_delete.get(kid)
        if not want or want[0] != token or want[1] < time.time():
            raise KeyError_("Deleting a key needs confirming.")
        self._pending_delete.pop(kid, None)
        self.backend.delete(kid)
        self.settings["keys"][pid] = [k for k in self._meta(pid) if k["id"] != kid]
        self.settings.save()
        self.state.data["keys"].pop(kid, None)
        self.state.save()

    def migrate_env(self, pid: str, env_var: str) -> dict:
        """Copy a .env key into the keyring. The .env line stays exactly as it is."""
        value = self.env.get(env_var, "")
        if not value:
            raise KeyError_(f"{env_var} is not set.")
        fp = fingerprint(value)
        existing = [k for k in self._meta(pid) if k.get("fingerprint") == fp]
        row = existing[0] if existing else self.add(pid, value, f"Moved from .env")
        for k in self._meta(pid):
            if k["id"] == row["id"]:
                k["migrated_from_env"] = env_var
        self.settings.save()
        return row

    # -- choosing --------------------------------------------------------------------------
    def usable(self, pid: str, env_var: str = "", now: float | None = None, distribute: bool = False) -> list[dict]:
        """Keys that may be tried now, in the order they should be tried."""
        now = time.time() if now is None else now
        rows = [e for e in self.entries(pid, env_var) if e.get("enabled", True)
                and self.status(e["id"], now)["state"] in {"ok", "untested"}]
        if distribute and len(rows) > 1:
            top = [r for r in rows if r.get("priority") == rows[0].get("priority")]
            if len(top) > 1:
                i = self._rr.get(pid, 0) % len(top)
                self._rr[pid] = i + 1
                rows = top[i:] + top[:i] + rows[len(top):]
        return rows

    def report(self, kid: str, kind: str, retry_after: float | None = None, now: float | None = None) -> None:
        now = time.time() if now is None else now
        row = self.state.data["keys"].setdefault(kid, {})
        if kind in (adapters.AUTH, adapters.PERMISSION):
            row["quarantined"] = kind              # until the owner tests or replaces it
        elif kind == adapters.RATE_LIMIT:
            row.update(until=now + max(1.0, retry_after or BACKOFF_DEFAULT_S), why=kind)
        elif kind == adapters.QUOTA:
            row.update(until=now + max(QUOTA_BACKOFF_S, retry_after or 0), why=kind)
        else:
            return                                 # network, outage, model, malformed: not the key
        self.state.save()

    def report_ok(self, kid: str) -> None:
        row = self.state.data["keys"].setdefault(kid, {})
        row.pop("quarantined", None)
        row.pop("until", None)
        row["last_ok"] = time.time()
        self.state.save()

    def test(self, pid: str, kid: str, adapter, env_var: str = "") -> dict:
        """List models with this key. Returns a safe outcome; clears a quarantine on success."""
        entry = next((e for e in self.entries(pid, env_var) if e["id"] == kid), None)
        if entry is None:
            raise KeyError_("No such key.")
        try:
            models = adapter.list_models(self.secret(entry), timeout=8.0)
        except adapters.ProviderError as err:
            self.report(kid, err.kind, err.retry_after)
            return {"ok": False, "kind": err.kind, "detail": err.detail}
        self.report_ok(kid)
        return {"ok": True, "models": len(models)}
