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

    def note(self, level: float) -> None:
        self.frames += 1
        self.peak = max(self.peak, float(level))

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


def verdict(heard: "Heard") -> str:
    """"silent", "too quiet", or "" when the microphone was not the problem."""
    if not heard.frames:
        return ""
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
