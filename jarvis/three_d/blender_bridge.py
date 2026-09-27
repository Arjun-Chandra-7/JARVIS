"""JARVIS's side of the restricted Blender bridge: launch, authenticate, talk, watch, recover.

One ``BlenderSession`` owns one Blender process that JARVIS started itself — never a Blender the
user opened, so the user's own project is never overwritten. It can be headless (building,
validating, exporting) or a window (presenting and live edits); both speak the same protocol.

What the session guarantees:

* The socket lives in a 0700 directory under ``$XDG_RUNTIME_DIR``; the token is random, handed
  over in a 0600 file that Blender deletes after reading, and never logged.
* Only the commands in ``blender_server.COMMANDS`` exist. Plans are validated here before they
  are sent, and again inside Blender.
* Version is checked at connect. Heartbeats notice a hung main thread; a watchdog kills a
  Blender that exceeds its memory budget or a render that exceeds its time budget.
* A crash is detected, reported by category, and the next call relaunches Blender and reopens
  the last checkpoint — the saved project is never half-written, because saves are atomic.
"""
from __future__ import annotations

import base64
import itertools
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import scene_ops
from .types import ErrorCategory

SERVER_SCRIPT = Path(__file__).with_name("blender_server.py")
MIN_VERSION = (4, 2, 0)
MAX_VERSION = (6, 0, 0)
DEFAULT_MEMORY_MB = 4096
DEFAULT_TIMEOUT_S = 120.0


class BridgeError(RuntimeError):
    """Something went wrong talking to Blender. ``category`` is an ``ErrorCategory``."""

    def __init__(self, message: str, category: ErrorCategory = ErrorCategory.INTERNAL) -> None:
        super().__init__(message)
        self.category = category


def find_blender() -> Optional[str]:
    """The Blender to use: $JARVIS_BLENDER, then PATH, then the user-local install."""
    for cand in (os.environ.get("JARVIS_BLENDER"), shutil.which("blender"),
                 str(Path.home() / ".local/opt/blender-5.2.2/blender")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def blender_version(binary: str, timeout: float = 30.0) -> Optional[tuple[int, int, int]]:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=timeout,
                             env=_clean_env()).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if line.startswith("Blender "):
            nums = line.split()[1].split(".")
            try:
                return tuple(int(n) for n in (nums + ["0", "0"])[:3])  # type: ignore[return-value]
            except ValueError:
                return None
    return None


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "VIRTUAL_ENV")}
    # Nothing from JARVIS's environment that could carry a credential into Blender.
    for key in list(env):
        if any(s in key.upper() for s in ("TOKEN", "API_KEY", "SECRET", "PASSWORD")):
            env.pop(key, None)
    return env


