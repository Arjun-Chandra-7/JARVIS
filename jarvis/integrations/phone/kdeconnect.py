"""Phone bridge over KDE Connect's D-Bus API (async, via dbus-next).

Reads mirrored notifications (WhatsApp, Instagram, SMS, etc.), watches incoming calls, replies to
repliable notifications, and sends SMS. Requires KDE Connect running with a paired phone that has
granted Notification access.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import Awaitable, Callable, Optional

from dbus_next.aio import MessageBus

SERVICE = "org.kde.kdeconnect"
NOTIF_IFACE = "org.kde.kdeconnect.device.notifications"
NOTIF_OBJ_IFACE = "org.kde.kdeconnect.device.notifications.notification"
TEL_IFACE = "org.kde.kdeconnect.device.telephony"
PROPS = "org.freedesktop.DBus.Properties"

NotifHandler = Callable[[dict], Awaitable[None]]
CallHandler = Callable[[dict], None]


class KDEConnect:
    def __init__(self, device_id: Optional[str] = None) -> None:
        self.device_id = device_id
        self.bus: Optional[MessageBus] = None
        self.base: Optional[str] = None
        self._notif = None
        self._tel = None

    async def connect(self) -> "KDEConnect":
        self.bus = await MessageBus().connect()
        if not self.device_id:
            self.device_id = await self._first_device()
        if not self.device_id:
            raise RuntimeError("No paired KDE Connect device found (is your phone connected?).")
        self.base = f"/modules/kdeconnect/devices/{self.device_id}"
        self._notif = await self._iface(f"{self.base}/notifications", NOTIF_IFACE)
        try:
            self._tel = await self._iface(f"{self.base}/telephony", TEL_IFACE)
        except Exception:  # noqa: BLE001 - telephony plugin may be off
            self._tel = None
        return self

    async def _iface(self, path: str, name: str):
        node = await self.bus.introspect(SERVICE, path)
        obj = self.bus.get_proxy_object(SERVICE, path, node)
        return obj.get_interface(name)

    async def _first_device(self) -> Optional[str]:
        node = await self.bus.introspect(SERVICE, "/modules/kdeconnect/devices")
        return node.nodes[0].name if node.nodes else None

    # --- notifications ---------------------------------------------------
    async def notification_details(self, public_id: str) -> dict:
        props = await self._iface(f"{self.base}/notifications/{public_id}", PROPS)
        raw = await props.call_get_all(NOTIF_OBJ_IFACE)

        def g(key):
            v = raw.get(key)
            return v.value if v is not None else None

        return {
            "id": public_id,
            "app": g("appName") or "",
            "title": g("title") or "",
            "text": g("text") or "",
            "ticker": g("ticker") or "",
            "repliable": bool(g("hasReplyAction")),
        }

    async def active_notifications(self) -> list[dict]:
        ids = await self._notif.call_active_notifications()
        out = []
        for nid in ids:
            try:
                out.append(await self.notification_details(nid))
            except Exception:  # noqa: BLE001 - notification may vanish mid-read
                pass
        return out

    async def reply(self, public_id: str, message: str) -> None:
        await self._notif.call_send_reply(public_id, message)

    async def dismiss(self, public_id: str) -> None:
        niface = await self._iface(f"{self.base}/notifications/{public_id}", NOTIF_OBJ_IFACE)
        await niface.call_dismiss()

    def watch_notifications(self, handler: NotifHandler) -> None:
        async def _run(public_id: str):
            try:
                handler_arg = await self.notification_details(public_id)
            except Exception:  # noqa: BLE001
                return
            await handler(handler_arg)

        self._notif.on_notification_posted(lambda public_id: asyncio.create_task(_run(public_id)))

    # --- calls -----------------------------------------------------------
    def watch_calls(self, handler: CallHandler) -> None:
        if not self._tel:
            return

        def _cb(event, number, name):  # callReceived(event, phoneNumber, contactName)
            handler({"event": event, "number": number, "name": name})

        self._tel.on_call_received(_cb)

    # --- SMS (via the stable CLI) ---------------------------------------
    def send_sms(self, number: str, message: str) -> bool:
        cli = shutil.which("kdeconnect-cli")
        if not cli or not self.device_id:
            return False
        result = subprocess.run(
            [cli, "-d", self.device_id, "--send-sms", message, "--destination", number],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
