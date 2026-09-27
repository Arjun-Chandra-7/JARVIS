"""The restricted Blender bridge against a real headless Blender: security and failure handling.

Skipped when Blender is not installed. Every Blender here is started and owned by the test.
"""
import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

import pytest

from jarvis.three_d import blender_bridge as bb
from jarvis.three_d.types import ErrorCategory

pytestmark = pytest.mark.skipif(bb.find_blender() is None, reason="Blender is not installed")

BOX = {"op": "create_primitive", "name": "Box", "shape": "box", "size": [0.04, 0.02, 0.01], "location": [0, 0, 0.005]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, addr):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("network connection attempted")
        return real(self, addr)
    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    root = tmp_path_factory.mktemp("bridge")
    s = bb.BlenderSession([str(root)]).start()
    yield s, root
    s.close()


def test_headless_blender_builds_named_parts_with_real_dimensions(shared):
    s, root = shared
    s.call("new_project")
    s.apply([{"op": "create_collection", "name": "Parts"}, {**BOX, "collection": "Parts"},
             {"op": "create_primitive", "name": "Peg", "shape": "cylinder", "size": [0.01, 0.01, 0.02],
              "location": [0, 0, 0.02], "collection": "Parts"},
             {"op": "set_parent", "child": "Peg", "parent": "Box"}], known=[])
    objs = {o["name"]: o for o in s.scene()["objects"]}
    assert [round(v * 1000, 3) for v in objs["Box"]["size"]] == [40, 20, 10]
    assert objs["Peg"]["parent"] == "Box" and objs["Peg"]["collection"] == "Parts"
    assert objs["Peg"]["bounds"][0][2] == pytest.approx(0.01, abs=1e-6)       # stayed put when parented
    assert s.version >= (4, 2, 0) and s.health().alive


def test_wrong_token_is_rejected(shared):
    s, _ = shared
    assert bb.raw_connect(s.sock_path, "0" * 64) == {"ok": False, "error": "not authenticated"}


def test_a_foreign_process_is_rejected_even_with_the_token(shared):
    s, _ = shared
    code = ("import sys; sys.path.insert(0, %r); from jarvis.three_d.blender_bridge import raw_connect; "
            "print(raw_connect(%r, %r))" % (os.getcwd(), s.sock_path, s._token))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout
    assert "foreign process" in out and "'ok': True" not in out


def test_socket_and_directory_are_private(shared):
    s, _ = shared
    assert stat.S_IMODE(os.stat(s.sock_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(s.sock_path)).st_mode) == 0o700
    assert not [p for p in os.listdir(os.path.dirname(s.sock_path)) if p.startswith("token-")], "token file must be gone"


def test_there_is_no_arbitrary_code_endpoint(shared):
    s, _ = shared
    for cmd in ("exec", "python", "run_script", "eval", "shell", "operator"):
        with pytest.raises(bb.BridgeError, match="unknown command"):
            s.call(cmd, {"code": "import os; os.system('true')"})
    with pytest.raises(bb.BridgeError) as e:
        s.call("apply", {"plan": [{"op": "exec_python", "code": "import os"}]})    # bypassing the client check
    assert e.value.category == ErrorCategory.INVALID_PLAN


def test_the_server_refuses_paths_outside_the_project(shared, tmp_path):
    s, root = shared
    for bad in ("/tmp/escape.blend", str(root / ".." / "escape.blend")):
        with pytest.raises(bb.BridgeError, match="outside"):
            s.call("save", {"path": bad})
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "sneaky"
    link.symlink_to(outside)
    with pytest.raises(bb.BridgeError, match="outside"):
        s.call("save", {"path": str(link / "x.blend")})
    assert not (outside / "x.blend").exists()
    with pytest.raises(bb.BridgeError, match="outside"):
        s.call("open", {"path": "/etc/hostname"})
    with pytest.raises(bb.BridgeError, match="outside"):
        s.call("export", {"format": "stl", "path": str(tmp_path / "x.stl")})


def test_only_owned_objects_can_be_changed(shared):
    s, _ = shared
    with pytest.raises(bb.BridgeError):
        s.call("apply", {"plan": [{"op": "delete_object", "target": "Camera"}]})


