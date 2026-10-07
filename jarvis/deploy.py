"""What is deployed, what could be, and moving between them without losing anything.

The running checkout can hold three kinds of commit: the published release
(``origin/live-failure-repair``), a verified local repair activated by self-repair, and
work nobody activated. ``jarvis restart`` used to fast-forward onto the release and, when a
local repair made that impossible, only said "Not updated". This module keeps a record of what
is actually deployed — separately from the remote-tracking ref — and decides:

    release          HEAD is the release                       → nothing to do
    update           the release is ahead, HEAD is an ancestor → fast-forward
    local-repair     HEAD is ahead with activated repairs only → keep it; never overwritten
    local-commits    HEAD is ahead with commits nobody activated → keep, say so
    diverged         both moved                                → stop, explain, offer actions
    dirty            tracked files edited                      → touch nothing
    pinned           a rollback is in force                    → stay until `jarvis update`

Nothing here pushes, rewrites published history, or deletes a branch. Every move that leaves a
commit behind first puts a backup branch on it. After a deploy the services are health-checked
against the commit they report loading; a deploy that does not come up healthy is rolled back.

    python -m jarvis.deploy status [--json] [--fetch]
    python -m jarvis.deploy pre-restart          (bin/jarvis restart)
    python -m jarvis.deploy verify [--rollback-on-failure]
    python -m jarvis.deploy rollback | update | keep | export | integrate | discard
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO = Path(os.environ.get("JARVIS_DEPLOY_REPO") or Path(__file__).resolve().parent.parent)
REMOTE_REF = os.environ.get("JARVIS_UPDATE_FROM", "origin/live-failure-repair")


def _state_path() -> Path:
    base = Path(os.environ.get("JARVIS_STATE_DIR", "~/.local/share/jarvis")).expanduser()
    return base / "deploy.json"


def git(repo: Path, *args: str, check: bool = False, timeout: float = 60) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    out = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=timeout, env=env)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {out.stderr.strip()[:200]}")
    return out


def _sha(repo: Path, ref: str) -> str:
    out = git(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    return out.stdout.strip() if out.returncode == 0 else ""


# ------------------------------------------------------------------------------ record
def load_record() -> dict:
    try:
        data = json.loads(_state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_record(data: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data["history"] = list(data.get("history", []))[-20:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def record_deploy(to: str, previous: str, kind: str, reason: str, *, pinned: Optional[bool] = None) -> dict:
    data = load_record()
    data.update({"active": to, "previous": previous, "kind": kind, "at": time.time()})
    if pinned is not None:
        data["pinned"] = pinned
    data.setdefault("history", []).append({"ts": round(time.time()), "from": previous[:12], "to": to[:12],
                                           "kind": kind, "reason": reason[:80]})
    if kind == "local-repair":
        data.setdefault("repairs", [])
        if to not in data["repairs"]:
            data["repairs"].append(to)
    save_record(data)
    return data


# ------------------------------------------------------------------------------ classification
@dataclass
class State:
    kind: str
    head: str = ""
    branch: str = ""
    remote: str = ""
    ahead: list[str] = field(default_factory=list)        # commits in HEAD, not in the release
    behind: int = 0
    repairs: list[str] = field(default_factory=list)      # of `ahead`, the activated repairs
    dirty: bool = False
    pinned: bool = False
    active: str = ""
    previous: str = ""
    message: str = ""
    actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _activated_repairs() -> set[str]:
    shas = set(load_record().get("repairs", []))
    try:
        from .selfrepair.jobs import JobStore

        for job in JobStore().all():
            if job.state == "completed" and job.candidate_commit:
                shas.add(job.candidate_commit)
    except Exception:  # noqa: BLE001 — the record alone is enough to decide
        pass
    return shas


def classify(repo: Path = REPO, remote_ref: str = REMOTE_REF, *, fetch: bool = False) -> State:
    if fetch and "/" in remote_ref:
        remote_name, _, branch = remote_ref.partition("/")
        git(repo, "fetch", "--quiet", remote_name, branch, timeout=60)
    head = _sha(repo, "HEAD")
    remote = _sha(repo, remote_ref)
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    record = load_record()
    s = State("release", head=head, branch=branch, remote=remote, pinned=bool(record.get("pinned")),
              active=record.get("active", ""), previous=record.get("previous", ""))
    s.dirty = bool(git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip())
    if not remote:
        s.kind, s.message = "no-remote", f"{remote_ref} is not known here; running {head[:12]} as it is."
        return s
    s.ahead = [c for c in git(repo, "rev-list", f"{remote}..{head}").stdout.split() if c]
    s.behind = len([c for c in git(repo, "rev-list", f"{head}..{remote}").stdout.split() if c])
    activated = _activated_repairs()
    s.repairs = [c for c in s.ahead if c in activated]
    local_only = [c for c in s.ahead if c not in activated and not _is_revert_of_repair(repo, c, activated)]
    if s.dirty:
        s.kind = "dirty"
        s.message = "Tracked files have local edits that were never activated; nothing is updated until they are committed or removed."
    elif s.pinned:
        s.kind = "pinned"
        s.message = f"Pinned to {head[:12]} by a rollback. `jarvis update` takes the release again."
    elif s.ahead and s.behind:
        s.kind = "diverged"
        what = f"{len(s.repairs)} activated repair(s)" if s.repairs and not local_only else f"{len(s.ahead)} local commit(s)"
        s.message = (f"The release has {s.behind} new commit(s) and this checkout has {what} the release lacks. "
                     "Nothing was changed. Choose: keep (stay as is), integrate (merge the release in, stopping on "
                     "any conflict), export (save the local commits as patches), or discard (back to the release; "
                     "the local commits stay on a backup branch).")
        s.actions = ["keep", "integrate", "export", "discard"]
    elif s.ahead:
        s.kind = "local-repair" if not local_only else "local-commits"
        s.message = (f"Running {len(s.repairs)} verified local repair(s) on top of the release; kept as they are."
                     if s.kind == "local-repair" else
                     f"Running {len(s.ahead)} local commit(s) that no repair activated; kept as they are.")
        s.actions = ["keep", "export", "discard"]
    elif s.behind:
        s.kind, s.message = "update", f"The release is {s.behind} commit(s) ahead; it can be fast-forwarded."
    else:
        s.message = f"Running the release, {head[:12]}."
    return s


def waiting(repo: Path = REPO, remote_ref: str = REMOTE_REF) -> Optional[str]:
    """One line for a service starting behind the release, else None. Never fetches or moves.

    A reboot starts the services on whatever is checked out; only `jarvis restart` takes the
    release. Without this the newer code just sat there and nothing said so.
    """
    try:
        s = classify(repo, remote_ref)
    except Exception:  # noqa: BLE001 — a service must start whatever git says
        return None
    if s.kind != "update":
        return None
    return f"running {s.head[:7]}; the release is {s.behind} commit(s) newer — `jarvis restart` takes it"


def _is_revert_of_repair(repo: Path, sha: str, repairs: set[str]) -> bool:
    body = git(repo, "log", "-1", "--format=%B", sha).stdout
    return any(f"This reverts commit {r}" in body for r in repairs)


# ------------------------------------------------------------------------------ moves
def pre_restart(repo: Path = REPO, remote_ref: str = REMOTE_REF, *, fetch: bool = True,
                say: Callable[[str], None] = print) -> State:
    """What `jarvis restart` does before restarting: update only when that loses nothing."""
    if remote_ref == "none":
        return State("disabled", head=_sha(repo, "HEAD"), message="Updates are off (JARVIS_UPDATE_FROM=none).")
    s = classify(repo, remote_ref, fetch=fetch)
    if s.kind == "update":
        before = s.head
        merged = git(repo, "merge", "--ff-only", "--quiet", s.remote)
        if merged.returncode == 0:
            record_deploy(s.remote, before, "release", f"fast-forward to {remote_ref}")
            log = git(repo, "log", "--oneline", f"{before}..{s.remote}").stdout.strip().splitlines()
            say(f"Updated {before[:7]} → {s.remote[:7]}:")
            for line in log[:12]:
                say(f"  {line}")
            return classify(repo, remote_ref)
        say("Not updated: the fast-forward failed; running what was there.")
        return s
    if s.kind == "release" and s.active != s.head:
        record_deploy(s.head, s.active or s.head, "release", "running the release")
    say(s.message)
    return s


def rollback(repo: Path = REPO, target: Optional[str] = None) -> State:
    """Back to the previously deployed commit (or `target`), keeping a backup branch, pinned."""
    s = classify(repo)
    if s.dirty:
        raise RuntimeError("tracked files have local edits; roll back refused so nothing is lost")
    target = target or s.previous
    if not target or not _sha(repo, target):
        raise RuntimeError("there is no recorded previous deployment to return to")
    target = _sha(repo, target)
    if target == s.head:
        raise RuntimeError("the previous deployment is what is running already")
    backup = f"jarvis/rollback-from/{s.head[:12]}"
    if not _sha(repo, backup):
        git(repo, "branch", backup, s.head, check=True)
    git(repo, "reset", "--keep", target, check=True)   # --keep refuses to lose any local edit
    record_deploy(target, s.head, "rollback", f"rolled back; backup branch {backup}", pinned=True)
    return classify(repo)


def update(repo: Path = REPO, remote_ref: str = REMOTE_REF, *, fetch: bool = True,
           say: Callable[[str], None] = print) -> State:
    """Lift a rollback pin and take the release again (only by fast-forward)."""
    data = load_record()
    if data.get("pinned"):
        data["pinned"] = False
        save_record(data)
    s = classify(repo, remote_ref, fetch=fetch)
    if s.kind == "update":
        return pre_restart(repo, remote_ref, fetch=False, say=say)
    say(s.message)
    return s


def keep() -> str:
    data = load_record()
    data["kept"] = time.time()
    save_record(data)
    return "Keeping the local commits as they are. Restarts will not touch them."


def export(repo: Path = REPO, remote_ref: str = REMOTE_REF, out_dir: Optional[Path] = None) -> list[str]:
    s = classify(repo, remote_ref)
    if not s.ahead:
        return []
    out = out_dir or Path(os.path.expanduser("~/jarvis-patches")) / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    res = git(repo, "format-patch", "--quiet", "-o", str(out), f"{s.remote}..{s.head}", check=True)
    return sorted(str(p) for p in out.glob("*.patch")) or res.stdout.split()


def integrate(repo: Path = REPO, remote_ref: str = REMOTE_REF, *, fetch: bool = True) -> str:
    """Merge the release into the local commits. A conflict is aborted and reported."""
    s = classify(repo, remote_ref, fetch=fetch)
    if s.kind != "diverged":
        return f"Nothing to integrate: {s.message}"
    merged = git(repo, "merge", "--no-edit", "--no-ff", s.remote)
    if merged.returncode != 0:
        conflicted = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        git(repo, "merge", "--abort")
        return ("The release conflicts with the local commits in " + ", ".join(conflicted[:6]) +
                ". I stopped and left everything as it was.")
    head = _sha(repo, "HEAD")
    record_deploy(head, s.head, "local-repair" if s.repairs else "local", "integrated the release")
    return f"Merged the release into the local commits ({head[:12]}). Restart to run it."


def discard(repo: Path = REPO, remote_ref: str = REMOTE_REF, *, fetch: bool = True) -> str:
    """Back to the release; the local commits stay reachable on a backup branch."""
    s = classify(repo, remote_ref, fetch=fetch)
    if s.dirty:
        return "Tracked files have local edits; discard refused so nothing is lost."
    if not s.ahead:
        return "There are no local commits to discard."
    backup = f"jarvis/discarded/{s.head[:12]}"
    if not _sha(repo, backup):
        git(repo, "branch", backup, s.head, check=True)
    git(repo, "reset", "--keep", s.remote, check=True)
    record_deploy(s.remote, s.head, "release", f"discarded local commits; backup {backup}", pinned=False)
    return f"Back on the release {s.remote[:12]}. The local commits are kept on {backup}."


# ------------------------------------------------------------------------------ verification
def loaded_commits(port: int = int(os.environ.get("JARVIS_WEB_PORT", "8770"))) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {"backend": None, "voice": None, "whatsapp": None, "overlay": None}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            data = json.loads(r.read())
        out["backend"] = (data.get("build") or {}).get("commit")
        out["voice"] = (data.get("voice_build") or {}).get("commit")
        out["overlay"] = (data.get("overlay_build") or {}).get("commit")
    except (OSError, ValueError):
        pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{os.environ.get('WA_PORT', '8765')}/status", timeout=3) as r:
            out["whatsapp"] = json.loads(r.read()).get("commit")
    except (OSError, ValueError):
        pass
    return out


UNITS = {"backend": "jarvis-backend", "voice": "jarvis-voice", "whatsapp": "jarvis-whatsapp"}


def enabled_services() -> tuple[str, ...]:
    """Services this machine is set up to run: a unit that is not installed is not required.
    (Installed, not "enabled": here the backend and voice units are started by the autostart
    timer and `jarvis start`, and report "disabled".)"""
    found = []
    for name, unit in UNITS.items():
        try:
            out = subprocess.run(["systemctl", "--user", "show", "-p", "LoadState", "--value", f"{unit}.service"],
                                 capture_output=True, text=True, timeout=10)
            if out.stdout.strip() == "loaded":
                found.append(name)
        except (OSError, subprocess.SubprocessError):
            pass
    return tuple(found) or ("backend",)


def verify(repo: Path = REPO, wait_s: float = 90, *, loaded: Callable[[], dict] = loaded_commits,
           required: Optional[tuple[str, ...]] = None) -> tuple[bool, dict]:
    """Every service this machine runs reports loading HEAD within `wait_s`."""
    required = required or enabled_services()
    want = _sha(repo, "HEAD")[:12]
    deadline = time.monotonic() + wait_s
    seen: dict = {}
    while time.monotonic() < deadline:
        seen = loaded()
        if all((seen.get(k) or "")[:12] == want for k in required):
            return True, seen
        time.sleep(2)
    return False, seen


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis deploy")
    p.add_argument("action", choices=["status", "pre-restart", "verify", "rollback", "update", "keep", "export",
                                      "integrate", "discard"])
    p.add_argument("--json", action="store_true")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--wait", type=float, default=float(os.environ.get("JARVIS_DEPLOY_WAIT", "90")))
    p.add_argument("--to", default=None, help="rollback: the commit to return to")
    a = p.parse_args(argv)
    try:
        if a.action == "status":
            s = classify(fetch=a.fetch)
            if a.json:
                print(json.dumps({**s.as_dict(), "loaded": loaded_commits()}, indent=1))
            else:
                print(f"{s.kind}: {s.message}")
                for name, commit in loaded_commits().items():
                    print(f"  {name:9} running {commit or 'unknown'}")
                if s.actions:
                    print("  actions: " + ", ".join(f"jarvis {x}" for x in s.actions))
            return 0
        if a.action == "pre-restart":
            pre_restart()
            return 0
        if a.action == "verify":
            ok, seen = verify(wait_s=a.wait)
            head = _sha(REPO, "HEAD")[:12]
            print(("✅ all services run " if ok else "❌ not every service runs ") + head +
                  "".join(f"\n  {k:9} {v or 'not answering'}" for k, v in seen.items()))
            return 0 if ok else 1
        if a.action == "rollback":
            s = rollback(target=a.to)
            print(f"Rolled back to {s.head[:12]} (pinned). Restart to run it; `jarvis update` to leave the pin.")
            return 0
        if a.action == "update":
            update()
            return 0
        if a.action == "keep":
            print(keep())
            return 0
        if a.action == "export":
            files = export()
            print("\n".join(files) if files else "No local commits to export.")
            return 0
        if a.action == "integrate":
            print(integrate())
            return 0
        if a.action == "discard":
            print(discard())
            return 0
    except RuntimeError as exc:
        print(f"Refused: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(_main())
