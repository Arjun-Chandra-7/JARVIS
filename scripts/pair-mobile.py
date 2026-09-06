#!/usr/bin/env python3
"""Create a private phone token. Existing pairing is preserved unless --rotate is given."""
import argparse
from pathlib import Path
import secrets
import os

parser = argparse.ArgumentParser()
parser.add_argument("--rotate", action="store_true")
args = parser.parse_args()
path = Path.home() / ".config/jarvis/mobile-token"
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if args.rotate or not path.exists():
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_urlsafe(32) + "\n")
    path.chmod(0o600)
print(f"Phone token saved privately to {path}. Copy its contents into the app's Token field.")
print("Recommended remote transport: tailscale serve --bg http://127.0.0.1:8770")
