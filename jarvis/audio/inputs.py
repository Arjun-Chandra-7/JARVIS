"""Which microphone Jarvis is actually listening to, and whether anything is coming out of it.

Written after an evening of "Sorry sir, I didn't catch that." The log said the same thing six
times — the wake word fired, then `no audio captured` — and the cause was not in Jarvis at all:

    alsa_input.usb-Yichip (a USB headset)  ->  EasyEffects  ->  Jarvis
    alsa_input.pci-0000_06_00.6 (the laptop's own microphone)  ->  nothing

The system default input had become a USB headset that was not being worn, with an effects
processor in front of it. Jarvis was listening perfectly, to a microphone in a drawer.

Nothing here changes any of that. What it does is make the failure legible: a microphone that
delivers digital silence for a whole utterance is not a person who mumbled, and saying "I didn't
catch that" sends someone off to repeat themselves more loudly at a device that is not connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Below this, a frame carries no signal at all — not a quiet room, which still has a noise floor
# an order of magnitude above it, but nothing. Measured on this machine: a live microphone in a
# silent room reads about 0.0013 peak; a device with nothing arriving reads 0.0000.
SILENT_PEAK = 0.0004


@dataclass
class Heard:
    """What the microphone delivered across one listening window."""
    frames: int = 0
    peak: float = 0.0
    loud: int = 0           # frames pinned at full scale — one is a clap, all of them is a fault

    def note(self, level: float) -> None:
        self.frames += 1
        level = float(level)
        self.peak = max(self.peak, level)
        if level >= LOUD_FRAME:
            self.loud += 1

    @property
    def silent(self) -> bool:
        """True when the device delivered frames and every one of them was empty."""
        return self.frames > 0 and self.peak < SILENT_PEAK


def describe(device_index: int = -1) -> str:
    """A name for the input Jarvis is using, as a person would recognise it."""
    try:
        import sounddevice as sd

        info = sd.query_devices(None if device_index < 0 else device_index, kind="input")
        name = str(info.get("name", "")).strip()
    except Exception:  # noqa: BLE001 — never let naming a device break a turn
        return "the default microphone"
    if not name or name in ("default", "pulse", "pipewire"):
        # "default" tells the user nothing. What it resolves to is worth the extra call.
        resolved = _what_default_resolves_to()
        return resolved or "the default microphone"
    return name


def _what_default_resolves_to() -> str:
    """The real device behind "default", asked of the audio server rather than guessed."""
    try:
        import subprocess

        out = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=4).stdout
    except Exception:  # noqa: BLE001
        return ""
    inside = False
    for line in out.splitlines():
        if "Sources:" in line:
            inside = True
            continue
        if inside:
            if not line.strip(" │├└─"):
                break
            if "*" in line:
                # " │  *  201. USB-Audio Mono   [vol: 1.00]"
                name = line.split(".", 1)[-1]
                return name.split("[")[0].strip() or ""
    return ""


def advice(device_index: int = -1) -> str:
    """What to say when the microphone delivered nothing at all."""
    name = describe(device_index)
    other = _a_different_input(name)
    hint = f" Something else is plugged in — {other} — if that is the one you meant." if other else ""
    return (f"I'm listening to {name} and it isn't picking anything up, sir — "
            f"not quiet, silent.{hint}")


# Names that are not a microphone: the audio server's own aliases, which all resolve back to
# whichever device is already being used, and the loopbacks that carry the speakers rather than
# a microphone. Offering "Default Source" as an alternative to the default is no help at all.
_NOT_A_MICROPHONE = ("default", "pulse", "pipewire", "sysdefault", "default source",
                     "default sink", "spdif", "hdmi", "monitor", "loopback")


def _a_different_input(besides: str) -> Optional[str]:
    """Another real input device, so the advice can name an alternative rather than gesture."""
    try:
        import sounddevice as sd

        candidates = []
        for info in sd.query_devices():
            name = str(info.get("name", "")).strip()
            if info.get("max_input_channels", 0) < 1 or not name or name == besides:
                continue
            if any(word in name.lower() for word in _NOT_A_MICROPHONE):
                continue
            candidates.append(name)
        # A built-in microphone is the likeliest thing someone actually meant when the default
        # has wandered off to a headset, so it is offered first when there is one.
        for name in candidates:
            if any(word in name.lower() for word in ("analog", "internal", "built", "pci")):
                return name
        return candidates[0] if candidates else None
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------- too quiet to hear
# Speech that reaches the endpointer below this never starts an utterance. Measured on this
# machine while chasing "no audio captured": a spoken sentence arriving through the current chain
# peaked at 0.014 and was never detected, while the same chain passes a test tone perfectly. A
# microphone can be working, unmuted, at full gain, and still be too far away or too insensitive
# to talk to — which looks identical to being broken and is fixed completely differently.
TOO_QUIET_PEAK = 0.02

# The other way a microphone fails: not too little but far too much. Measured on this machine
# after a Bluetooth headset disconnected and reconnected — every input on the box, built-in and
# USB alike, at both 16 and 48 kHz:
#
#     peak 1.01   rms 0.78   18-22% of samples at full scale, broadband, no words in it
#
# Real speech peaks near full scale occasionally and never sustains it; a device in this state
# does nothing else. Silero declines to call it speech, correctly, so nothing is ever captured
# and the only sign is that the loudest frame is pinned.
LOUD_FRAME = 0.9            # a frame that is essentially pinned to full scale
SATURATED_SHARE = 0.5       # ...and most of the window looks like that


def verdict(heard: "Heard") -> str:
    """"silent", "too quiet", "saturated", or "" when the microphone was not the problem."""
    if not heard.frames:
        return ""
    if heard.loud / heard.frames >= SATURATED_SHARE:
        return "saturated"
    if heard.peak < SILENT_PEAK:
        return "silent"
    if heard.peak < TOO_QUIET_PEAK:
        return "too quiet"
    return ""


def explain(heard: "Heard", device_index: int = -1) -> str:
    """What to say about a window that captured nothing, or "" to use the ordinary apology."""
    what = verdict(heard)
    if not what:
        return ""
    name = describe(device_index)
    if what == "saturated":
        return (f"{name} is deafening me, sir — it's pinned at full volume and nothing in it is "
                f"speech. I'll reset the audio and try again.")
    if what == "silent":
        other = _a_different_input(name)
        hint = f" {other} is also connected, if that is the one you meant." if other else ""
        return (f"I'm listening to {name} and it isn't picking anything up, sir — "
                f"not quiet, silent.{hint}")
    return (f"I can hear {name}, sir, but only just — your voice is arriving far too faintly "
            f"to make out. Try speaking closer to it, or run mic-check to see which microphone "
            f"hears you best.")


# --------------------------------------------------------------------------- the check itself
@dataclass
class Reading:
    name: str
    peak: float = 0.0
    rms: float = 0.0
    error: str = ""

    @property
    def usable(self) -> bool:
        return not self.error and self.peak >= TOO_QUIET_PEAK


def real_inputs() -> list[tuple[int, str]]:
    """Every device that could plausibly be a microphone, as (index, name)."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return []
    found = []
    for index, info in enumerate(sd.query_devices()):
        name = str(info.get("name", "")).strip()
        if info.get("max_input_channels", 0) < 1 or not name:
            continue
        if any(word in name.lower() for word in _NOT_A_MICROPHONE):
            continue
        found.append((index, name))
    return found


