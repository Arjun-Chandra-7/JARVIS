"""Deployment: what restart and update may do to the running checkout — and what they may not.

A bare "origin" and a live clone, real git. Each test is one situation the owner can be in."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis import deploy

REF = "origin/live-failure-repair"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, path, text, msg):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "live-failure-repair", str(origin)], check=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True, capture_output=True)
    git(dev, "checkout", "-q", "-b", "live-failure-repair")
    base = commit(dev, "app.py", "v = 1\n", "base")
    git(dev, "push", "-q", "origin", "live-failure-repair")
    live = tmp_path / "live"
    subprocess.run(["git", "clone", "-q", str(origin), str(live)], check=True, capture_output=True)
    git(live, "checkout", "-q", "-b", "daily", "origin/live-failure-repair")
    git(live, "config", "user.email", "t@example.com")
    git(live, "config", "user.name", "t")
    return {"origin": origin, "dev": dev, "live": live, "base": base}


def release(world, text="v = 2\n", msg="release 2"):
    sha = commit(world["dev"], "app.py", text, msg)
    git(world["dev"], "push", "-q", "origin", "live-failure-repair")
    return sha


def local_repair(world, name="fix.py"):
    sha = commit(world["live"], name, "fixed = True\n", "Repair 0925-abc123: fix")
    deploy.record_deploy(sha, world["base"], "local-repair", "repair activated")
    return sha


def quiet(*_):
    pass


def test_restart_takes_a_release_by_fast_forward_and_records_it(world):
    new = release(world)
    s = deploy.pre_restart(world["live"], REF, say=quiet)
    assert s.kind == "release" and s.head == new
    rec = deploy.load_record()
    assert rec["active"] == new and rec["previous"] == world["base"] and rec["kind"] == "release"


def test_restart_never_overwrites_an_activated_local_repair(world):
    fix = local_repair(world)
    s = deploy.pre_restart(world["live"], REF, say=quiet)
    assert s.kind == "local-repair" and s.head == fix and s.repairs == [fix]
    assert "kept" in s.message and deploy._sha(world["live"], "HEAD") == fix


def test_unactivated_local_commits_are_named_as_such(world):
    sha = commit(world["live"], "x.py", "x = 1\n", "hand edit")
    s = deploy.classify(world["live"], REF)
    assert s.kind == "local-commits" and s.ahead == [sha] and not s.repairs


def test_local_edits_stop_every_move(world):
    release(world)
    (world["live"] / "app.py").write_text("v = 'mine'\n")
    s = deploy.pre_restart(world["live"], REF, say=quiet)
    assert s.kind == "dirty" and (world["live"] / "app.py").read_text() == "v = 'mine'\n"
    with pytest.raises(RuntimeError):
        deploy.rollback(world["live"], target=world["base"])


def test_a_release_that_moved_past_a_local_repair_stops_and_explains(world):
    fix = local_repair(world)
    release(world)
    s = deploy.pre_restart(world["live"], REF, say=quiet)
    assert s.kind == "diverged" and s.head == fix and s.behind == 1
    assert s.actions == ["keep", "integrate", "export", "discard"] and "Nothing was changed" in s.message


def test_integrate_merges_the_release_into_the_repair_when_they_do_not_conflict(world):
    fix = local_repair(world)
    new = release(world)
    msg = deploy.integrate(world["live"], REF)
    assert msg.startswith("Merged")
    head = deploy._sha(world["live"], "HEAD")
    for sha in (fix, new):
        assert git(world["live"], "merge-base", "--is-ancestor", sha, head) == ""
    assert deploy.load_record()["kind"] == "local-repair"


def test_integrate_stops_on_a_conflict_and_leaves_everything_as_it_was(world):
    fix = commit(world["live"], "app.py", "v = 'repaired'\n", "Repair 0925-abc123: fix")
    deploy.record_deploy(fix, world["base"], "local-repair", "repair activated")
    release(world, "v = 'released'\n")
    msg = deploy.integrate(world["live"], REF)
    assert "conflicts" in msg and "app.py" in msg
    assert deploy._sha(world["live"], "HEAD") == fix and (world["live"] / "app.py").read_text() == "v = 'repaired'\n"
    assert git(world["live"], "status", "--porcelain") == ""


def test_export_writes_patches_and_changes_nothing(world, tmp_path):
    fix = local_repair(world)
    files = deploy.export(world["live"], REF, out_dir=tmp_path / "patches")
    assert len(files) == 1 and files[0].endswith(".patch") and "fixed = True" in Path(files[0]).read_text()
    assert deploy._sha(world["live"], "HEAD") == fix


def test_discard_returns_to_the_release_and_keeps_a_backup_branch(world):
    fix = local_repair(world)
    new = release(world)
    msg = deploy.discard(world["live"], REF)
    assert deploy._sha(world["live"], "HEAD") == deploy._sha(world["live"], REF) == new
    assert deploy._sha(world["live"], f"jarvis/discarded/{fix[:12]}") == fix and "kept on" in msg


def test_rollback_returns_to_the_previous_deploy_pins_it_and_update_lifts_the_pin(world):
    new = release(world)
    deploy.pre_restart(world["live"], REF, say=quiet)
    s = deploy.rollback(world["live"])
    assert s.head == world["base"] and s.kind == "pinned"
    assert deploy._sha(world["live"], f"jarvis/rollback-from/{new[:12]}") == new
    s = deploy.pre_restart(world["live"], REF, say=quiet)          # a plain restart keeps the rollback
    assert s.kind == "pinned" and s.head == world["base"]
    s = deploy.update(world["live"], REF, say=quiet)
    assert s.head == new and s.kind == "release" and not deploy.load_record()["pinned"]


def test_a_reverted_repair_still_counts_as_verified_local_state(world):
    fix = local_repair(world)
    git(world["live"], "revert", "--no-edit", fix)
    s = deploy.classify(world["live"], REF)
    assert s.kind == "local-repair"


def test_verify_requires_every_service_on_the_new_commit(world):
    head = deploy._sha(world["live"], "HEAD")[:12]
    ok, seen = deploy.verify(world["live"], wait_s=0.1, loaded=lambda: {"backend": head, "voice": head,
                                                                          "whatsapp": head},
                             required=("backend", "voice", "whatsapp"))
    assert ok
    ok, _ = deploy.verify(world["live"], wait_s=0.1, loaded=lambda: {"backend": head, "voice": "5647adc00000",
                                                                     "whatsapp": head},
                          required=("backend", "voice", "whatsapp"))
    assert not ok


def test_updates_can_be_switched_off(world):
    release(world)
    s = deploy.pre_restart(world["live"], "none", say=quiet)
    assert s.kind == "disabled" and deploy._sha(world["live"], "HEAD") == world["base"]


def _installed(world, tmp_path, healthy: bool):
    """The live clone with the real bin/jarvis, a stand-in start.sh and a venv that runs this code."""
    import os
    import shutil
    import sys

    live = world["live"]
    src = Path(deploy.__file__).resolve().parents[1]
    (live / "bin").mkdir()
    shutil.copy(src / "bin" / "jarvis", live / "bin" / "jarvis")
    (live / "scripts").mkdir()
    (live / "scripts" / "start.sh").write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/../started.log"\n')
    (live / ".venv" / "bin").mkdir(parents=True)
    (live / ".venv" / "bin" / "python").write_text(f'#!/bin/sh\nPYTHONPATH="{src}" exec "{sys.executable}" "$@"\n')
    for p in ("bin/jarvis", "scripts/start.sh", ".venv/bin/python"):
        os.chmod(live / p, 0o755)
    (live / ".git" / "info" / "exclude").write_text("bin/\nscripts/\n.venv/\nstarted.log\n")
    env = {**os.environ, "JARVIS_DEPLOY_REPO": str(live), "JARVIS_DEPLOY_WAIT": "3",
           "JARVIS_WEB_PORT": "9", "WA_PORT": "9"}          # nothing listens: nothing is healthy
    if healthy:
        port = _serve_health(live)
        env.update({"JARVIS_WEB_PORT": str(port), "WA_PORT": str(port)})
    return live, env


def _serve_health(live):
    """Services that load whatever the checkout's HEAD is — as real ones would after a restart."""
    import http.server
    import json
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            head = deploy._sha(live, "HEAD")[:12]
            body = {"build": {"commit": head}, "voice_build": {"commit": head}, "commit": head}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def test_jarvis_restart_rolls_back_an_update_that_does_not_come_up_healthy(world, tmp_path):
    old = world["base"]
    release(world)
    live, env = _installed(world, tmp_path, healthy=False)
    out = subprocess.run([str(live / "bin" / "jarvis"), "restart", "--headless"], env=env,
                         capture_output=True, text=True, timeout=120)
    assert "did not come up healthy" in out.stdout, out.stdout + out.stderr
    assert deploy._sha(live, "HEAD") == old                        # back where it was
    assert (live / "started.log").read_text().count("--restart") == 2
    assert out.returncode != 0


def test_jarvis_restart_keeps_a_healthy_update(world, tmp_path):
    new = release(world)
    live, env = _installed(world, tmp_path, healthy=True)
    out = subprocess.run([str(live / "bin" / "jarvis"), "restart", "--headless"], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert deploy._sha(live, "HEAD") == new and "all services run" in out.stdout


def test_self_repair_activation_is_recorded_as_a_local_repair(world, monkeypatch):
    from jarvis.selfrepair import activate

    activate._record("a" * 40, world["base"], "repair activated")
    rec = deploy.load_record()
    assert rec["kind"] == "local-repair" and "a" * 40 in rec["repairs"]
