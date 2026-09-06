#!/usr/bin/env python3
"""Create a private phone token. Existing pairing is preserved unless --rotate is given."""
import argparse
from pathlib import Path
import secrets
import os

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--rotate", action="store_true", help="replace an existing token")
parser.add_argument("--show", action="store_true", help="print the current token and exit")
args = parser.parse_args()
path = Path.home() / ".config/jarvis/mobile-token"
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

if args.show:
    print(path.read_text().strip() if path.exists() else "(no token yet — run without --show)")
    raise SystemExit(0)

if args.rotate or not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")
    path.chmod(0o600)
    print("New phone token generated.")
else:
    print("Existing phone token kept (use --rotate to replace it).")

print(f"Token file: {path}  (mode 600). Copy its contents into the app's Token field.")
print("On the phone, set the server URL to your tailnet name, e.g. https://bhramastra.<tailnet>.ts.net")
print("Expose it once on this machine:  tailscale serve --bg 8770")