def _measure_with_pipewire(seconds: float) -> list[Reading]:
    """Read every input through PipeWire, which does not mind that Jarvis is already listening.

    PortAudio opens the device exclusively enough that, while the voice service holds it, every
    stream comes back as exactly 0.0000 — not an error, just silence, which reads as "your
    microphone is dead" and sends someone off to fix a microphone that is working perfectly.
    pw-record shares, so the check can be run without stopping anything.
    """
    import json
    import subprocess
    import tempfile
    import threading
    import wave

    try:
        dump = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=8).stdout
        objects = json.loads(dump)
    except Exception:  # noqa: BLE001
        return []

    sources = []
    for o in objects:
        if not str(o.get("type", "")).endswith("Node"):
            continue
        props = o.get("info", {}).get("props", {})
        if props.get("media.class") != "Audio/Source":
            continue
        name = props.get("node.description") or props.get("node.name") or str(o["id"])
        if any(word in str(name).lower() for word in _NOT_A_MICROPHONE):
            continue
        sources.append((o["id"], str(name)))
    if not sources:
        return []

    readings: dict[int, Reading] = {}

    def record(node_id: int, name: str) -> None:
        reading = Reading(name=name)
        readings[node_id] = reading
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            try:
                proc = subprocess.Popen(
                    ["pw-record", f"--target={node_id}", "--rate=16000", "--channels=1",
                     "--format=s16", f.name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                import time as _t
                _t.sleep(seconds)
                proc.send_signal(2)         # SIGINT: pw-record finalises the file on it
                proc.wait(timeout=5)
                import numpy as np

                with wave.open(f.name) as w:
                    raw = w.readframes(w.getnframes())
                if len(raw) < 1000:
                    reading.error = "no audio"
                    return
                a = np.frombuffer(raw, "<i2").astype("float32") / 32768.0
                a = a - a.mean()
                reading.peak = float(abs(a).max())
                reading.rms = float(np.sqrt((a ** 2).mean()))
            except Exception as exc:  # noqa: BLE001
                reading.error = f"{type(exc).__name__}: {str(exc)[:50]}"

    threads = [threading.Thread(target=record, args=pair, daemon=True) for pair in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=seconds + 8)
    return [readings[i] for i in sorted(readings)]


def measure(seconds: float = 5.0) -> list[Reading]:
    """Listen on every input at once and report what each one heard.

    All of them together rather than one after another, because the point is to compare what they
    heard of the *same* words — asked to speak once per device, nobody says it the same way twice,
    and the comparison stops meaning anything.
    """
    import shutil
    import threading

    if shutil.which("pw-record"):
        through_pipewire = _measure_with_pipewire(seconds)
        if through_pipewire:
            return through_pipewire

    readings: dict[int, Reading] = {}
    threads = []

    def listen(index: int, name: str) -> None:
        reading = Reading(name=name)
        readings[index] = reading
        try:
            import numpy as np
            import sounddevice as sd

            data = sd.rec(int(seconds * 16000), samplerate=16000, channels=1,
                          device=index, dtype="float32")
            sd.wait()
            a = data.flatten()
            a = a - a.mean()
            reading.peak = float(abs(a).max())
            reading.rms = float(np.sqrt((a ** 2).mean()))
        except Exception as exc:  # noqa: BLE001 — a device that will not open is a result too
            reading.error = f"{type(exc).__name__}: {str(exc)[:60]}"

    for index, name in real_inputs():
        thread = threading.Thread(target=listen, args=(index, name), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        # Generous: a device that is going to block will block, and the daemon thread is
        # abandoned rather than holding up the report on every other device.
        thread.join(timeout=seconds + 3)
    return [readings[i] for i in sorted(readings)]


# --------------------------------------------------------------------------- putting it right
# A laptop's microphone is not one fixed thing. Over an afternoon it can be a Bluetooth headset,
# then the built-in one because the headset wandered out of range, then a wired pair, then a
# second headset. Every one of those is a device appearing or disappearing underneath a stream
# that was opened once, at startup, and never reconsidered.
#
# Measured after one such reconnect: every input on the machine — built-in and USB, 16 kHz and
# 48 kHz, through PipeWire and through PortAudio — returned full-scale broadband noise. Not a
# stale device index and not the channel count: the audio server itself was in a bad state, and
# restarting it returned the very same devices to peak 0.07 with nothing clipped.
#
# So the ladder below climbs only as far as it has to, and each rung is checked rather than
# assumed to have worked.
AUDIO_UNITS = ("wireplumber", "pipewire", "pipewire-pulse")

# Long enough to judge, short enough that nobody notices it happening between turns.
PROBE_S = 0.6


def sample(device_index: int = -1, seconds: float = PROBE_S) -> Heard:
    """Listen briefly to whatever is currently selected and report what arrived."""
    heard = Heard()
    try:
        import numpy as np
        import sounddevice as sd

        device = None if device_index < 0 else device_index
        channels = 2 if _channels_of(sd, device) >= 2 else 1
        data = sd.rec(int(seconds * 16000), samplerate=16000, channels=channels,
                      device=device, dtype="float32")
        sd.wait()
        block = data[:, 0]
        for i in range(0, len(block) - 512, 512):
            heard.note(float(abs(block[i:i + 512]).max()))
    except Exception:  # noqa: BLE001 — an unreadable device is reported as "nothing arrived"
        return Heard()
    return heard


def _channels_of(sd, device) -> int:
    try:
        return int(sd.query_devices(device, kind="input")["max_input_channels"])
    except Exception:  # noqa: BLE001
        return 2


def refresh_devices() -> None:
    """Make PortAudio look at the world again.

    It reads the device list once, when it initialises, and a headset that connected since is
    simply not there as far as it is concerned — while the index it still believes in may now
    belong to something else entirely.
    """
    try:
        import sounddevice as sd

        sd._terminate()
        sd._initialize()
    except Exception:  # noqa: BLE001
        pass


# What actually goes wrong, and it is not exotic. The capture chain on this laptop is a +30 dB
# capture stage followed by a +30 dB "Internal Mic Boost", and at sixty decibels the microphone's
# own noise floor clips. Measured, same microphone, one minute apart:
#
#     capture 100%  boost 100%   peak 1.011  rms 0.769   clipping 18% of samples
#     capture 100%  boost   0%   peak 1.000  rms 0.122
#     capture  80%  boost   0%   peak 0.264  rms 0.026   clean, with headroom
#
# The reason it keeps coming back is that these are per-device settings, and the audio server
# reapplies a profile whenever the set of devices changes — which is every time a headset
# connects or drops. Nothing is broken; the gain is simply put back to maximum behind your back.
QUIET_CAPTURE = "80%"
NO_BOOST = "0%"
BOOST_CONTROLS = ("Internal Mic Boost", "Mic Boost", "Front Mic Boost")


def _capture_cards() -> list[str]:
    """Card indices that have a capture control worth touching."""
    import subprocess

    cards = []
    try:
        with open("/proc/asound/cards") as f:
            for line in f:
                head = line.strip().split(maxsplit=1)
                if head and head[0].isdigit():
                    cards.append(head[0])
    except Exception:  # noqa: BLE001
        return []
    keep = []
    for card in cards:
        try:
            out = subprocess.run(["amixer", "-c", card, "scontrols"],
                                 capture_output=True, text=True, timeout=5).stdout
        except Exception:  # noqa: BLE001
            continue
        if "Capture" in out or any(b in out for b in BOOST_CONTROLS):
            keep.append(card)
    return keep


def calm_the_gain() -> bool:
    """Take the boost off the capture chain. True when something was actually changed.

    Deliberately only touches gain that is pinned at maximum. A microphone someone has set
    deliberately low is left where they put it — this is here to undo a profile reset, not to
    overrule a person.
    """
    import subprocess

    changed = False
    for card in _capture_cards():
        for control in BOOST_CONTROLS:
            try:
                now = subprocess.run(["amixer", "-c", card, "sget", control],
                                     capture_output=True, text=True, timeout=5)
                if now.returncode != 0 or "100%" not in now.stdout:
                    continue
                done = subprocess.run(["amixer", "-c", card, "sset", control, NO_BOOST],
                                      capture_output=True, timeout=5)
                changed = changed or done.returncode == 0
            except Exception:  # noqa: BLE001
                continue
        try:
            now = subprocess.run(["amixer", "-c", card, "sget", "Capture"],
                                 capture_output=True, text=True, timeout=5)
            if now.returncode == 0 and "100%" in now.stdout:
                done = subprocess.run(["amixer", "-c", card, "sset", "Capture", QUIET_CAPTURE],
                                      capture_output=True, timeout=5)
                changed = changed or done.returncode == 0
        except Exception:  # noqa: BLE001
            pass
    return changed


def restart_audio_server() -> bool:
    """Restart the user's audio server. The heavy rung, and the one that actually worked.

    User-scoped and quick — a few seconds, and only this login's audio — but it interrupts
    anything playing, so it is the last thing tried rather than the first.
    """
    import subprocess

    try:
        done = subprocess.run(["systemctl", "--user", "restart", *AUDIO_UNITS],
                              capture_output=True, timeout=25)
        return done.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def broken_without_anyone_speaking(heard: Heard) -> bool:
    """Whether a short idle listen proves the device is faulty.

    Only saturation proves it. An idle probe of a healthy microphone in a quiet room is
    near-silent by definition, so "silent" and "too quiet" cannot tell a broken device from a
    room with nobody in it — they are only meaningful across a window where someone did speak.
    Using them here would have Jarvis restart the audio server every time the house went quiet.
    """
    return verdict(heard) == "saturated"


def recover(device_index: int = -1, note=None) -> tuple[bool, str]:
    """Try to get a usable microphone back. Returns (healthy, what was done).

    Each rung is followed by a fresh listen, because a rung that did not help must not be
    reported as a fix — that is how "I restarted the audio" ends up in a log beside a microphone
    that is still broken.
    """
    def say(message: str) -> None:
        if note is not None:
            note(message)

    import time

    say("microphone is unusable — reopening it")
    refresh_devices()
    time.sleep(0.3)
    if not broken_without_anyone_speaking(sample(device_index)):
        return True, "reopened the microphone"

    say("still unusable — taking the boost off the capture gain")
    if calm_the_gain():
        time.sleep(0.5)
        if not broken_without_anyone_speaking(sample(device_index)):
            return True, "turned the microphone boost down"

    say("still unusable — restarting the audio server")
    if restart_audio_server():
        # The server needs a moment to bring the devices back before anything can be read.
        time.sleep(4.0)
        refresh_devices()
        if not broken_without_anyone_speaking(sample(device_index)):
            return True, "restarted the audio server"

    return False, "could not get a usable microphone back"
