"""Resource coordination across features: voice keeps priority, heavy work never blocks commands."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest


def test_voice_keeps_priority_over_a_blender_preview():
    from jarvis.three_d import resources
    tight = resources.Snapshot(vram_total_mb=4096, vram_free_mb=700, ram_available_mb=8000, disk_free_mb=9000)
    ok, why = resources.can_render(tight)
    assert not ok and why
    roomy = resources.Snapshot(vram_total_mb=4096, vram_free_mb=2500, ram_available_mb=8000, disk_free_mb=9000)
    assert resources.can_render(roomy)[0]


def test_speech_recognition_goes_to_the_gpu_only_when_it_fits(monkeypatch):
    from jarvis import resources
    resources.forget()
    monkeypatch.setattr(resources, "gpu_memory", lambda: (3500, 4096))
    assert not resources.room_on_gpu("small")
    monkeypatch.setattr(resources, "gpu_memory", lambda: (1500, 4096))
    assert resources.room_on_gpu("small")


def test_local_model_loading_respects_memory_limits():
    from jarvis.brain import local_models
    ok, why = local_models.can_load(3.4, {"ram_available_gb": 4.0, "vram_gb": 4.0, "vram_used_gb": 3.3})
    assert not ok and "free memory" in why
    ok, why = local_models.can_load(1.9, {"ram_available_gb": 12.0, "vram_gb": 4.0, "vram_used_gb": 3.3})
    assert ok and "processor" in why
    assert local_models.can_load(1.9, {"ram_available_gb": 12.0, "vram_gb": 4.0, "vram_used_gb": 0.5}) == (True, "")


def test_study_processing_does_not_starve_wake_word_handling(db, fake, study):
    """A slow study model call runs in a worker thread; the event loop keeps serving other work."""
    release = threading.Event()

    def slow(body):
        release.wait(5)
        return "Accommodation is the eye adjusting focus."
    fake.set("openai/gpt-oss-120b", slow)

    async def scenario():
        from jarvis.brain import daily
        ticks = 0
        task = asyncio.create_task(daily.maybe_answer("explain the power of accommodation of the human eye", "voice"))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:          # a wake-word loop's cadence keeps ticking
            await asyncio.sleep(0.02)
            ticks += 1
        release.set()
        return ticks, await task
    ticks, out = asyncio.run(scenario())
    assert ticks >= 15 and out is not None


def test_provider_timeout_does_not_block_deterministic_commands(db, fake, monkeypatch):
    from jarvis import commands, open_command
    from jarvis.config import CONFIG
    fake.set("openai/gpt-oss-20b", ("timeout",))
    fake.set("openai/gpt-oss-120b", ("timeout",))

    async def opened(text, config):
        return "Opened Settings." if text.lower().startswith("open settings") else None
    monkeypatch.setattr(open_command, "handle", opened)
    t0 = time.monotonic()
    out = asyncio.run(commands.handle("open settings", CONFIG, "voice"))
    assert out == "Opened Settings." and time.monotonic() - t0 < 2.0
    assert fake.calls == []


def test_a_blender_crash_does_not_crash_the_backend(monkeypatch, tmp_path):
    from jarvis.three_d import blender_bridge as bb
    if bb.find_blender() is None:
        pytest.skip("Blender is not installed")
    from jarvis.three_d.studio import Studio, StudioConfig
    st = Studio(StudioConfig(present=False, root=str(tmp_path / "p")), announce=lambda t, s=False: None,
                status=lambda t: None)
    try:
        s = st._session(watch=False)
        s.call("new_project")
        proc = s.proc if hasattr(s, "proc") else getattr(s, "_proc", None)
        assert proc is not None
        proc.kill()                                    # Blender dies under the job
        proc.wait(10)
        try:
            s.call("info")
        except Exception as exc:  # noqa: BLE001 — reported as an error, not a crash of this process
            assert isinstance(exc, (bb.BridgeError, OSError, ConnectionError, RuntimeError))
        assert threading.main_thread().is_alive()
    finally:
        st.close()
