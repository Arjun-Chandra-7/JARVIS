"""Heavy models go where they fit: a nearly-full card sends speech recognition to the processor
before an utterance is lost, not after."""
from __future__ import annotations

import subprocess

import pytest

from jarvis import resources
from jarvis.audio import local_stt


@pytest.fixture(autouse=True)
def _fresh():
    resources.forget()
    yield
    resources.forget()


def smi(monkeypatch, text: str, code: int = 0):
    monkeypatch.setattr(resources.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=text, stderr=""))


def test_room_is_measured_from_the_card(monkeypatch):
    smi(monkeypatch, "3900, 4096\n")
    assert not resources.room_on_gpu("small")
    resources.forget()
    smi(monkeypatch, "1200, 4096\n")
    assert resources.room_on_gpu("small")


def test_no_driver_means_no_room(monkeypatch):
    smi(monkeypatch, "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.", 9)
    assert resources.gpu_memory() is None and not resources.room_on_gpu("tiny")


class Model:
    made: list = []

    def __init__(self, name, device, compute_type):
        Model.made.append(device)


@pytest.mark.parametrize("room,device", [(False, "cpu"), (True, "cuda")])
def test_speech_recognition_goes_where_it_fits(monkeypatch, room, device):
    import faster_whisper

    Model.made = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    monkeypatch.setattr(local_stt, "_cuda_usable", lambda: True)
    monkeypatch.setattr(resources, "room_on_gpu", lambda name, margin_mib=150: room)
    monkeypatch.setattr(local_stt, "_models", {})
    local_stt._get_model("small")
    assert Model.made == [device]
