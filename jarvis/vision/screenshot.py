"""Screen capture: save a screenshot Jarvis can then view with its Read tool (Opus vision).

Returns a PNG path, or None if capture failed / came back blank.

On Wayland (GNOME/KDE), generic X11 grabbers and even `gnome-screenshot` are denied by the
compositor and return a black frame, so we go through the **XDG desktop portal**
(org.freedesktop.portal.Screenshot) over D-Bus — the sanctioned Wayland path. GNOME asks for a
one-time "Share" permission and then remembers it. On X11 (or if the portal is unavailable) we
fall back to whatever CLI grabber is installed.

The subprocess env is sanitized first: a snap-confined launcher can leak LD_LIBRARY_PATH and make
these tools crash with a libc symbol-lookup error — we drop those vars before running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

# name -> argv builder(out_path). Used on X11 / wlroots, or as a fallback.
_TOOLS = {
    "grim": lambda out: ["grim", out],                          # Wayland (wlroots: Sway/Hyprland)
    "gnome-screenshot": lambda out: ["gnome-screenshot", "-f", out],
    "spectacle": lambda out: ["spectacle", "-b", "-n", "-o", out],
    "maim": lambda out: ["maim", out],                          # X11
    "scrot": lambda out: ["scrot", "-o", out],                  # X11
    "import": lambda out: ["import", "-window", "root", out],   # ImageMagick, X11
}

_SNAP_ENV = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "GTK_PATH", "GIO_MODULE_DIR",
    "GSETTINGS_SCHEMA_DIR", "GDK_PIXBUF_MODULE_FILE", "LOCPATH",
)

# Minimal introspection for the portal Request object (so we can subscribe to Response before the
# object exists, avoiding a race with introspecting a not-yet-created path).
_REQUEST_XML = """<node>
  <interface name="org.freedesktop.portal.Request">
    <method name="Close"/>
    <signal name="Response">
      <arg type="u" name="response"/>
      <arg type="a{sv}" name="results"/>
    </signal>
  </interface>
</node>"""

# Hand-written so we never introspect the live portal object — GNOME exposes a property with a
# hyphen ("power-saver-enabled") that dbus-next's strict validator refuses to parse.
_SCREENSHOT_XML = """<node>
  <interface name="org.freedesktop.portal.Screenshot">
    <method name="Screenshot">
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="o" name="handle" direction="out"/>
    </method>
    <property name="version" type="u" access="read"/>
  </interface>
</node>"""


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SNAP_ENV}


def available_tool() -> str | None:
    for name in _TOOLS:
        if shutil.which(name):
            return name
    return None


def _looks_blank(path: Path) -> bool:
    """True if the PNG is a single flat colour (the tell-tale black frame from a failed grab)."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - Pillow missing; skip the check
        return False
    try:
        with Image.open(path) as im:
            extrema = im.convert("RGB").getextrema()
        return all(lo == hi for lo, hi in extrema)
    except Exception:  # noqa: BLE001
        return False


# --- XDG desktop portal (Wayland) ------------------------------------------
async def _portal_async(out_path: str) -> str | None:
    from dbus_next import BusType, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.introspection import Node

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    try:
        portal = bus.get_proxy_object(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", Node.parse(_SCREENSHOT_XML)
        )
        screenshot = portal.get_interface("org.freedesktop.portal.Screenshot")

        sender = bus.unique_name[1:].replace(".", "_")  # strip leading ':'
        token = f"jarvis_{int(time.time() * 1000)}"
        request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

        # Subscribe to the Response signal BEFORE calling, using hand-written introspection.
        req_obj = bus.get_proxy_object(
            "org.freedesktop.portal.Desktop", request_path, Node.parse(_REQUEST_XML)
        )
        req_iface = req_obj.get_interface("org.freedesktop.portal.Request")

        import asyncio

        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()

        def on_response(response: int, results: dict) -> None:
            if not fut.done():
                fut.set_result((response, results))

        req_iface.on_response(on_response)

        await screenshot.call_screenshot(
            "",
            {"handle_token": Variant("s", token), "interactive": Variant("b", False)},
        )
        response, results = await asyncio.wait_for(fut, timeout=25)
        if response != 0 or "uri" not in results:
            return None
        uri = results["uri"].value
        src = unquote(urlparse(uri).path)
        if src != out_path:
            shutil.copyfile(src, out_path)
            try:
                os.remove(src)  # portal drops it in ~/Pictures/Screenshots; don't litter
            except OSError:
                pass
        return out_path
    finally:
        bus.disconnect()


