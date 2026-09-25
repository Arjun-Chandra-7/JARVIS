"""What a repair may touch, run and activate — the rules, in one file, read from the live checkout.

Everything a repair job is allowed to do is decided here and nowhere else:

* the emergency switch (``JARVIS_SELF_REPAIR_DISABLED=1``) that stops every code repair cold;
* the components a report can be mapped to, each with the files it may edit, the tests that
  cover it, the services that load it, and whether it is protected;
* the protected paths no repair may auto-activate, whatever component it came from;
* the commands a job may run, as exact templates, and the git subcommands it may use;
* the limits: time, memory, output, diff size.

This module is itself protected (``jarvis/selfrepair/**``), and it is always imported from the
running checkout, never from a repair's worktree. A job records the digest of this file when it
starts and refuses to continue if the file changes underneath it, so no job can loosen these
rules and then benefit from the looser rules in the same run.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

REPO = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- emergency switch
def disabled() -> bool:
    """JARVIS_SELF_REPAIR_DISABLED=1: preferences still work; no repair edits, tests or activates."""
    return os.environ.get("JARVIS_SELF_REPAIR_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def digest() -> str:
    """Fingerprint of the rules as loaded. A job refuses to go on if this changes mid-run."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


# ----------------------------------------------------------------------------- limits
@dataclass(frozen=True)
class Limits:
    edit_timeout_s: int = int(os.environ.get("JARVIS_REPAIR_EDIT_TIMEOUT", "900"))
    test_timeout_s: int = int(os.environ.get("JARVIS_REPAIR_TEST_TIMEOUT", "900"))
    focused_timeout_s: int = 180
    cpu_s: int = 1800                      # RLIMIT_CPU per test process
    memory_bytes: int = 6 * 1024 ** 3      # RLIMIT_AS per test process
    file_bytes: int = 200 * 1024 ** 2      # RLIMIT_FSIZE: no multi-GB log files
    output_bytes: int = 64 * 1024          # what is kept of a command's output, for parsing only
    tasks_max: int = 256                   # the worker unit's TasksMax (every subprocess counts)
    worker_memory: str = "8G"              # the worker unit's MemoryMax
    worker_runtime_s: int = 3600           # the worker unit's RuntimeMaxSec
    max_diff_lines: int = int(os.environ.get("JARVIS_REPAIR_MAX_DIFF_LINES", "150"))
    max_changed_files: int = 6
    job_ttl_s: int = 24 * 3600             # an unfinished job this old is expired
    approval_ttl_s: int = 600              # a pending activation approval lapses after this


LIMITS = Limits()


# ----------------------------------------------------------------------------- protected paths
# A repair that touches any of these never activates on its own. Diagnosis and a reviewed patch
# are fine; activation needs a specific approval naming the area.
PROTECTED: dict[str, tuple[str, ...]] = {
    "approval and confirmation enforcement": (
        "jarvis/approvals.py", "jarvis/agent/permissions.py", "jarvis/agent/gate.py",
        "jarvis/agent/action_claims.py", "tests/test_approvals.py", "tests/test_gate.py"),
    "authentication and authorization": (
        "jarvis/mobile.py", "jarvis/trust.py", "tests/test_mobile_auth.py"),
    "secret handling and configuration": (
        "jarvis/config.py", ".env*", "**/.env*", "*.pem", "*.key", "jarvis/integrations/google/auth*"),
    "shell sandboxing": ("jarvis/agent/sandbox.py", "tests/test_sandbox.py"),
    "self-repair policy and git restrictions": (
        "jarvis/selfrepair/**", "tests/test_selfrepair*.py", "jarvis/build_info.py"),
    "messaging, email, calendar and payment side effects": (
        "jarvis/message_command.py", "jarvis/integrations/whatsapp*", "jarvis/integrations/google/**",
        "jarvis/integrations/phone/**", "jarvis/bridges/**", "whatsapp/**", "jarvis/agent/groq_tools.py",
        "tests/test_contact_messaging.py", "tests/test_live_whatsapp_flow.py"),
    "contact resolution": (
        "jarvis/integrations/contacts.py", "jarvis/memory/contacts_index.py", "tests/test_contacts_index.py"),
    "away-mode disclosure and safety": ("jarvis/away_mode/**", "tests/test_away_mode.py"),
    "password and OTP protection": ("jarvis/**/otp*", "jarvis/**/password*"),
    "service units and lifecycle": (
        "*.service", "**/*.service", "scripts/**", "bin/**", "jarvis.sh", "start_jarvis.sh", "jarvis.desktop"),
    "network exposure, Host/Origin checks and CORS": ("jarvis/webserver.py", "tests/test_security_boundaries.py"),
    "storage and migrations": ("jarvis/memory/vault.py", "jarvis/preferences.py", "**/migrations/**"),
    "update and package-install mechanisms": (
        "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg", "**/package.json",
        "**/package-lock.json", "jarvis.spec", ".github/**"),
    "settings and trust boundaries": ("jarvis/settings/**",),
}

