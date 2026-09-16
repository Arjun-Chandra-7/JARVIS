"""The picture generator: the parts that hold without loading two and a half gigabytes.

Nothing here generates an image. A real generation takes about seven seconds of processor time,
which does not belong in a suite that runs on every change; what belongs here is everything that
decides *what* gets generated, and those are all cheap.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from jarvis.vision import imagine


class _FakeImage:
    def __init__(self):
        self.saved_to = None

    def save(self, path):
        self.saved_to = Path(path)
        Path(path).write_bytes(b"\x89PNG")


class _FakePipe:
    """Stands in for the diffusion pipeline and records what it was asked for."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt, **kw):
        self.calls.append({"prompt": prompt, **kw})
        return types.SimpleNamespace(images=[_FakeImage()])


@pytest.fixture
def pipe(monkeypatch, tmp_path):
    fake = _FakePipe()
    monkeypatch.setattr(imagine, "_load", lambda: fake)
    monkeypatch.setattr(imagine, "SAVE_TO", tmp_path / "pictures")
    # `generate` imports torch only to seed; a stub keeps the test off the real one.
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        Generator=lambda _d: types.SimpleNamespace(manual_seed=lambda s: s)))
    return fake


def test_an_empty_prompt_is_refused_rather_than_drawn(pipe):
    with pytest.raises(ValueError):
        imagine.generate("   ")


def test_the_picture_lands_somewhere_the_user_can_find_it(pipe, tmp_path):
    made = imagine.generate("a red fox")
    assert made.path.exists()
    assert made.path.parent == tmp_path / "pictures"
    assert "red_fox" in made.path.name


def test_the_name_keeps_the_prompt_but_not_its_punctuation(pipe):
    made = imagine.generate('a cat: "sitting", 50% lit')
    assert "/" not in made.path.name and ":" not in made.path.name
    assert "cat" in made.path.name


def test_guidance_is_off_because_a_turbo_model_was_trained_without_it(pipe):
    imagine.generate("anything")
    assert pipe.calls[0]["guidance_scale"] == 0.0


def test_one_step_by_default_because_the_second_costs_as_much_as_the_first(pipe):
    imagine.generate("anything")
    assert pipe.calls[0]["num_inference_steps"] == 1


def test_an_absurd_size_is_brought_back_to_something_the_model_can_do(pipe):
    imagine.generate("anything", size=4096)
    assert pipe.calls[0]["height"] == 768
    imagine.generate("anything", size=16)
    assert pipe.calls[1]["height"] == 256


def test_the_size_stays_a_multiple_of_eight(pipe):
    imagine.generate("anything", size=515)
    assert pipe.calls[0]["height"] % 8 == 0


def test_a_hundred_steps_is_not_honoured(pipe):
    """Left unclamped this is a ten-minute request that looks like a hung machine."""
    imagine.generate("anything", steps=100)
    assert pipe.calls[0]["num_inference_steps"] <= 8


def test_the_same_seed_is_passed_through_so_a_picture_can_be_repeated(pipe):
    imagine.generate("anything", seed=42)
    assert pipe.calls[0]["generator"] == 42


def test_no_seed_means_no_generator_rather_than_seed_zero(pipe):
    imagine.generate("anything")
    assert pipe.calls[0]["generator"] is None


def test_generation_reserves_cores_for_the_rest_of_jarvis():
    """All eight cores on the picture means Whisper misses the next sentence."""
    import os
    assert 1 <= imagine.THREADS < (os.cpu_count() or 4)


def test_readiness_does_not_depend_on_a_complete_repository(monkeypatch, tmp_path):
    """Only the half-precision weights are fetched, so the hub calls the snapshot incomplete."""
    monkeypatch.setattr(imagine, "_pipe", None)
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    assert imagine.ready() is False
    for sub in (f"hub/models--{imagine.MODEL.replace('/', '--')}/snapshots/abc/unet",
                f"hub/models--{imagine.DECODER.replace('/', '--')}/snapshots/abc"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / f"hub/models--{imagine.MODEL.replace('/', '--')}/snapshots/abc/unet"
                / "diffusion_pytorch_model.fp16.safetensors").write_bytes(b"")
    (tmp_path / f"hub/models--{imagine.DECODER.replace('/', '--')}/snapshots/abc"
                / "diffusion_pytorch_model.safetensors").write_bytes(b"")
    assert imagine.ready() is True