def _portal_screenshot(out_path: str) -> str | None:
    """Run the async portal flow in a dedicated thread (safe from sync or async callers)."""
    result: dict = {}

    def worker() -> None:
        try:
            import asyncio

            result["path"] = asyncio.run(_portal_async(out_path))
        except Exception as exc:  # noqa: BLE001
            result["err"] = exc

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=30)
    return result.get("path")


# --- CLI grabbers (X11 / wlroots / fallback) -------------------------------
def _tool_screenshot(out_path: str) -> str | None:
    tool = available_tool()
    if not tool:
        return None
    try:
        subprocess.run(
            _TOOLS[tool](out_path), check=True, timeout=15, capture_output=True, env=_clean_env()
        )
    except Exception:  # noqa: BLE001
        return None
    p = Path(out_path)
    if p.exists() and p.stat().st_size and not _looks_blank(p):
        return out_path
    return None


_last_geom: dict = {}  # {"real": (w, h), "img": (w, h)} from the most recent capture


def scale_note() -> str:
    """Tell Jarvis how to map coordinates it reads off the (downscaled) screenshot to real pixels."""
    real, img = _last_geom.get("real"), _last_geom.get("img")
    if not real or not img or img[0] == 0:
        return ""
    sx = real[0] / img[0]
    return (
        f"[The image is {img[0]}x{img[1]} px; the real screen is {real[0]}x{real[1]} px. To click, "
        f"multiply any pixel coordinate you read off the image by {sx:.3f} to get real screen coords.]"
    )


def _finalize(path: str | None, out_dir: str) -> str | None:
    """Downscale + JPEG-compress the grab so its base64 stays under the SDK's ~1 MB stdio buffer.
    A full-res PNG easily exceeds it and crashes the transport; a 1400px JPEG reads just as well."""
    if not path:
        return None
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0 or _looks_blank(p):
        return None
    try:
        from PIL import Image

        out = str(Path(out_dir) / f"jarvis-screen-{int(time.time())}.jpg")
        with Image.open(p) as im:
            im = im.convert("RGB")
            _last_geom["real"] = im.size
            im.thumbnail((1400, 1400))
            _last_geom["img"] = im.size
            im.save(out, "JPEG", quality=70)
        if str(p) != out:
            try:
                p.unlink()
            except OSError:
                pass
        return out
    except Exception:  # noqa: BLE001 - Pillow missing/failed: return original as a last resort
        return path


def _capture_once(out_dir: str) -> str | None:
    out = str(Path(out_dir) / f"jarvis-screen-{int(time.time() * 1000)}.png")
    # Wayland: try the portal first (compositor grabbers give black frames here).
    if os.environ.get("WAYLAND_DISPLAY"):
        p = _portal_screenshot(out)
        if not (p and Path(p).exists() and not _looks_blank(Path(p))):
            p = _tool_screenshot(out) if shutil.which("grim") else None  # wlroots fallback
        return _finalize(p, out_dir)
    # X11: CLI grabbers work fine.
    return _finalize(_tool_screenshot(out), out_dir)


def capture(out_dir: str = "/tmp") -> str | None:
    """Grab the screen, retrying — the portal can intermittently fail or hand back a blank frame."""
    for attempt in range(3):
        try:
            p = _capture_once(out_dir)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            return p
        time.sleep(0.4 * (attempt + 1))
    return None