DEPENDENCY_FILES = ("requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg",
                    "**/package.json", "**/package-lock.json")


def _matches(path: str, pattern: str) -> bool:
    p = PurePosixPath(path)
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(p.name, pattern[3:]):
        return True
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return False


def protected_area(path: str) -> Optional[str]:
    """The protected area `path` belongs to, or None."""
    path = str(PurePosixPath(path))
    for area, patterns in PROTECTED.items():
        if any(_matches(path, pat) for pat in patterns):
            return area
    return None


def is_dependency_file(path: str) -> bool:
    return any(_matches(str(PurePosixPath(path)), pat) for pat in DEPENDENCY_FILES)


# ----------------------------------------------------------------------------- components
@dataclass(frozen=True)
class Component:
    name: str
    keywords: tuple[str, ...]              # regex fragments matched against the report
    files: tuple[str, ...]                 # what a repair may edit (globs, repo-relative)
    tests: tuple[str, ...]                 # the focused tests that cover it
    services: tuple[str, ...] = ()         # systemd user units that load this code
    protected: str = ""                    # non-empty: the protected area it lives in
    description: str = ""


COMPONENTS: tuple[Component, ...] = (
    Component("speech recognition",
              (r"transcri\w*", r"speech recognition", r"\bstt\b", r"whisper", r"mishear\w*", r"misheard",
               r"(?:doesn'?t|didn'?t|can'?t|not) hear", r"cublas", r"gpu (?:memory|error)",
               r"lost (?:what i said|my words)"),
              ("jarvis/audio/local_stt.py",),
              ("tests/test_local_stt*.py", "tests/test_voice_pipeline_integration.py",
               "tests/test_hearing.py", "tests/test_repair_*.py"),
              services=("jarvis-voice",),
              description="turning recorded speech into text"),
    Component("spoken text formatting",
              (r"pronounc\w*", r"reads? out", r"says? the (?:symbols|markdown)", r"speech text"),
              ("jarvis/audio/speech_text.py",),
              ("tests/test_speech_text.py", "tests/test_lens_and_speech.py", "tests/test_repair_*.py"),
              services=("jarvis-voice",),
              description="what text is turned into before it is spoken"),
    Component("overlay layout",
              (r"overlay", r"\bhud\b", r"\bpill\b", r"layout", r"window (?:size|position)", r"button"),
              ("overlay/app.css", "overlay/app.js", "overlay/index.html", "overlay/tokens.css",
               "overlay/settings.js"),
              ("tests/test_overlay_appearance.py", "tests/test_repair_*.py"),
              description="the desktop overlay's look and layout"),
    Component("teaching overlay",
              (r"teach\w*", r"lesson", r"whiteboard", r"diagram", r"drawing"),
              ("jarvis/teach/*.py", "jarvis/teach/lessons/*.py", "overlay/teach/*.js", "overlay/teach/teach.css"),
              ("tests/test_teach_*.py", "tests/test_repair_*.py"),
              services=("jarvis-backend", "jarvis-voice"),
              description="lessons drawn on the screen"),
    Component("dictation",
              (r"dictat\w*", r"voice typing", r"type (?:what i say|for me)"),
              ("jarvis/flow/*.py",),
              ("tests/test_flow_dictation.py", "tests/test_dictation*.py", "tests/test_repair_*.py"),
              services=("jarvis-voice",),
              description="typing what is said into the focused field"),
    Component("hinglish understanding",
              (r"hinglish", r"hindi", r"understand (?:my )?hindi"),
              ("jarvis/hinglish.py", "jarvis/misheard.py"),
              ("tests/test_hinglish.py", "tests/test_misheard.py", "tests/test_repair_*.py"),
              services=("jarvis-backend",),
              description="reading Hindi and Hinglish requests"),
    Component("youtube and video",
              (r"youtube", r"\bvideo\b"),
              ("jarvis/screen/youtube.py", "jarvis/video_command.py", "jarvis/youtube_command.py"),
              ("tests/test_screen_youtube.py", "tests/test_youtube_command.py", "tests/test_repair_*.py"),
              services=("jarvis-backend",),
              description="finding and playing videos"),
    Component("whatsapp search and contacts",
              (r"whatsapp", r"contact", r"wrong (?:person|number|chat)", r"message (?:search|lookup)"),
              ("jarvis/integrations/contacts.py", "jarvis/memory/contacts_index.py", "jarvis/message_command.py"),
              ("tests/test_contacts_index.py", "tests/test_contact_messaging.py"),
              services=("jarvis-backend",),
              protected="contact resolution",
              description="who a message goes to"),
    Component("approvals",
              (r"approv\w*", r"confirm\w*"),
              ("jarvis/approvals.py",), ("tests/test_approvals.py",),
              services=("jarvis-backend",), protected="approval and confirmation enforcement"),
    Component("away mode",
              (r"away[\s-]mode", r"auto[\s-]?repl\w*"),
              ("jarvis/away_mode/*.py",), ("tests/test_away_mode.py",),
              services=("jarvis-backend",), protected="away-mode disclosure and safety"),
    Component("email and calendar",
              (r"\bemail\b", r"\bgmail\b", r"calendar", r"meeting invite"),
              ("jarvis/integrations/google/*.py",), ("tests/test_notifications.py",),
              services=("jarvis-backend",), protected="messaging, email, calendar and payment side effects"),
    Component("self-repair",
              (r"self[\s-]?repair", r"repair system", r"your (?:own )?restrictions"),
              ("jarvis/selfrepair/*.py",), ("tests/test_selfrepair*.py",),
              protected="self-repair policy and git restrictions"),
)