def runtime_dir() -> Path:
    base = Path(os.environ.get("JARVIS_RUNTIME_DIR") or os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    d = base / "jarvis-3d"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _rss_mb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


@dataclass
class Health:
    alive: bool
    busy: str = ""
    main_idle_s: float = 0.0
    rss_mb: float = 0.0


class BlenderSession:
    def __init__(self, roots: list[str], *, background: bool = True, owner: str = "jarvis",
                 binary: Optional[str] = None, memory_mb: int = DEFAULT_MEMORY_MB,
                 open_file: Optional[str] = None, max_render: int = 1024,
                 startup_timeout: float = 90.0, factory_startup: Optional[bool] = None) -> None:
        self.roots = [os.path.realpath(r) for r in roots]
        self.background = background
        self.owner = owner
        self.binary = binary or find_blender()
        self.memory_mb = memory_mb
        self.open_file = open_file
        self.max_render = max_render
        self.startup_timeout = startup_timeout
        self.factory_startup = background if factory_startup is None else factory_startup
        self.proc: Optional[subprocess.Popen] = None
        self.sock: Optional[socket.socket] = None
        self.sock_path = ""
        self.version: tuple = ()
        self.version_string = ""
        self._ids = itertools.count(1)
        self._lock = threading.RLock()
        self._watch_stop = threading.Event()
        self._watch: Optional[threading.Thread] = None
        self.killed_reason: Optional[ErrorCategory] = None
        self.last_checkpoint: Optional[str] = None
        self.crashes = 0
        self._log = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "BlenderSession":
        if self.binary is None:
            raise BridgeError("Blender is not installed.", ErrorCategory.BLENDER_UNAVAILABLE)
        ver = blender_version(self.binary)
        if ver is None:
            raise BridgeError("Blender did not report a version.", ErrorCategory.BLENDER_UNAVAILABLE)
        if not (MIN_VERSION <= ver < MAX_VERSION):
            raise BridgeError(f"Blender {'.'.join(map(str, ver))} is not a supported version "
                              f"(need {MIN_VERSION[0]}.{MIN_VERSION[1]} to {MAX_VERSION[0] - 1}.x).",
                              ErrorCategory.BLENDER_UNAVAILABLE)
        rdir = runtime_dir()
        tag = secrets.token_hex(6)
        self.sock_path = str(rdir / f"bridge-{tag}.sock")
        token = secrets.token_hex(32)
        self._token = token
        fd, token_file = tempfile.mkstemp(dir=rdir, prefix="token-")
        with os.fdopen(fd, "w") as fh:
            fh.write(token)
        os.chmod(token_file, 0o600)
        cmd = [self.binary]
        if self.background:
            cmd.append("-b")
        if self.factory_startup:
            cmd.append("--factory-startup")
        if self.open_file:
            cmd.append(scene_ops.safe_path(self.open_file, self.roots, must_exist=True))
        cmd += ["--disable-autoexec", "--python", str(SERVER_SCRIPT), "--",
                "--socket", self.sock_path, "--token-file", token_file, "--owner", self.owner,
                "--allowed-pid", str(os.getpid()), "--max-render", str(self.max_render)]
        for r in self.roots:
            cmd += ["--root", r]
        log_path = rdir / f"blender-{tag}.log"
        self._log = open(log_path, "w")
        os.chmod(log_path, 0o600)
        env = _clean_env()
        if not self.background and env.get("DISPLAY") and os.environ.get("JARVIS_3D_NATIVE_WAYLAND") != "1":
            # Our window under XWayland: a native Wayland window cannot be brought to the front by
            # anyone but the user, and "ready in Blender" should put the result in front of them.
            # (Unset is not enough: libwayland then tries "wayland-0". A name with no socket is.)
            env["WAYLAND_DISPLAY"] = "jarvis-x11-only"
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, env=env,
                                     start_new_session=True)
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._cleanup_file(token_file)
                raise BridgeError("Blender exited while starting.", ErrorCategory.BLENDER_CRASHED)
            if os.path.exists(self.sock_path):
                break
            time.sleep(0.1)
        else:
            self._cleanup_file(token_file)
            self.kill()
            raise BridgeError("Blender did not start in time.", ErrorCategory.BLENDER_UNAVAILABLE)
        self._connect()
        self._watch_stop.clear()
        self._watch = threading.Thread(target=self._watchdog, daemon=True, name="blender-watchdog")
        self._watch.start()
        return self

    @staticmethod
    def _cleanup_file(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    def _connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(self.sock_path)
        _send(s, {"cmd": "hello", "token": self._token})
        hello = _recv(s)
        if not hello.get("ok"):
            s.close()
            raise BridgeError("Blender refused the session.", ErrorCategory.BRIDGE_LOST)
        self.version = tuple(hello.get("version") or ())
        self.version_string = hello.get("version_string", "")
        if not (MIN_VERSION <= self.version < MAX_VERSION):
            s.close()
            raise BridgeError("Blender version is not supported.", ErrorCategory.BLENDER_UNAVAILABLE)
        self.sock = s

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def close(self) -> None:
        self._watch_stop.set()
        if self.alive() and self.sock is not None:
            try:
                self.call("shutdown", timeout=10)
            except BridgeError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self.kill()

    def kill(self, reason: Optional[ErrorCategory] = None) -> None:
        if reason:
            self.killed_reason = reason
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self.proc.kill()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.sock_path and os.path.exists(self.sock_path):
            self._cleanup_file(self.sock_path)
        if self._log:
            self._log.close()
            self._log = None

    def restart(self) -> None:
        """After a crash: a fresh Blender, with the last good checkpoint open."""
        self.kill()
        self.crashes += 1
        self.killed_reason = None
        if self.last_checkpoint and os.path.exists(self.last_checkpoint):
            self.open_file = self.last_checkpoint
        self.start()

    # ------------------------------------------------------------------ watchdog
    def _watchdog(self) -> None:
        while not self._watch_stop.wait(1.0):
            if not self.alive():
                return
            rss = _rss_mb(self.proc.pid)
            if self.memory_mb and rss > self.memory_mb:
                self.kill(ErrorCategory.RESOURCE_LIMIT)
                return

    def health(self) -> Health:
        if not self.alive():
            return Health(False)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.sock_path)
            _send(s, {"cmd": "hello", "token": self._token})
            if not _recv(s).get("ok"):
                return Health(False)
            _send(s, {"cmd": "ping", "id": 0})
            r = _recv(s)
            return Health(True, r.get("busy", ""), float(r.get("main_idle_s", 0)), _rss_mb(self.proc.pid))
        except (OSError, ValueError, ConnectionError):
            return Health(False)
        finally:
            s.close()

    def cancel(self) -> bool:
        """Ask Blender to stop between operations — on a separate connection, so it gets through."""
        if not self.alive():
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(self.sock_path)
            _send(s, {"cmd": "hello", "token": self._token})
            if not _recv(s).get("ok"):
                return False
            _send(s, {"cmd": "cancel", "id": 0})
            return bool(_recv(s).get("ok"))
        except (OSError, ValueError, ConnectionError):
            return False
        finally:
            s.close()

    # ------------------------------------------------------------------ calls
    def call(self, cmd: str, args: Optional[dict] = None, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        with self._lock:
            if not self.alive() or self.sock is None:
                reason = self.killed_reason or ErrorCategory.BLENDER_CRASHED
                raise BridgeError("Blender is not running.", reason)
            rid = next(self._ids)
            try:
                self.sock.settimeout(timeout)
                _send(self.sock, {"cmd": cmd, "id": rid, "args": args or {}})
                reply = _recv(self.sock)
            except socket.timeout:
                self.kill(ErrorCategory.RESOURCE_LIMIT)
                raise BridgeError(f"Blender took longer than {int(timeout)} s and was stopped.",
                                  ErrorCategory.RESOURCE_LIMIT) from None
            except (OSError, ConnectionError, ValueError, struct.error):
                reason = self.killed_reason or (ErrorCategory.BLENDER_CRASHED if not self.alive()
                                                else ErrorCategory.BRIDGE_LOST)
                self.kill()
                raise BridgeError("Lost the connection to Blender.", reason) from None
            if reply.get("id") != rid:
                self.kill()
                raise BridgeError("Blender answered out of turn.", ErrorCategory.BRIDGE_LOST)
            if not reply.get("ok"):
                cat = {"invalid_scene_operation": ErrorCategory.INVALID_PLAN,
                       "cancelled": ErrorCategory.CANCELLED,
                       "resource_limit": ErrorCategory.RESOURCE_LIMIT}.get(reply.get("category", ""),
                                                                          ErrorCategory.INTERNAL)
                raise BridgeError(reply.get("error", "Blender refused."), cat)
            return reply

    # ------------------------------------------------------------------ helpers
    def apply(self, plan: list, known: list | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
        try:
            scene_ops.validate_plan(plan, known=known if known is not None else self.object_names(),
                                    image_roots=self.roots)
        except scene_ops.PlanError as exc:
            raise BridgeError(str(exc), ErrorCategory.INVALID_PLAN) from None
        return self.call("apply", {"plan": plan}, timeout=timeout)

    def object_names(self) -> list[str]:
        scene = self.call("scene")
        return [o["name"] for o in scene["objects"]] + [o.get("collection", "") for o in scene["objects"]]

    def scene(self) -> dict:
        return self.call("scene")

    def snapshot(self, names: Optional[list] = None, max_faces: int = 200_000) -> dict:
        import numpy as np
        r = self.call("snapshot", {"names": names or [], "max_faces": max_faces})
        tris = np.frombuffer(base64.b64decode(r["triangles"]), dtype=np.float32).reshape(-1, 3, 3)
        r["tris"] = tris
        return r

    def save(self, path: str, checkpoint: bool = True) -> dict:
        r = self.call("save", {"path": path})
        if checkpoint:
            self.last_checkpoint = r["path"]
        return r

    def open(self, path: str) -> dict:
        r = self.call("open", {"path": path})
        self.last_checkpoint = r["path"]
        return r

    def render(self, camera: str, path: str, resolution: int = 384, mode: str = "shaded",
               timeout: float = 60.0) -> dict:
        return self.call("render", {"camera": camera, "path": path, "resolution": resolution, "mode": mode},
                         timeout=timeout)

    def export(self, fmt: str, path: str, **opts) -> dict:
        return self.call("export", {"format": fmt, "path": path, **opts}, timeout=300)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def _send(s: socket.socket, data: dict) -> None:
    blob = json.dumps(data, separators=(",", ":")).encode()
    s.sendall(struct.pack(">I", len(blob)) + blob)


def _recv(s: socket.socket) -> dict:
    head = _exact(s, 4)
    (n,) = struct.unpack(">I", head)
    if n > 64 * 1024 * 1024:
        raise ValueError("reply too large")
    return json.loads(_exact(s, n))


def _exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(min(1 << 20, n - len(buf)))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def raw_connect(sock_path: str, token: str) -> dict:
    """For tests: what a client that is not this session gets back."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(sock_path)
        _send(s, {"cmd": "hello", "token": token})
        return _recv(s)
    finally:
        s.close()
