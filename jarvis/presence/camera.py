"""Webcam presence sensor — the only source on this laptop that can give a real bearing.

Uses OpenCV's YuNet face detector, which returns five landmarks per face. The two eye
landmarks give an interpupillary distance in pixels, and IPD is a tight anthropometric
constant, so range comes out metric without per-user calibration (see geometry.py).

The camera is opened lazily and released when the sensor stops, so the webcam LED is
only lit while presence sensing is actually enabled.
"""
from __future__ import annotations

import glob
import math
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from . import geometry as geo
from .types import Contact, SensorStatus

MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
MODEL_PATH = Path(os.environ.get(
    "JARVIS_YUNET_MODEL", "~/.local/share/jarvis/models/yunet.onnx")).expanduser()

# A laptop exposes several /dev/videoN nodes (capture + metadata), and they renumber
# whenever the USB device re-enumerates. Discover a node that actually yields frames
# rather than assuming index 0. JARVIS_CAMERA_INDEX pins one explicitly.
_DEVICE_ENV = os.environ.get("JARVIS_CAMERA_INDEX")
_DEVICE = int(_DEVICE_ENV) if _DEVICE_ENV and _DEVICE_ENV.lstrip("-").isdigit() else None
_WIDTH = int(os.environ.get("JARVIS_CAMERA_WIDTH", "640"))
_HEIGHT = int(os.environ.get("JARVIS_CAMERA_HEIGHT", "480"))
_FPS = float(os.environ.get("JARVIS_PRESENCE_FPS", "4"))       # low: this runs all day
_SCORE = float(os.environ.get("JARVIS_FACE_SCORE", "0.75"))
DARK_LEVEL = float(os.environ.get("JARVIS_CAMERA_DARK_LEVEL", "8"))  # mean 0-255 below which no face can exist

# Some laptop webcams are mounted inverted and the driver does not correct it. A face
# detector finds nothing at all in a 180-rotated frame, so orientation is auto-detected
# by trying each rotation until one produces a face. "auto" | 0 | 90 | 180 | 270.
_ROTATE_ENV = os.environ.get("JARVIS_CAMERA_ROTATE", "auto").strip().lower()
ROTATIONS = (0, 180, 90, 270)   # most likely first


def ensure_model(timeout: float = 60.0) -> bool:
    """Fetch the 230 KB YuNet model once. Returns True when it is on disk."""
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 50_000:
        return True
    try:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".tmp")
        with urllib.request.urlopen(MODEL_URL, timeout=timeout) as r, tmp.open("wb") as f:
            f.write(r.read())
        if tmp.stat().st_size < 50_000:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(MODEL_PATH)
        return True
    except Exception:  # noqa: BLE001 - offline is a normal, non-fatal state
        return False


def candidate_indices() -> list[int]:
    """Video node numbers to try, lowest first. Honours an explicit override."""
    if _DEVICE is not None:
        return [_DEVICE]
    found = sorted(int(m.group(1)) for m in
                   (re.match(r"/dev/video(\d+)$", p) for p in sorted(glob.glob("/dev/video*")))
                   if m)
    return found or [0]


def open_capture(cv2, warmup: int = 3):
    """Open the first node that genuinely delivers a frame. Returns (capture, index)."""
    for index in candidate_indices():
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, _WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _HEIGHT)
        ok = False
        for _ in range(warmup):                    # metadata nodes open but never yield a frame
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        if ok:
            return cap, index
        cap.release()
    return None, None


def _rotate(cv2, frame, degrees: int):
    if degrees == 0:
        return frame
    return cv2.rotate(frame, {90: cv2.ROTATE_90_CLOCKWISE,
                              180: cv2.ROTATE_180,
                              270: cv2.ROTATE_90_COUNTERCLOCKWISE}[degrees])


def detect_rotation(cv2, cap, model_path, attempts: int = 6) -> Optional[int]:
    """Find the rotation that actually yields a face. None if no orientation does."""
    if _ROTATE_ENV.isdigit():
        return int(_ROTATE_ENV) % 360
    for _ in range(attempts):
        ok, frame = cap.read()
        if not ok or frame is None or frame.mean() < DARK_LEVEL:
            continue
        for degrees in ROTATIONS:
            turned = _rotate(cv2, frame, degrees)
            h, w = turned.shape[:2]
            det = cv2.FaceDetectorYN_create(str(model_path), "", (w, h), score_threshold=0.5)
            det.setInputSize((w, h))
            _, faces = det.detect(turned)
            if faces is not None and len(faces):
                return degrees
        time.sleep(0.15)
    return None


def _ipd_px(landmarks) -> Optional[float]:
    """Eye-to-eye separation. YuNet landmark order: right eye, left eye, nose, mouth x2."""
    try:
        (rx, ry), (lx, ly) = landmarks[0], landmarks[1]
        d = math.hypot(float(lx) - float(rx), float(ly) - float(ry))
        return d if d > 1.0 else None
    except Exception:  # noqa: BLE001
        return None


