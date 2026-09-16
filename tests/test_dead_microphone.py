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


# ------------------------------------------------------- the microphone that is far too loud
def test_a_pinned_window_is_saturated():
    """Measured after a headset reconnect: peak 1.01, rms 0.78, 18% of samples at full scale,
    on every input on the machine. Silero declines to call it speech, so nothing is captured."""
    heard = inputs.Heard()
    for _ in range(40):
        heard.note(1.01)
    assert inputs.verdict(heard) == "saturated"


def test_a_clap_in_a_normal_utterance_is_not_saturation():
    """One pinned frame is a door slamming; all of them is a fault."""
    heard = inputs.Heard()
    for i in range(40):
        heard.note(1.0 if i < 3 else 0.06)
    assert inputs.verdict(heard) == ""


def test_ordinary_speech_is_not_saturation():
    heard = inputs.Heard()
    for _ in range(40):
        heard.note(0.3)
    assert inputs.verdict(heard) == ""


def test_saturation_is_the_only_fault_provable_without_anyone_speaking():
    """An idle probe of a healthy microphone in a quiet room is silent by definition. Treating
    that as a fault would restart the audio server every time the house went quiet."""
    quiet = inputs.Heard()
    for _ in range(20):
        quiet.note(0.00001)
    assert inputs.verdict(quiet) == "silent"
    assert inputs.broken_without_anyone_speaking(quiet) is False

    loud = inputs.Heard()
    for _ in range(20):
        loud.note(1.01)
    assert inputs.broken_without_anyone_speaking(loud) is True


def test_the_saturated_advice_says_it_is_being_dealt_with(monkeypatch):
    monkeypatch.setattr(inputs, "describe", lambda _i=-1: "Built-in Analog Stereo")
    heard = inputs.Heard()
    for _ in range(20):
        heard.note(1.01)
    said = inputs.explain(heard)
    assert "Built-in Analog Stereo" in said and "reset the audio" in said


def test_only_gain_that_is_pinned_at_maximum_is_touched(monkeypatch):
    """A microphone someone deliberately set low is left where they put it — this undoes a
    profile reset, it does not overrule a person."""
    calls = []

    class _Run:
        def __init__(self, out, code=0):
            self.stdout, self.returncode = out, code

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "scontrols" in cmd:
            return _Run("Simple mixer control 'Capture',0\nSimple mixer control 'Internal Mic Boost',0\n")
        if "sget" in cmd:
            return _Run("  Front Left: Capture 30 [45%] [12.00dB] [on]\n")   # not pinned
        return _Run("", 0)

    monkeypatch.setattr(inputs, "_capture_cards", lambda: ["1"])
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert inputs.calm_the_gain() is False
    assert not any("sset" in c for c in calls), "changed a gain nobody had pinned"


def test_gain_pinned_at_maximum_is_turned_down(monkeypatch):
    sset = []

    class _Run:
        def __init__(self, out, code=0):
            self.stdout, self.returncode = out, code

    def fake_run(cmd, **kw):
        if "scontrols" in cmd:
            return _Run("Simple mixer control 'Capture',0\nSimple mixer control 'Internal Mic Boost',0\n")
        if "sget" in cmd:
            return _Run("  Front Left: Capture 63 [100%] [30.00dB] [on]\n")
        if "sset" in cmd:
            sset.append(cmd)
        return _Run("", 0)

    monkeypatch.setattr(inputs, "_capture_cards", lambda: ["1"])
    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert inputs.calm_the_gain() is True
    assert any(inputs.NO_BOOST in c for c in sset)
    assert any(inputs.QUIET_CAPTURE in c for c in sset)


def test_recovery_stops_at_the_first_rung_that_works(monkeypatch):
    """A rung that did not help must not be reported as the fix."""
    monkeypatch.setattr(inputs, "refresh_devices", lambda: None)
    # The first look after reopening finds a healthy device, so the ladder must stop there.
    verdicts = iter([False])
    monkeypatch.setattr(inputs, "sample", lambda *a, **k: inputs.Heard())
    monkeypatch.setattr(inputs, "broken_without_anyone_speaking",
                        lambda _h: next(verdicts, True))
    restarted = []
    monkeypatch.setattr(inputs, "restart_audio_server", lambda: restarted.append(1) or True)
    ok, what = inputs.recover()
    assert ok and what == "reopened the microphone"
    assert not restarted, "restarted the audio server when reopening had already worked"


def test_recovery_admits_when_nothing_worked(monkeypatch):
    monkeypatch.setattr(inputs, "refresh_devices", lambda: None)
    monkeypatch.setattr(inputs, "sample", lambda *a, **k: inputs.Heard())
    monkeypatch.setattr(inputs, "broken_without_anyone_speaking", lambda _h: True)
    monkeypatch.setattr(inputs, "calm_the_gain", lambda: False)
    monkeypatch.setattr(inputs, "restart_audio_server", lambda: False)
    ok, what = inputs.recover()
    assert ok is False and "could not" in what