def test_a_failed_save_leaves_the_previous_file_intact(shared):
    s, root = shared
    s.call("new_project")
    s.apply([BOX], known=[])
    d = root / "locked"
    d.mkdir()
    target = d / "model.blend"
    s.save(str(target))
    before = target.read_bytes()
    os.chmod(d, 0o500)
    try:
        s.apply([{"op": "set_transform", "target": "Box", "location": [0.1, 0, 0]}])
        with pytest.raises(bb.BridgeError):
            s.save(str(target))
    finally:
        os.chmod(d, 0o700)
    assert target.read_bytes() == before
    assert not [p for p in os.listdir(d) if "saving" in p]


def test_polygon_and_render_limits(shared):
    s, root = shared
    with pytest.raises(bb.BridgeError):
        s.call("apply", {"plan": [{**BOX, "name": "Big", "shape": "sphere", "segments": 256},
                                  {"op": "array", "target": "Big", "count": 64, "offset": [0.1, 0, 0]}]})
    s.call("new_project")
    s.apply([BOX, {"op": "add_camera", "name": "Cam", "location": [0, -1, 0], "look_at": [0, 0, 0]}], known=[])
    with pytest.raises(bb.BridgeError, match="resolution"):
        s.render("Cam", str(root / "big.png"), resolution=8000)


def test_version_mismatch_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "blender_version", lambda binary, timeout=30: (3, 6, 0))
    with pytest.raises(bb.BridgeError) as e:
        bb.BlenderSession([str(tmp_path)]).start()
    assert e.value.category == ErrorCategory.BLENDER_UNAVAILABLE and "3.6" in str(e.value)


def test_blender_unavailable_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(bb, "find_blender", lambda: None)
    with pytest.raises(bb.BridgeError) as e:
        bb.BlenderSession([str(tmp_path)]).start()
    assert e.value.category == ErrorCategory.BLENDER_UNAVAILABLE


def test_crash_is_detected_and_recovery_reopens_the_checkpoint(tmp_path):
    s = bb.BlenderSession([str(tmp_path)]).start()
    try:
        s.apply([BOX], known=[])
        s.save(str(tmp_path / "model.blend"))
        s.apply([{"op": "set_transform", "target": "Box", "location": [0.5, 0, 0]}])   # unsaved
        os.killpg(s.proc.pid, signal.SIGKILL)
        s.proc.wait(timeout=10)
        with pytest.raises(bb.BridgeError) as e:
            s.scene()
        assert e.value.category in (ErrorCategory.BLENDER_CRASHED, ErrorCategory.BRIDGE_LOST)
        s.restart()
        objs = {o["name"]: o for o in s.scene()["objects"]}
        assert "Box" in objs and objs["Box"]["location"][0] == pytest.approx(0.0)   # the checkpoint, not the lost edit
        assert s.crashes == 1
    finally:
        s.close()


def test_memory_watchdog_stops_a_runaway_blender(tmp_path):
    s = bb.BlenderSession([str(tmp_path)], memory_mb=60).start()
    try:
        deadline = time.time() + 15
        while s.alive() and time.time() < deadline:
            time.sleep(0.2)
        assert not s.alive()
        with pytest.raises(bb.BridgeError) as e:
            s.scene()
        assert e.value.category == ErrorCategory.RESOURCE_LIMIT
    finally:
        s.close()


def test_a_render_over_its_time_budget_is_stopped(tmp_path):
    s = bb.BlenderSession([str(tmp_path)]).start()
    try:
        s.apply([BOX, {"op": "add_camera", "name": "Cam", "location": [0, -1, 0], "look_at": [0, 0, 0]}], known=[])
        with pytest.raises(bb.BridgeError) as e:
            s.render("Cam", str(tmp_path / "r.png"), resolution=1024, timeout=0.01)
        assert e.value.category == ErrorCategory.RESOURCE_LIMIT and not s.alive()
    finally:
        s.close()


def test_cancel_stops_a_long_plan_between_operations(tmp_path):
    s = bb.BlenderSession([str(tmp_path)]).start()
    try:
        plan = [{"op": "create_primitive", "name": f"B{i}", "shape": "cylinder", "size": [0.01] * 3, "segments": 256,
                 "location": [i * 0.001, 0, 0]} for i in range(1500)]
        result = {}

        def run():
            try:
                result["r"] = s.apply(plan, known=[], timeout=300)
            except bb.BridgeError as exc:
                result["e"] = exc
        t = threading.Thread(target=run)
        t.start()
        while s.health().busy != "apply" and t.is_alive():
            time.sleep(0.02)
        assert s.cancel()
        t.join(60)
        assert "e" in result and result["e"].category == ErrorCategory.CANCELLED
        assert len(s.scene()["objects"]) < 1500
    finally:
        s.close()