class CameraSensor:
    """Polls the webcam in a background thread and publishes located Contacts."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._contacts: list[Contact] = []
        self._status = SensorStatus("camera", False, "not started")
        self._next_id = 0

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="presence-camera", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            self._contacts = []
            self._status = SensorStatus("camera", False, "stopped")

    def snapshot(self) -> tuple[list[Contact], SensorStatus]:
        with self._lock:
            return list(self._contacts), self._status

    # --- worker ---------------------------------------------------------------
    def _set_status(self, ok: bool, detail: str) -> None:
        with self._lock:
            self._status = SensorStatus("camera", ok, detail)

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self._set_status(False, "opencv not installed (pip install opencv-python-headless)")
            return
        if not ensure_model():
            self._set_status(False, "YuNet model unavailable (offline?)")
            return

        cap, index = open_capture(cv2)
        if cap is None:
            self._set_status(False, "no capture-capable camera (busy, missing, or blocked)")
            return
        rotation = detect_rotation(cv2, cap, MODEL_PATH)
        # No orientation produced a face yet — that usually means nobody is in shot, so
        # start unrotated and re-check occasionally rather than refusing to run.
        confirmed = rotation is not None
        rotation = rotation or 0
        probe_ok, probe = cap.read()
        if probe_ok and probe is not None:
            turned = _rotate(cv2, probe, rotation)
            height, width = turned.shape[:2]
        else:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or _WIDTH
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or _HEIGHT
        detector = cv2.FaceDetectorYN_create(str(MODEL_PATH), "", (width, height),
                                             score_threshold=_SCORE)
        detector.setInputSize((width, height))   # YuNet finds nothing if this mismatches
        self._set_status(True, f"video{index} {width}x{height} @{_FPS:g}fps, "
                               f"rot {rotation}deg{'' if confirmed else '?'}, hfov {geo.hfov_deg():.0f}deg")

        period = 1.0 / max(0.5, _FPS)
        empty_runs = 0
        read_failures = 0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    # This webcam re-enumerates when it resumes from USB autosuspend, which
                    # moves its /dev/videoN node out from under us. Reopen and rediscover
                    # rather than sitting on a dead handle for the rest of the session.
                    read_failures += 1
                    self._set_status(False, f"frame read failed ({read_failures})")
                    if read_failures >= 3:
                        cap.release()
                        time.sleep(1.0)
                        cap, index = open_capture(cv2)
                        if cap is None:
                            self._set_status(False, "camera vanished — reopening")
                            time.sleep(3.0)
                            cap, index = open_capture(cv2)
                            if cap is None:
                                return
                        read_failures = 0
                    time.sleep(0.5)
                    continue
                read_failures = 0
                frame = _rotate(cv2, frame, rotation)
                # A near-black frame means the shutter is closed or the room is dark.
                # Say so — silently reporting "nobody here" would be a lie.
                brightness = float(frame.mean())
                if brightness < DARK_LEVEL:
                    self._publish([])
                    self._set_status(False, f"no light (mean {brightness:.0f}/255) — "
                                            "privacy shutter closed or room dark")
                    time.sleep(1.0)
                    continue
                found = self._detect(detector, frame, width)
                self._publish(found)
                # Still unsure of orientation and seeing nobody? Re-probe now and then;
                # an inverted webcam is invisible to the detector until it is corrected.
                if found:
                    confirmed, empty_runs = True, 0
                elif not confirmed:
                    empty_runs += 1
                    if empty_runs >= int(_FPS) * 20:
                        empty_runs = 0
                        again = detect_rotation(cv2, cap, MODEL_PATH, attempts=3)
                        if again is not None and again != rotation:
                            rotation, confirmed = again, True
                            probe_ok, probe = cap.read()
                            if probe_ok and probe is not None:
                                height, width = _rotate(cv2, probe, rotation).shape[:2]
                                detector.setInputSize((width, height))
                self._set_status(True, f"video{index} {width}x{height} @{_FPS:g}fps, "
                                       f"rot {rotation}deg{'' if confirmed else '?'}, "
                                       f"hfov {geo.hfov_deg():.0f}deg")
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            cap.release()
            self._set_status(False, "stopped")

    def _detect(self, detector, frame, width: int) -> list[Contact]:
        _, faces = detector.detect(frame)
        out: list[Contact] = []
        if faces is None:
            return out
        now = time.time()
        for face in faces:
            x, y, w, h = (float(v) for v in face[:4])
            score = float(face[-1])
            landmarks = [(face[4 + 2 * i], face[5 + 2 * i]) for i in range(5)]
            ipd = _ipd_px(landmarks)
            if ipd:
                distance = geo.distance_from_ipd(ipd, width)
                from_ipd = True
            elif w > 1:
                distance = geo.distance_from_width(w, width)
                from_ipd = False
            else:
                continue
            # A face further than ~8 m on a laptop webcam is a false positive, not a person.
            if not 0.25 <= distance <= 8.0:
                continue
            bearing = geo.bearing_deg(x + w / 2.0, width)
            self._next_id += 1
            out.append(Contact(
                id=f"cam-{self._next_id}",
                source="camera",
                distance_m=distance,
                bearing_deg=bearing,
                confidence=round(min(1.0, geo.range_confidence(distance, from_ipd) * score), 3),
                first_seen=now, last_seen=now,
                detail=f"face score {score:.2f}, {'ipd' if from_ipd else 'box'} range",
            ))
        return out

    def _publish(self, contacts: list[Contact]) -> None:
        with self._lock:
            self._contacts = contacts


_sensor: Optional[CameraSensor] = None


def sensor() -> CameraSensor:
    global _sensor
    if _sensor is None:
        _sensor = CameraSensor()
    return _sensor


def describe() -> dict[str, Any]:
    """Cheap capability report for diagnostics."""
    return {"device": _DEVICE, "candidates": candidate_indices(), "size": [_WIDTH, _HEIGHT], "fps": _FPS,
            "model": str(MODEL_PATH), "model_present": MODEL_PATH.exists(),
            "hfov_deg": geo.hfov_deg()}
