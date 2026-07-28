"""Situational awareness: real nearby devices for the HUD radar.

Everything here is *live* — WiFi access points (with signal), devices on the local
network (ARP/neighbour table), and Bluetooth devices seen nearby. Scans are bounded
and cached, refreshed on a background thread so the HTTP endpoint stays instant.
"""

from __future__ import annotations

import subprocess
import threading
import time

_cache: dict = {"wifi": [], "lan": [], "bt": [], "t": 0.0}
_lock = threading.Lock()
_refreshing = False
_TTL = 12.0


def _run(cmd: list[str], timeout: float) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ""


def _wifi() -> list[dict]:
    out = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi"], 8)
    best: dict[str, dict] = {}
    for line in out.splitlines():
        # nmcli escapes ':' inside fields as '\:'; split on unescaped colons
        parts = _split_nmcli(line)
        if len(parts) < 4:
            continue
        ssid, sig, sec, inuse = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue
        try:
            signal = int(sig)
        except ValueError:
            continue
        cur = best.get(ssid)
        if cur is None or signal > cur["signal"]:
            best[ssid] = {"ssid": ssid, "signal": signal,
                          "secure": bool(sec and sec != "--"),
                          "current": inuse.strip() == "*"}
    return sorted(best.values(), key=lambda d: -d["signal"])[:14]


def _split_nmcli(line: str) -> list[str]:
    fields, buf, esc = [], "", False
    for ch in line:
        if esc:
            buf += ch; esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            fields.append(buf); buf = ""
        else:
            buf += ch
    fields.append(buf)
    return fields


def _lan() -> list[dict]:
    out = _run(["ip", "neigh"], 4)
    devs = []
    for line in out.splitlines():
        p = line.split()
        if len(p) < 1 or ":" in p[0] and "lladdr" not in line:
            pass
        if "lladdr" not in line:
            continue
        ip = p[0]
        try:
            mac = p[p.index("lladdr") + 1]
        except (ValueError, IndexError):
            mac = ""
        state = p[-1]
        if state in ("FAILED", "INCOMPLETE"):
            continue
        devs.append({"ip": ip, "mac": mac, "state": state,
                     "router": "router" in line})
    # de-dupe by mac, prefer IPv4
    seen, out2 = set(), []
    for d in sorted(devs, key=lambda d: ":" in d["ip"]):
        if d["mac"] in seen:
            continue
        seen.add(d["mac"]); out2.append(d)
    return out2[:20]


def _bt() -> list[dict]:
    # kick a short discovery so RSSI/new devices show up, then read the list
    _run(["bluetoothctl", "--timeout", "5", "scan", "on"], 7)
    out = _run(["bluetoothctl", "devices"], 4)
    devs = []
    for line in out.splitlines():
        p = line.split(" ", 2)
        if len(p) < 3 or p[0] != "Device":
            continue
        mac, name = p[1], p[2]
        rssi = None
        info = _run(["bluetoothctl", "info", mac], 3)
        connected = "Connected: yes" in info
        for il in info.splitlines():
            il = il.strip()
            if il.startswith("RSSI:"):
                try:
                    rssi = int(il.split(":")[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
        # phones/audio are the interesting ones; keep everything but flag type
        icon = "phone" if any(k in name.lower() for k in ("phone", "pixel", "iphone", "galaxy", "nothing", "oneplus")) else "device"
        devs.append({"mac": mac, "name": name, "rssi": rssi, "connected": connected, "icon": icon})
    # connected + strong signal first
    devs.sort(key=lambda d: (not d["connected"], -(d["rssi"] or -200)))
    return devs[:16]


def _do_refresh() -> None:
    global _refreshing
    try:
        wifi, lan, bt = _wifi(), _lan(), _bt()
        with _lock:
            _cache.update(wifi=wifi, lan=lan, bt=bt, t=time.time())
    finally:
        _refreshing = False


def snapshot() -> dict:
    """Instant: returns cached data and refreshes in the background when stale."""
    global _refreshing
    now = time.time()
    with _lock:
        stale = now - _cache["t"] > _TTL
        data = {"wifi": _cache["wifi"], "lan": _cache["lan"], "bt": _cache["bt"],
                "age": round(now - _cache["t"], 1) if _cache["t"] else None}
    if stale and not _refreshing:
        _refreshing = True
        threading.Thread(target=_do_refresh, daemon=True).start()
    data["counts"] = {"wifi": len(data["wifi"]), "lan": len(data["lan"]), "bt": len(data["bt"])}
    return data
