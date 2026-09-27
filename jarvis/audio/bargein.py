"""Is the person talking over Jarvis — or is that Jarvis, or the video?

Barge-in used to be loudness alone: ~1 s of sound above 1.9x the echo measured in the first
0.6 s of playback. Two failures came out of that. A lecture playing aloud is louder than Jarvis's
echo, so long answers were cut off halfway — the fix was to stand barge-in down entirely while
any player was playing. And a second of sustained sound is far too long to feel like being
listened to: people stop talking over someone who does not stop.

What decides now, frame by frame:

* **Is it speech?** Silero's probability, not loudness — a fan, keys or a door are loud and not
  speech.
* **Is it louder than what is already in the room?** The background — Jarvis's own echo, the
  video — is tracked continuously from the frames that did not look like the person, and speech
  must clear it by a margin. With echo cancellation active the background is small (the echo and
  the video have been subtracted) and the margin can be small; without it, and with a video
  playing, it has to be large.
* **For long enough?** Two 80 ms frames with echo cancellation (onset → trigger 160–240 ms),
  three without, four with a video playing and no cancellation.

Pure: frames and probabilities in, a decision out. The voice session does the reading.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass(frozen=True)
class Policy:
    frames_needed: int
    margin: float           # speech RMS must be this many times the background
    probability: float      # Silero speech probability, averaged over the frame


def policy(aec: bool, media: bool) -> Policy:
    if aec:
        return Policy(frames_needed=2, margin=1.6, probability=0.6)
    if media:
        return Policy(frames_needed=4, margin=2.6, probability=0.7)
    return Policy(frames_needed=3, margin=2.0, probability=0.6)


def rms(frame) -> float:
    a = np.asarray(frame, dtype=np.float32) / 32768.0
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


@dataclass
class BargeInDetector:
    policy: Policy
    frame_s: float = 0.08
    floor_min: float = 0.004          # an empty, quiet room: speech still has to be speech-loud
    history: int = 24                 # ~2 s of background frames at 80 ms
    _background: deque = field(default_factory=lambda: deque(maxlen=24))
    _run: int = 0
    _run_started: float = 0.0
    warmup_frames: int = 3            # after the first audio: the echo arrives, learn it first
    onset_at: float = 0.0             # when the speech that triggered began
    triggered_at: float = 0.0
    armed: bool = True
    _warm: int = 0

    def arm(self) -> None:
        """Jarvis's audio has started. The next few frames are its echo arriving, whatever
        Silero thinks of them — they set the background before anything can trigger."""
        self.armed = True
        self._warm = self.warmup_frames
        self._run = 0

    def background(self) -> float:
        if not self._background:
            return self.floor_min
        return max(self.floor_min, float(np.percentile(self._background, 80)))

    def feed(self, level: float, probability: float, now: float) -> bool:
        """One frame's RMS and speech probability. True on the frame that decides it is the
        person talking. After a trigger it keeps returning False."""
        if self.triggered_at:
            return False
        if not self.armed or self._warm > 0:
            self._warm = max(0, self._warm - 1)
            self._background.append(level)
            return False
        bar = self.background() * self.policy.margin
        if probability >= self.policy.probability and level >= bar:
            if self._run == 0:
                self._run_started = now - self.frame_s     # the frame began a frame ago
            self._run += 1
            if self._run >= self.policy.frames_needed:
                self.onset_at = self._run_started
                self.triggered_at = now
                return True
            return False
        # Not the person: this frame is what the room sounds like right now.
        self._run = 0
        self._background.append(level)
        return False

    @property
    def decided_in_s(self) -> Optional[float]:
        """Onset → decision, the detector's share of the barge-in latency."""
        return self.triggered_at - self.onset_at if self.triggered_at else None
