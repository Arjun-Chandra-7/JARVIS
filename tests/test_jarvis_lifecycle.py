import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _launcher_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Build the smallest repo layout needed to exercise bin/jarvis dispatch."""
    bin_dir = tmp_path / "bin"
    scripts_dir = tmp_path / "scripts"
    bin_dir.mkdir()
    scripts_dir.mkdir()
    launcher = bin_dir / "jarvis"
    shutil.copy2(ROOT / "bin" / "jarvis", launcher)

    calls = tmp_path / "calls"
    start = scripts_dir / "start.sh"
    start.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$JARVIS_TEST_CALLS\"\n"
        "exit \"${JARVIS_TEST_EXIT:-0}\"\n"
    )
    start.chmod(0o755)
    return launcher, calls


def _run(launcher: Path, calls: Path, *args: str, exit_code: str = "0"):
    env = os.environ.copy()
    env["JARVIS_TEST_CALLS"] = str(calls)
    env["JARVIS_TEST_EXIT"] = exit_code
    return subprocess.run(
        [str(launcher), *args], env=env, text=True, capture_output=True, check=False
    )


def test_start_dispatches_to_gui_start(tmp_path):
    launcher, calls = _launcher_fixture(tmp_path)
    result = _run(launcher, calls, "start")

    assert result.returncode == 0
    assert calls.read_text().splitlines() == ["--gui"]


def test_stop_dispatches_to_shared_stop(tmp_path):
    launcher, calls = _launcher_fixture(tmp_path)
    result = _run(launcher, calls, "stop")

    assert result.returncode == 0
    assert calls.read_text().splitlines() == ["--stop"]


def test_restart_is_one_operation_and_forwards_arguments(tmp_path):
    launcher, calls = _launcher_fixture(tmp_path)
    result = _run(launcher, calls, "restart", "--headless")

    assert result.returncode == 0
    assert calls.read_text().splitlines() == ["--restart --headless"]


def test_lifecycle_commands_propagate_failures(tmp_path):
    launcher, calls = _launcher_fixture(tmp_path)

    assert _run(launcher, calls, "stop", exit_code="23").returncode == 23
    assert _run(launcher, calls, "restart", exit_code="24").returncode == 24


def test_shared_controller_has_expected_public_functions():
    controller = (ROOT / "scripts" / "jarvisctl.sh").read_text()

    for function in ("jarvis_start_services", "jarvis_stop", "jarvis_restart", "jarvis_status"):
        assert f"{function}()" in controller
