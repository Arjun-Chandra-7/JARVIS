"""Put a wall around the shell, instead of a list of words we hope not to see.

`permissions.py` says it plainly in its own docstring: *"It is a heuristic, not a sandbox — run
Jarvis as a low-privilege user."* That is honest, and it is also a README line rather than
something the machine enforces. This module is the enforcement.

The distinction matters because the two mechanisms answer different questions. A permission gate
answers *is the agent allowed to do this* — and it does that by matching patterns, which means it
is a denylist, which means it is a list of the bad things somebody thought of. `_DESTRUCTIVE`
catches `rm` and `dd` and `chmod -R`. It does not catch `find . -delete`, or `xargs rm`, or `cp`
over a file that mattered. It never will, because enumerating badness does not terminate.

A sandbox answers a different question: *can it*. In April 2026 Claude Code was documented
escaping its own bubblewrap sandbox, and the cause was not bubblewrap — it was a denylist policy
trying to enumerate badness inside it. So the rule here is the opposite one: name what may be
reached, and let everything else be absent.

What this does and does not buy you
-----------------------------------
`STRICT` is real containment: a read-only system, one writable directory, and no network. Nothing
else exists inside it.

`GUARDED` is the one a general-purpose shell can actually live with — your home is there, because
a shell that cannot see your files is not a shell — with credentials masked by empty tmpfs mounts.
It stops a command reading your SSH keys or posting your API tokens somewhere. It does not stop a
command deleting your work, and it is not a security boundary against something actively trying
to get out. Calling it containment would be a lie; calling it worthless would also be a lie.

If bubblewrap is not installed there is no sandbox, and this module says so rather than quietly
running the command anyway. A caller that believes it is sandboxed when it is not is worse off
than one that knows it is not.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Directories a program needs to be a program at all. Read-only, every one of them.
_SYSTEM_READONLY = (
    "/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/opt", "/var/lib",
)

# Masked with an empty tmpfs, so they exist and are empty rather than being absent in a way a
# script would notice and complain about. These are the things worth stealing.
DEFAULT_MASKS = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.kube",
    "~/.docker/config.json",
    "~/.config/gcloud",
    "~/.netrc",
    "~/.git-credentials",
    "~/.local/share/keyrings",
    "~/.mozilla",
    "~/.config/google-chrome",
    "~/.config/opera",
)


@dataclass
class Policy:
    """What a command may reach."""

    name: str
    writable: tuple[str, ...] = ()          # paths bound read-write
    readable: tuple[str, ...] = ()          # paths bound read-only, beyond the system set
    masks: tuple[str, ...] = ()             # paths replaced by an empty tmpfs
    network: bool = False
    home: bool = False                      # bind the real home read-write (GUARDED only)
    extra: tuple[str, ...] = field(default_factory=tuple)


# Nothing but a scratch directory. Use this for anything a model composed on its own.
STRICT = Policy(name="strict", network=False)

# What a general-purpose shell tool needs, minus the credentials.
GUARDED = Policy(name="guarded", home=True, masks=DEFAULT_MASKS, network=True)


def available() -> bool:
    """Whether a sandbox can actually be built here."""
    return shutil.which("bwrap") is not None


def why_unavailable() -> str:
    """A sentence for the user, when it cannot."""
    if available():
        return ""
    return ("bubblewrap is not installed, so shell commands run unsandboxed. "
            "Install it with: sudo apt install bubblewrap")


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def build_argv(command: str, policy: Policy = GUARDED,
               workdir: Optional[str] = None) -> list[str]:
    """The bwrap invocation that runs `command` under `policy`.

    Built as a list rather than a string on purpose: the command itself goes to `bash -lc` as a
    single argument, so nothing in it can be read as an argument to bwrap.
    """
    home = os.path.expanduser("~")
    work = _expand(workdir) if workdir else (home if policy.home else "/tmp")

    argv: list[str] = ["bwrap", "--die-with-parent", "--unshare-pid", "--new-session"]

    # An allowlist, not a denylist: only what is named below exists inside.
    for path in _SYSTEM_READONLY:
        if os.path.exists(path):
            argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/run", "--tmpfs", "/tmp"]

    # Found by running it: /etc/resolv.conf on this distribution is a symlink into /run, which
    # the tmpfs above replaces — so a policy with network:true got a namespace that could reach
    # the internet and could not resolve a name. Bind what the symlink actually points at.
    if policy.network:
        try:
            resolved = Path("/etc/resolv.conf").resolve()
            if resolved.exists() and not str(resolved).startswith("/etc/"):
                argv += ["--ro-bind", str(resolved), str(resolved)]
        except OSError:
            pass

    if policy.home:
        argv += ["--bind", home, home]
    else:
        # A home that exists and is empty, so tools that insist on one still start.
        argv += ["--tmpfs", home]

    for path in policy.readable:
        real = _expand(path)
        if os.path.exists(real):
            argv += ["--ro-bind", real, real]
    for path in policy.writable:
        real = _expand(path)
        if os.path.exists(real):
            argv += ["--bind", real, real]

    # Masks come last so they land on top of the binds above. Order is the whole point: a mask
    # applied before the bind that contains it would simply be overwritten.
    for path in policy.masks:
        real = _expand(path)
        if os.path.exists(real):
            argv += ["--tmpfs", real] if os.path.isdir(real) else ["--ro-bind", "/dev/null", real]

    if not policy.network:
        argv += ["--unshare-net"]

    argv += list(policy.extra)
    # `bash -c`, not `-lc`. A login shell sources the profile, and on this machine that prints
    # a GIO module warning into every single result because the snap paths it references are not
    # in the namespace. The environment is inherited from the parent anyway, PATH included.
    argv += ["--chdir", work, "--", "/bin/bash", "-c", command]
    return argv


@dataclass
class Result:
    stdout: str
    returncode: int
    sandboxed: bool
    note: str = ""


def run(command: str, policy: Policy = GUARDED, workdir: Optional[str] = None,
        timeout: float = 120.0, allow_unsandboxed: bool = True) -> Result:
    """Run a shell command, sandboxed when that is possible.

    `allow_unsandboxed` is the honest switch. Left true, a machine without bubblewrap behaves as
    it did before and the result says `sandboxed=False` so the caller can tell the user. Set
    false and the command does not run at all rather than running with less protection than was
    asked for.
    """
    if not available():
        if not allow_unsandboxed:
            return Result("", 126, False, why_unavailable())
        out = _plain(command, workdir, timeout)
        return Result(out.stdout, out.returncode, False, why_unavailable())

    argv = build_argv(command, policy, workdir)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result("", 124, True, f"timed out after {timeout:.0f}s")
    except OSError as exc:
        if not allow_unsandboxed:
            return Result("", 126, False, f"sandbox failed to start: {exc}")
        out = _plain(command, workdir, timeout)
        return Result(out.stdout, out.returncode, False, f"sandbox failed to start: {exc}")
    return Result((proc.stdout or "") + (proc.stderr or ""), proc.returncode, True)


def _plain(command: str, workdir: Optional[str], timeout: float):
    class _Out:
        stdout = ""
        returncode = 1

    try:
        return subprocess.run(command, shell=True, capture_output=True, text=True,
                              timeout=timeout, cwd=_expand(workdir) if workdir else None)
    except subprocess.TimeoutExpired:
        out = _Out()
        out.returncode = 124
        return out
    except OSError:
        return _Out()
