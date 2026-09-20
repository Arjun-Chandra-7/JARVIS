"""The wall around the shell.

These run real commands inside a real namespace. A sandbox that has only been reasoned about is
not a sandbox — the interesting failures are all of the form "the policy said no and the kernel
said yes", and only the kernel can settle that.

Skipped rather than failed where bubblewrap is absent, because the point of the module is that it
degrades honestly there, and that part is tested without it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.agent import sandbox

needs_bwrap = pytest.mark.skipif(not sandbox.available(), reason="bubblewrap is not installed")


# --------------------------------------------------------------------------- the policy itself
def test_the_command_is_one_argument_not_a_string_to_re_parse():
    """Everything the model wrote goes to `bash -c` as a single argv entry, so nothing in it can
    be read as an argument to bwrap."""
    argv = sandbox.build_argv("echo hi; rm -rf /", sandbox.GUARDED)
    assert argv[-3:] == ["/bin/bash", "-c", "echo hi; rm -rf /"]
    assert argv[0] == "bwrap"


def test_a_login_shell_is_not_used():
    """`bash -lc` sources the profile, and on this machine that printed a GIO module warning into
    every single result. The environment is inherited anyway."""
    assert "-lc" not in sandbox.build_argv("true", sandbox.GUARDED)


def test_strict_asks_for_no_network_and_guarded_does_not():
    assert "--unshare-net" in sandbox.build_argv("true", sandbox.STRICT)
    assert "--unshare-net" not in sandbox.build_argv("true", sandbox.GUARDED)


def test_masks_come_after_the_bind_they_sit_on():
    """Order is the whole point: a mask applied before the bind containing it is overwritten."""
    argv = sandbox.build_argv("true", sandbox.GUARDED)
    home = os.path.expanduser("~")
    ssh = os.path.expanduser("~/.ssh")
    if not Path(ssh).exists():
        pytest.skip("no ~/.ssh on this machine to order against")
    assert argv.index("--bind") < argv.index(ssh)


def test_the_system_is_read_only():
    argv = sandbox.build_argv("true", sandbox.STRICT)
    pairs = list(zip(argv, argv[1:]))
    assert ("--ro-bind", "/usr") in pairs
    assert ("--bind", "/usr") not in pairs


# --------------------------------------------------------------------------- honest degradation
def test_without_bubblewrap_it_says_so_rather_than_pretending(monkeypatch):
    """A caller that believes it is sandboxed when it is not is worse off than one that knows."""
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    assert not sandbox.available()
    assert "bubblewrap is not installed" in sandbox.why_unavailable()

    result = sandbox.run("echo hi", sandbox.GUARDED, timeout=10)
    assert result.sandboxed is False
    assert "bubblewrap is not installed" in result.note
    assert "hi" in result.stdout


def test_a_caller_can_refuse_to_run_unsandboxed(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    result = sandbox.run("echo hi", sandbox.GUARDED, allow_unsandboxed=False, timeout=10)
    assert result.returncode == 126
    assert result.stdout == ""


# --------------------------------------------------------------------------- against the kernel
@needs_bwrap
def test_an_ordinary_command_still_works():
    """A sandbox that breaks the shell would simply be turned off."""
    result = sandbox.run("echo hello", sandbox.GUARDED, timeout=30)
    assert result.sandboxed
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


@needs_bwrap
def test_credentials_are_not_there_to_be_read():
    """The measured case: outside, ~/.ssh holds real files; inside GUARDED it holds nothing."""
    ssh = Path(os.path.expanduser("~/.ssh"))
    if not ssh.is_dir() or not any(ssh.iterdir()):
        pytest.skip("no populated ~/.ssh to hide on this machine")

    outside = sorted(p.name for p in ssh.iterdir())
    inside = sandbox.run("ls -A ~/.ssh", sandbox.GUARDED, timeout=30)
    assert inside.sandboxed
    assert inside.stdout.strip() == "", f"the sandbox could see {outside}"


@needs_bwrap
def test_strict_cannot_see_the_home_at_all():
    result = sandbox.run("ls -A ~ | wc -l", sandbox.STRICT, timeout=30)
    assert result.stdout.strip() == "0"


@needs_bwrap
def test_strict_has_no_network_and_guarded_does():
    """Guarded needs DNS as well as a route — on this distribution /etc/resolv.conf is a symlink
    into /run, which the tmpfs replaces, so binding the target is what makes the difference
    between 'no network' and 'network that cannot resolve anything'."""
    probe = "timeout 8 curl -s -o /dev/null -w '%{http_code}' https://example.com"
    blocked = sandbox.run(probe, sandbox.STRICT, timeout=30)
    assert blocked.stdout.strip().endswith("000"), "strict reached the network"

    allowed = sandbox.run(probe, sandbox.GUARDED, timeout=40)
    if allowed.stdout.strip().endswith("000"):
        pytest.skip("this machine has no working network to test the other direction with")
    assert allowed.stdout.strip().endswith("200")


@needs_bwrap
def test_strict_still_has_somewhere_to_work():
    """Found by running it: with no /tmp mount, bwrap could not even chdir, so every strict
    command failed before it started."""
    result = sandbox.run("echo scratch > /tmp/x && cat /tmp/x", sandbox.STRICT, timeout=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "scratch"


@needs_bwrap
def test_a_write_inside_strict_does_not_reach_the_real_disk():
    marker = Path(os.path.expanduser("~/.jarvis-sandbox-escape-check"))
    assert not marker.exists(), "stale marker from an earlier run"
    sandbox.run(f"touch {marker}", sandbox.STRICT, timeout=30)
    assert not marker.exists(), "the sandbox wrote to the real home"
