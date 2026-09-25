"""The policy boundary around a candidate diff.

Whatever wrote the change — the coding agent, a recipe, a person — the diff is judged here, by
the rules in ``policy.py`` as loaded from the running checkout:

    scope        every changed file is one the diagnosed component may touch (or a new
                 tests/test_repair_*.py); anything else fails the job outright
    protected    a change inside a protected area makes the repair tier 2: prepared, never
                 auto-activated, and activated only by a specific approval naming the area
    symlinks     no symlink added or changed, no path that resolves outside the worktree
    binary       no binary files
    dependencies no requirements/package manifest changed (tier 2 when it is)
    size         at most LIMITS.max_diff_lines changed lines and LIMITS.max_changed_files files
    secrets      nothing in the added lines that looks like a key, token, private key,
                 phone number or e-mail address; a hit fails the job and the diff is never kept
    tests        a repro test written to fail before the fix may not be edited afterwards
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import policy
from .sandbox import PolicyViolation, check_changed_paths, git

_SECRET_PATTERNS = (
    ("api key", re.compile(r"(?:sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|"
                           r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|xox[abpr]-[A-Za-z0-9\-]{10,}|"
                           r"AKIA[0-9A-Z]{16})")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("assigned secret", re.compile(r"(?i)\b(?:password|passwd|secret|api_?key|token|bearer)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("phone number", re.compile(r"(?<![\w.])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\w.])|"
                                r"\+\d{1,3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{4}")),
    ("e-mail address", re.compile(r"\b[\w.+-]+@(?!example\.(?:com|org))[\w-]+\.(?:com|in|org|net|io|co)\b")),
)


def scan_secrets(added_lines: list[str]) -> list[str]:
    """Kinds of secret found in added lines — the kind only, never the match."""
    found = []
    for kind, pattern in _SECRET_PATTERNS:
        if any(pattern.search(line) for line in added_lines):
            found.append(kind)
    return found


@dataclass
class DiffReport:
    changed: list[str] = field(default_factory=list)
    added_lines: int = 0
    removed_lines: int = 0
    out_of_scope: list[str] = field(default_factory=list)
    protected: dict[str, list[str]] = field(default_factory=dict)   # area -> paths
    dependencies: list[str] = field(default_factory=list)
    binary: list[str] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)             # fail the job
    escalations: list[str] = field(default_factory=list)            # make it tier 2

    @property
    def lines(self) -> int:
        return self.added_lines + self.removed_lines

    @property
    def ok(self) -> bool:
        return not self.violations


def changed_files(worktree: Path) -> list[str]:
    """Tracked changes and new files in the worktree, relative, sorted."""
    out = git(["status", "--porcelain", "--untracked-files=all"], worktree)
    paths = []
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.strip().strip('"'))
    return sorted(set(paths))


def inspect(worktree: Path, component: policy.Component, frozen_tests: dict[str, str] | None = None,
            limits: policy.Limits = policy.LIMITS) -> DiffReport:
    """Judge everything changed in `worktree` against `component`'s scope and the policy."""
    report = DiffReport(changed=changed_files(worktree))
    if not report.changed:
        report.violations.append("nothing was changed")
        return report
    try:
        check_changed_paths(worktree, report.changed)
    except PolicyViolation as exc:
        report.violations.append(str(exc))
    for rel in report.changed:
        path = Path(worktree) / rel
        if path.is_symlink():
            report.symlinks.append(rel)
        if not policy.in_scope(rel, component):
            report.out_of_scope.append(rel)
        area = policy.protected_area(rel)
        if area:
            report.protected.setdefault(area, []).append(rel)
        if policy.is_dependency_file(rel):
            report.dependencies.append(rel)
        if path.is_file() and not path.is_symlink():
            head = path.read_bytes()[:8192]
            if b"\x00" in head:
                report.binary.append(rel)
    # Staged view, so new files are counted too; intent-to-add is not needed for numstat on --cached.
    git(["add", "--", *[c for c in report.changed if c not in report.symlinks]], worktree)
    numstat = git(["diff", "--cached", "--numstat", "--no-color", "--no-ext-diff"], worktree).stdout
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            if parts[0] == "-" or parts[1] == "-":
                continue
            report.added_lines += int(parts[0])
            report.removed_lines += int(parts[1])
    mode = git(["diff", "--cached", "--summary", "--no-color"], worktree).stdout
    report.symlinks += [m for m in re.findall(r"mode 120000 (\S+)", mode) if m not in report.symlinks]
    diff = git(["diff", "--cached", "--no-color", "--no-ext-diff", "-U3"], worktree).stdout
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    report.secrets = scan_secrets(added)

    if report.out_of_scope:
        report.violations.append("changed files outside the approved component: " + ", ".join(report.out_of_scope))
    if report.symlinks:
        report.violations.append("adds or changes symlinks: " + ", ".join(report.symlinks))
    if report.binary:
        report.violations.append("adds binary files: " + ", ".join(report.binary))
    if report.secrets:
        report.violations.append("the diff contains what looks like a " + " and a ".join(report.secrets))
    if len(report.changed) > limits.max_changed_files:
        report.violations.append(f"changes {len(report.changed)} files (limit {limits.max_changed_files})")
    for rel, digest in (frozen_tests or {}).items():
        path = Path(worktree) / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            report.violations.append(f"the reproduction test {rel} was changed after it failed")
    if report.protected:
        report.escalations.append("touches protected areas: " + ", ".join(sorted(report.protected)))
    if report.dependencies:
        report.escalations.append("changes dependencies: " + ", ".join(report.dependencies))
    if report.lines > limits.max_diff_lines:
        report.escalations.append(f"{report.lines} changed lines (limit {limits.max_diff_lines} for automatic activation)")
    return report


def file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
