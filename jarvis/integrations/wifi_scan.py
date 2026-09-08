"""Wi-Fi scanner: list nearby networks and reveal the passwords THIS laptop has saved.

Two distinct, legitimate things:

* **Nearby networks** — SSID, signal, security and channel of every access point in
  range. This is what any Wi-Fi menu shows; no root, no privilege.
* **Saved passwords** — the pre-shared keys for networks *this machine* has connected to
  and stored in NetworkManager. These are your own credentials, retrieved with
  ``nmcli -s`` for connections you already own.

The two are merged so each nearby network shows its saved password when you have one, or
"not saved on this device" when you do not.

It deliberately does NOT attempt to recover the password of a network you have never
joined. There is no supported, lawful way to read another network's key by sniffing it,
and cracking a neighbour's Wi-Fi is unauthorised access. This tool reveals only what your
own machine already knows.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Callable, Optional


def _run(args: list[str], timeout: float = 15.0) -> str:
    if not shutil.which(args[0]):
        return ""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _fields(line: str) -> list[str]:
    r"""Split one nmcli terse line on unescaped ':' and unescape '\:' and '\\'."""
    out, buf, esc = [], [], False
    for ch in line:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def parse_networks(raw: str) -> list[dict]:
    """Parse `nmcli -t -f SSID,SIGNAL,SECURITY,CHAN dev wifi list`, best signal per SSID."""
    best: dict[str, dict] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = _fields(line)
        if len(parts) < 4:
            continue
        ssid, signal, security, chan = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue                      # hidden network, no usable name
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        prev = best.get(ssid)
        if prev is None or sig > prev["signal"]:
            best[ssid] = {"ssid": ssid, "signal": sig,
                          "security": security or "open", "channel": chan}
    return sorted(best.values(), key=lambda n: -n["signal"])


def parse_saved_names(raw: str) -> list[str]:
    """Parse `nmcli -t -f NAME,TYPE connection show` for wireless connection names."""
    names = []
    for line in raw.splitlines():
        parts = _fields(line)
        if len(parts) >= 2 and "wireless" in parts[1]:
            names.append(parts[0])
    return names


def nearby_networks(runner: Callable[[list[str]], str] = _run) -> list[dict]:
    runner(["nmcli", "dev", "wifi", "rescan"])   # best effort; ignore result
    return parse_networks(runner(
        ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,CHAN", "dev", "wifi", "list"]))


def saved_passwords(runner: Callable[[list[str]], str] = _run) -> dict[str, str]:
    """{ssid: pre-shared key} for the wireless networks this machine has stored."""
    out: dict[str, str] = {}
    for name in parse_saved_names(runner(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])):
        psk = runner(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                      "connection", "show", name]).strip()
        ssid = runner(["nmcli", "-s", "-g", "802-11-wireless.ssid",
                       "connection", "show", name]).strip() or name
        if psk:
            out[ssid] = psk
    return out


def scan(runner: Callable[[list[str]], str] = _run) -> list[dict]:
    """Nearby networks, each annotated with the saved password when this machine has one."""
    saved = saved_passwords(runner)
    networks = nearby_networks(runner)
    seen = {n["ssid"] for n in networks}
    for net in networks:
        pwd = saved.get(net["ssid"])
        net["saved"] = pwd is not None
        net["password"] = pwd
    # Saved networks not currently in range are still useful to show.
    for ssid, pwd in saved.items():
        if ssid not in seen:
            networks.append({"ssid": ssid, "signal": 0, "security": "?", "channel": "?",
                             "saved": True, "password": pwd, "out_of_range": True})
    return networks


def report(runner: Callable[[list[str]], str] = _run) -> str:
    """A readable block: every network with its saved password, or a clear note when not saved."""
    networks = scan(runner)
    if not networks:
        return ("No Wi-Fi networks found. Is Wi-Fi on? (nmcli radio wifi on)  "
                "This lists nearby networks and shows passwords only for networks this "
                "device has already joined.")
    lines = []
    for n in networks:
        bars = "" if n.get("out_of_range") else f"  {n['signal']}%  {n['security']}"
        if n["password"]:
            lines.append(f"{n['ssid']}{bars}\n    password: {n['password']}"
                         + ("   (saved; not currently in range)" if n.get("out_of_range") else "   (saved on this device)"))
        else:
            lines.append(f"{n['ssid']}{bars}\n    password: — not saved on this device")
    saved_n = sum(1 for n in networks if n["password"])
    header = (f"{len(networks)} network(s); {saved_n} with a password this device has saved. "
              "Passwords for networks you have never joined are not recoverable.")
    return header + "\n\n" + "\n".join(lines)