def component_named(name: str) -> Optional[Component]:
    return next((c for c in COMPONENTS if c.name == name), None)


def match_component(report: str) -> Optional[Component]:
    """The component a report is about: the one with the most keyword hits, earliest on a tie."""
    best, best_hits = None, 0
    for comp in COMPONENTS:
        hits = sum(1 for kw in comp.keywords if re.search(kw, report, re.I))
        if hits > best_hits:
            best, best_hits = comp, hits
    return best


def in_scope(path: str, component: Component) -> bool:
    """May a repair of `component` change `path`? Its files, or a new focused test for it."""
    path = str(PurePosixPath(path))
    if any(_matches(path, pat) for pat in component.files):
        return True
    return bool(re.fullmatch(r"tests/test_repair_[a-z0-9_]+\.py", path))


# ----------------------------------------------------------------------------- commands
def venv_python() -> str:
    candidate = REPO / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    main = Path(os.environ.get("JARVIS_REPAIR_VENV", "")) / "bin" / "python"
    if main.exists():
        return str(main)
    import sys

    return sys.executable


_SAFE_ARG = re.compile(r"^[A-Za-z0-9_./\-]+(?:::[A-Za-z0-9_\[\]\-]+)?$")


def allowed_command(argv: list[str], python: str) -> bool:
    """Exactly the command shapes a repair may run; anything else is refused before it starts.

        python -m pytest -q -p no:cacheprovider [-x] <tests/...>
        python -m py_compile <jarvis/... .py>
        node --check <overlay/... .js>
    """
    if not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
        return False
    if argv[0] == python and argv[1:3] == ["-m", "pytest"]:
        rest = argv[3:]
        fixed = ["-q", "-p", "no:cacheprovider"]
        if rest[:3] != fixed:
            return False
        targets = [a for a in rest[3:] if a not in {"-x"}]
        return bool(targets) and all(
            _SAFE_ARG.match(t) and (t == "tests" or t.startswith("tests/")) and ".." not in t for t in targets)
    if argv[0] == python and argv[1:3] == ["-m", "py_compile"]:
        files = argv[3:]
        return bool(files) and all(_SAFE_ARG.match(f) and f.endswith(".py") and ".." not in f
                                   and not f.startswith("/") for f in files)
    if argv[0] == "node" and len(argv) == 3 and argv[1] == "--check":
        f = argv[2]
        return bool(_SAFE_ARG.match(f)) and f.endswith(".js") and ".." not in f and not f.startswith("/")
    return False


