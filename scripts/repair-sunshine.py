#!/usr/bin/env python3
"""Repair this user's duplicate Sunshine launchers, preserving a rollback copy."""
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime

APP = "dev.lizardbyte.app.Sunshine"
config = Path.home() / ".config"
backup = config / "jarvis" / ("sunshine-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
backup.mkdir(parents=True, mode=0o700)
unit = config / "systemd/user/sunshine.service"
autostart = config / f"autostart/{APP}.desktop"
for path in (unit, autostart):
    if path.exists():
        shutil.copy2(path, backup / path.name)

def run(*args, check=True):
    return subprocess.run(args, check=check, timeout=45)

# KillMode=process leaves flatpak scopes alive after restart. Stop ALL instances of this
# exact app before giving the service exclusive ownership of its RTSP/HTTPS ports.
run("systemctl", "--user", "stop", "sunshine.service", check=False)
run("flatpak", "kill", APP, check=False)
unit.parent.mkdir(parents=True, exist_ok=True)
unit.write_text("""[Unit]
Description=Sunshine remote desktop (single instance)
After=graphical-session.target xdg-desktop-portal.service pipewire.service
Wants=xdg-desktop-portal.service
PartOf=graphical-session.target
StartLimitIntervalSec=120
StartLimitBurst=10

[Service]
Type=exec
PassEnvironment=DISPLAY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP XDG_SESSION_TYPE XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS XAUTHORITY
ExecStart=/usr/bin/flatpak run dev.lizardbyte.app.Sunshine
ExecStop=-/usr/bin/flatpak kill dev.lizardbyte.app.Sunshine
KillMode=control-group
Restart=on-failure
RestartSec=8
TimeoutStopSec=15

[Install]
WantedBy=graphical-session.target
""")
autostart.parent.mkdir(parents=True, exist_ok=True)
autostart.write_text("[Desktop Entry]\nType=Application\nName=Sunshine (managed by systemd)\nHidden=true\n")
run("systemctl", "--user", "import-environment", *[name for name in (
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY") if name in os.environ])
run("systemctl", "--user", "daemon-reload")
run("systemctl", "--user", "reenable", "sunshine.service")
run("systemctl", "--user", "start", "sunshine.service")
print(f"Sunshine now has one managed launcher. Previous files: {backup}")
print("If GNOME requests screen sharing, select the display once. Check sunshine.log for capture readiness.")
