#!/usr/bin/env python3
"""Watch the human radar live, or calibrate the camera against a known distance.

  .venv/bin/python scripts/radar-check.py                 # live readout
  .venv/bin/python scripts/radar-check.py --seconds 30
  .venv/bin/python scripts/radar-check.py --calibrate 1.5 # stand 1.5 m away, one face

Calibration solves for the true focal length from one measured standoff and prints the
JARVIS_CAM_HFOV_DEG to put in .env. Without it the code assumes a typical 68 deg webcam,
which is usually within a few degrees but is still an assumption.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # run from anywhere


def _remote_snapshot():
    """Prefer the already-running backend: only one process can own the camera."""
    import os
    import urllib.request
    port = os.environ.get("JARVIS_WEB_PORT", "8770")
    try:
        import json
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/radar", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def live(seconds: float, sensors: str | None) -> int:
    import os
    if sensors:
        os.environ["JARVIS_PRESENCE"] = sensors

    from types import SimpleNamespace
    remote = _remote_snapshot() is not None and not sensors
    svc = None
    if remote:
        print(f"reading the live backend at :{os.environ.get('JARVIS_WEB_PORT', '8770')}"
              "  (it owns the camera)   ctrl-c to stop\n")
    else:
        from jarvis.config import CONFIG
        from jarvis.presence import service as presence
        svc = presence.start(CONFIG)
        print(f"local sensors: {sorted(presence.enabled_sensors())}   (ctrl-c to stop)\n")

    def _as_obj(d):
        contacts = [SimpleNamespace(**c) for c in d.get("contacts", [])]
        for c in contacts:
            c.distance_m = c.__dict__.get("distance_m")
            c.bearing_deg = c.__dict__.get("bearing_deg")
        return SimpleNamespace(people=d.get("people", 0), count=d.get("count", 0),
                               contacts=contacts,
                               sensors=[SimpleNamespace(**s) for s in d.get("sensors", [])])

    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            time.sleep(1.0)
            if remote:
                data = _remote_snapshot()
                if data is None:
                    print("backend went away")
                    break
                snap = _as_obj(data)
            else:
                snap = svc.snapshot()
            status = "  ".join(f"{s.name}={'ok' if s.ok else 'X'}" for s in snap.sensors)
            print(f"[{time.strftime('%H:%M:%S')}] people={snap.people} contacts={snap.count}   {status}")
            for c in snap.contacts:
                if c.bearing_deg is not None:
                    side = "ahead" if abs(c.bearing_deg) < 12 else ("right" if c.bearing_deg > 0 else "left")
                    print(f"    {c.label or 'person':<10} {c.distance_m:.2f} m  {c.bearing_deg:+6.1f} deg ({side})"
                          f"  conf {c.confidence:.2f}  [{c.source}]")
                elif c.distance_m is not None:
                    print(f"    {'echo':<10} {c.distance_m:.2f} m  bearing UNKNOWN"
                          f"          conf {c.confidence:.2f}  [{c.source}]")
                else:
                    print(f"    {c.label or 'device':<10} present, no position         "
                          f"  conf {c.confidence:.2f}  [{c.source}]")
            for s in snap.sensors:
                if not s.ok and s.detail:
                    print(f"    ! {s.name}: {s.detail}")
    except KeyboardInterrupt:
        pass
    finally:
        if svc is not None:
            svc.stop()
    return 0


def calibrate(distance_m: float) -> int:
    try:
        import cv2
    except ImportError:
        print("needs opencv: .venv/bin/pip install opencv-python-headless")
        return 1
    from jarvis.presence import camera, geometry as geo

    if not camera.ensure_model():
        print("could not fetch the YuNet model (offline?)")
        return 1
    cap = cv2.VideoCapture(camera._DEVICE)
    if not cap.isOpened():
        print(f"camera {camera._DEVICE} is busy — close the overlay, or set JARVIS_OVERLAY_CAMERA=0")
        return 1
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det = cv2.FaceDetectorYN_create(str(camera.MODEL_PATH), "", (width, height), score_threshold=0.8)
    print(f"stand exactly {distance_m} m from the camera, facing it. sampling 40 frames...")
    samples = []
    for _ in range(60):
        ok, frame = cap.read()
        if not ok:
            continue
        if frame.mean() < camera.DARK_LEVEL:
            print("frame is black — privacy shutter closed or room dark")
            cap.release()
            return 1
        _, faces = det.detect(frame)
        if faces is not None and len(faces) == 1:
            ipd = camera._ipd_px([(faces[0][4], faces[0][5]), (faces[0][6], faces[0][7])])
            if ipd:
                samples.append(ipd)
        if len(samples) >= 40:
            break
        time.sleep(0.05)
    cap.release()
    if len(samples) < 10:
        print(f"only {len(samples)} clean single-face samples — try better light and stay still")
        return 1

    ipd_px = statistics.median(samples)
    cal = geo.Calibration.from_known_distance(ipd_px, distance_m, width)
    spread = statistics.pstdev(samples) / ipd_px * 100
    print(f"\nsamples={len(samples)}  median IPD={ipd_px:.1f}px  spread={spread:.1f}%")
    print(f"assumed HFOV {geo.hfov_deg():.1f} deg -> measured {cal.hfov_deg:.1f} deg")
    print(f"at {distance_m} m the assumed value would report "
          f"{geo.distance_from_ipd(ipd_px, width):.2f} m")
    print(f"\nput this in .env:\n  JARVIS_CAM_HFOV_DEG={cal.hfov_deg:.1f}")
    return 0


def snapshot(path: str) -> int:
    """Save what the camera sees, with any detected face boxed — for aiming the lid."""
    try:
        import cv2
    except ImportError:
        print("needs opencv: .venv/bin/pip install opencv-python-headless")
        return 1
    from jarvis.presence import camera, geometry as geo

    if not camera.ensure_model():
        print("could not fetch the YuNet model (offline?)")
        return 1
    # Grab a usable frame as early as possible: this webcam only delivers ~40% of reads
    # once USB autosuspend has kicked in, so spend the budget on getting one good frame
    # rather than on anything clever beforehand.
    frame = None
    index = None
    for _ in range(4):
        cap, index = camera.open_capture(cv2, warmup=10)
        if cap is None:
            time.sleep(1.0)
            continue
        for _ in range(40):
            ok, candidate = cap.read()
            if ok and candidate is not None and candidate.mean() > 1:
                frame = candidate
                break
            time.sleep(0.05)
        if frame is not None:
            break
        cap.release()
    if frame is None:
        print("camera never delivered a frame.")
        print("This laptop's webcam re-enumerates on USB resume; fix it once with:")
        print("  sudo bash scripts/fix-camera-autosuspend.sh")
        return 1
    for _ in range(6):                   # a few more, so auto-exposure settles
        ok, better = cap.read()
        if ok and better is not None:
            frame = better
    rotation = camera.detect_rotation(cv2, cap, camera.MODEL_PATH, attempts=4)
    cap.release()

    print(f"video{index}  {frame.shape[1]}x{frame.shape[0]}  brightness {frame.mean():.0f}/255")
    if frame.mean() < camera.DARK_LEVEL:
        print("frame is black — privacy shutter closed or the room is dark")
    if rotation is None:
        print("orientation: unknown (no face found in any rotation — nobody in shot?)")
        rotation = 0
    else:
        print(f"orientation: {rotation} deg rotation needed")
    frame = camera._rotate(cv2, frame, rotation)
    h, w = frame.shape[:2]
    det = cv2.FaceDetectorYN_create(str(camera.MODEL_PATH), "", (w, h), score_threshold=0.5)
    det.setInputSize((w, h))
    _, faces = det.detect(frame)
    if faces is None or not len(faces):
        print("NO FACE DETECTED — is your face fully inside the frame? Tilt the lid.")
    for f in faces if faces is not None else []:
        x, y, bw, bh = (int(v) for v in f[:4])
        ipd = camera._ipd_px([(f[4], f[5]), (f[6], f[7])])
        cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 255, 0), 2)
        if ipd:
            dist = geo.distance_from_ipd(ipd, w)
            bearing = geo.bearing_deg(x + bw / 2, w)
            label = f"{dist:.2f}m {bearing:+.0f}deg"
            cv2.putText(frame, label, (x, max(14, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            print(f"  FACE score={f[-1]:.2f}  {dist:.2f} m  bearing {bearing:+.1f} deg")
    cv2.imwrite(path, frame)
    print(f"saved {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--sensors", default=None, help="override JARVIS_PRESENCE, e.g. camera,acoustic")
    ap.add_argument("--calibrate", type=float, metavar="METRES",
                    help="solve the camera FOV from a known standoff")
    ap.add_argument("--snapshot", nargs="?", const="/tmp/jarvis-camera.jpg", metavar="PATH",
                    help="save what the camera sees, with any face boxed")
    args = ap.parse_args()
    if args.snapshot:
        return snapshot(args.snapshot)
    return calibrate(args.calibrate) if args.calibrate else live(args.seconds, args.sensors)


if __name__ == "__main__":
    sys.exit(main())
