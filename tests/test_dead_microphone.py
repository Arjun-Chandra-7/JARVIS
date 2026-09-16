"""A microphone that delivers nothing is not a person who mumbled.

From the log, six times in two sittings: the wake word fired, the endpointer captured nothing,
and Jarvis said "Sorry sir, I didn't catch that." The microphone was a USB headset that had
become the system default with an effects processor in front of it, while the laptop's own
microphone went nowhere. The apology sent someone off to repeat themselves, louder, at a device
that was not connected.
"""
from __future__ import annotations

import pytest

from jarvis.audio import inputs


def test_a_window_of_empty_frames_is_a_silent_device():
    heard = inputs.Heard()
    for _ in range(50):
        heard.note(0.00001)
    assert heard.silent is True


def test_a_quiet_room_is_not_a_silent_device():
    """Measured on this machine: a live microphone in a silent room reads about 0.0013 peak,
    an order of magnitude above a device delivering nothing."""
    heard = inputs.Heard()
    for _ in range(50):
        heard.note(0.0013)
    assert heard.silent is False


def test_speech_is_obviously_not_silence():
    heard = inputs.Heard()
    for level in (0.0002, 0.0004, 0.31, 0.44, 0.02):
        heard.note(level)
    assert heard.silent is False


def test_no_frames_at_all_is_not_a_verdict():
    """Nothing was measured, so nothing is claimed — the ordinary apology still applies."""
    assert inputs.Heard().silent is False


def test_the_peak_is_kept_not_the_last_reading():
    heard = inputs.Heard()
    heard.note(0.5)
    heard.note(0.00001)
    assert heard.silent is False


def test_the_advice_names_the_device_and_says_what_is_wrong(monkeypatch):
    monkeypatch.setattr(inputs, "describe", lambda _i=-1: "USB-Audio Mono")
    monkeypatch.setattr(inputs, "_a_different_input", lambda _b: "Built-in Analog Stereo")
    said = inputs.advice()
    assert "USB-Audio Mono" in said
    assert "silent" in said
    assert "Built-in Analog Stereo" in said


def test_the_advice_stands_alone_when_there_is_no_other_microphone(monkeypatch):
    monkeypatch.setattr(inputs, "describe", lambda _i=-1: "USB-Audio Mono")
    monkeypatch.setattr(inputs, "_a_different_input", lambda _b: None)
    said = inputs.advice()
    assert "USB-Audio Mono" in said and "if that is the one you meant" not in said


@pytest.mark.parametrize("name", [
    "default", "pulse", "pipewire", "Default Source",
    "alsa_output.pci-0000_06_00.6.analog-stereo.monitor",
])
def test_the_servers_own_aliases_are_not_offered_as_microphones(name, monkeypatch):
    """Offering "Default Source" as an alternative to the default is no help at all, and a
    monitor is the speakers rather than a microphone."""
    monkeypatch.setattr(inputs, "sounddevice", None, raising=False)
    import sounddevice as sd
    monkeypatch.setattr(sd, "query_devices",
                        lambda *a, **k: [{"name": name, "max_input_channels": 2}])
    assert inputs._a_different_input("something else") is None


def test_a_built_in_microphone_is_offered_before_anything_else(monkeypatch):
    """When the default has wandered off to a headset, the laptop's own microphone is the
    likeliest thing that was actually meant."""
    import sounddevice as sd
    monkeypatch.setattr(sd, "query_devices", lambda *a, **k: [
        {"name": "Some Other USB Thing", "max_input_channels": 1},
        {"name": "alsa_input.pci-0000_06_00.6.analog-stereo", "max_input_channels": 2},
    ])
    assert inputs._a_different_input("USB-Audio Mono") == "alsa_input.pci-0000_06_00.6.analog-stereo"


def test_naming_the_device_never_throws(monkeypatch):
    """Whatever the audio server is doing, it must not be able to break a turn."""
    import sounddevice as sd

    def explode(*a, **k):
        raise RuntimeError("no audio server")

    monkeypatch.setattr(sd, "query_devices", explode)
    assert inputs.describe() == "the default microphone"
    assert "the default microphone" in inputs.advice()


# --------------------------------------------------------------- asking for mono breaks capture
class _FakeSd:
    def __init__(self, most):
        self.most = most

    def query_devices(self, device=None, kind=None):
        if self.most is None:
            raise RuntimeError("no audio server")
        return {"max_input_channels": self.most}


@pytest.mark.parametrize("advertised, asked_for", [
    (128, 2),   # the audio server's catch-all "default" device
    (2, 2),     # an ordinary stereo capture device
    (32, 2),    # the "pulse" alias
    (1, 1),     # a headset microphone addressed directly, genuinely mono
    (0, 1),
])
def test_two_channels_are_asked_for_whenever_the_device_has_them(advertised, asked_for):
    """Asking the catch-all device for one channel returns garbage, not audio. Measured on the
    same stream in the same second: channel 0 was the room at peak 0.0005, channel 1 was a
    750 Hz tone at full scale, and a mono request came back as that tone at peak 0.599."""
    from jarvis.audio.mic import Microphone
    assert Microphone._native_channels(_FakeSd(advertised), None) == asked_for


def test_an_unqueryable_device_still_gets_two():
    """Whatever went wrong, one channel is the option known to return garbage."""
    from jarvis.audio.mic import Microphone
    assert Microphone._native_channels(_FakeSd(None), None) == 2


def test_the_frame_is_the_first_channel_never_a_mix_of_them():
    """Averaging looks like the careful thing to do and was tried: it put the full-scale tone
    from channel 1 over the microphone at half amplitude, peak 0.50, hiding the speech."""
    import numpy as np
    from jarvis.audio.mic import Microphone

    mic = Microphone.__new__(Microphone)          # no device, no stream
    mic.frame_length = 4
    mic.channels = 2

    class _Stream:
        def read(self, _n):
            room = np.array([10, -12, 9, -8], dtype="int16")
            tone = np.array([32000, -32000, 32000, -32000], dtype="int16")
            return np.stack([room, tone], axis=1), False

    mic._stream = _Stream()
    assert mic.read() == [10, -12, 9, -8]