# Git, as the pipeline uses it. Anything that pushes, rewrites, deletes or reaches a remote is
# absent — not refused by a pattern, simply not in the list.
_GIT_ALLOWED: dict[str, tuple[str, ...]] = {
    "rev-parse": ("--show-toplevel", "--git-common-dir", "--abbrev-ref", "HEAD", "--verify", "-q"),
    "status": ("--porcelain", "--untracked-files=no", "--untracked-files=all"),
    "diff": ("--numstat", "--name-only", "--no-color", "--no-ext-diff", "--binary", "--cached",
             "--summary", "-U3", "--stat"),
    "add": ("--",),
    "commit": ("-q", "-m", "--no-verify", "--author"),
    "worktree": ("add", "remove", "-b", "--force", "list", "--porcelain"),
    "merge": ("--ff-only", "-q"),
    "revert": ("--no-edit",),
    "log": ("--format=%H", "-1", "--oneline"),
    "merge-base": ("--is-ancestor",),
    "show": ("--stat", "--format=%H %s", "--no-color"),
    "ls-files": ("-s", "--stage", "--"),
}
FORBIDDEN_GIT = ("push", "reset", "rebase", "filter-branch", "remote", "branch", "update-ref",
                 "clean", "stash", "gc", "prune", "reflog", "fetch", "pull", "checkout", "switch",
                 "tag", "config", "am", "apply", "cherry-pick", "submodule")


def allowed_git(args: list[str]) -> bool:
    if not args:
        return False
    sub, rest = args[0], args[1:]
    if sub in FORBIDDEN_GIT or sub not in _GIT_ALLOWED:
        return False
    if sub == "worktree" and rest and rest[0] == "remove":
        # Only ever a repair's own worktree, and never with the force flag doubled up.
        return len([a for a in rest if not a.startswith("-")]) == 2 and \
            str(repairs_root()) in (rest[-1] if rest else "")
    allowed = _GIT_ALLOWED[sub]
    for arg in rest:
        if arg.startswith("-") and arg not in allowed and not arg.startswith(("--format=", "--author=")):
            return False
    return True


def repairs_root() -> Path:
    """Where repair worktrees live: outside the repository, private to the user."""
    base = os.environ.get("JARVIS_REPAIR_ROOT") or os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "jarvis", "repairs")
    return Path(base)


SERVICES_ALLOWED = ("jarvis-backend", "jarvis-voice")     # whatsapp is messaging: never restarted here


@dataclass
class TierDecision:
    tier: int
    reasons: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
