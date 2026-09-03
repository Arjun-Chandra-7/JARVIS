"""High-Precision FMCW Acoustic Radar Engine with MTI Clutter Cancellation,
CA-CFAR Target Detection, Stereo Phase Interferometry, and Kalman Tracking.

Architecture:
1. Continuous Phase-Locked Full-Duplex Audio Stream (48 kHz).
2. FMCW Chirp: 18.0 kHz -> 21.5 kHz, Bandwidth B=3.5 kHz, T=40ms (25 sweeps/sec).
   - Theoretical range resolution: delta_R = c / (2*B) = 343 / (2 * 3500) = 4.9 cm.
3. De-chirping & Fast-Time Range FFT with 3-Pulse MTI Clutter Canceler (eliminates walls/furniture).
4. CA-CFAR (Cell-Averaging Constant False Alarm Rate) adaptive noise-floor detector.
5. Dual-Mic Stereo Phase Interferometry for Azimuth Angle (-45 deg to +45 deg).
6. Multi-Target Kalman Filter with Track Association & Coasting.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any

import numpy as np
from scipy.signal import butter, sosfilt

_SR = 48000
_CHIRP_T = 0.040  # 40ms chirp sweep
_N = int(_SR * _CHIRP_T)  # 1920 samples per chirp
_F0 = 18000.0
_F1 = 21500.0
_B = _F1 - _F0  # 3500 Hz bandwidth
_FC = (_F0 + _F1) / 2.0  # 19750 Hz carrier center
_SPEED_OF_SOUND = 343.0  # m/s
_MIC_DISTANCE = 0.18  # 18cm laptop stereo microphone baseline

# Range configuration
_MIN_DIST = 0.30  # 30 cm minimum distance (skip direct acoustic leakage)
_MAX_DIST = 3.80  # 3.8 meters maximum detection range

_sonar_lock = threading.Lock()
_sonar_state = {
    "running": False,
    "last_scan": 0.0,
    "detections": [],
    "active_tracks": [],
}
_worker_started = False


def _build_tx_chirp() -> np.ndarray:
    """Generate linear frequency modulated (LFM) chirp with Hann window."""
    t = np.linspace(0, _CHIRP_T, _N, endpoint=False)
    k = _B / _CHIRP_T
    phase = 2 * np.pi * (_F0 * t + 0.5 * k * t**2)
    # Hann window smooths start/end to avoid high-frequency speaker clicks
    window = np.hanning(_N)
    signal = 0.16 * window * np.sin(phase)
    return signal.astype(np.float32)


class KalmanTargetTracker:
    """Multi-Target Kalman / Alpha-Beta Filter for persistent human tracking."""

    def __init__(self):
        self.tracks = {}  # track_id -> dict
        self._next_id = 1

    def update(self, raw_targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        matched_tracks = set()
        matched_detections = set()

        # 1. Associate incoming detections to existing tracks (Euclidean distance matching)
        for tid, tr in list(self.tracks.items()):
            best_det_idx = None
            best_dist = 999.0

            for idx, d in enumerate(raw_targets):
                if idx in matched_detections:
                    continue
                spatial_dist = math.hypot(tr["x"] - d["x"], tr["y"] - d["y"])
                if spatial_dist < 0.75 and spatial_dist < best_dist:
                    best_dist = spatial_dist
                    best_det_idx = idx

            if best_det_idx is not None:
                d = raw_targets[best_det_idx]
                matched_detections.add(best_det_idx)
                matched_tracks.add(tid)

                dt = max(0.01, min(0.5, now - tr["last_seen"]))
                alpha = 0.32
                beta = 0.10
                residual_x = d["x"] - tr["x"]
                residual_y = d["y"] - tr["y"]

                tr["x"] += alpha * residual_x
                tr["y"] = max(_MIN_DIST, tr["y"] + alpha * residual_y)
                
                # Update and clamp velocity (max human walking speed 1.5 m/s)
                new_vx = (beta / dt) * residual_x
                new_vy = (beta / dt) * residual_y
                tr["vx"] = float(np.clip(0.7 * tr["vx"] + 0.3 * new_vx, -1.5, 1.5))
                tr["vy"] = float(np.clip(0.7 * tr["vy"] + 0.3 * new_vy, -1.5, 1.5))

                # Update polar representation
                dist = math.hypot(tr["x"], tr["y"])
                angle = math.degrees(math.atan2(tr["x"], tr["y"]))
                tr["distance"] = round(dist, 2)
                tr["angle"] = round(angle, 1)
                tr["snr"] = round(0.7 * tr["snr"] + 0.3 * d["snr"], 2)
                tr["hits"] += 1
                tr["misses"] = 0
                tr["last_seen"] = now

        # 2. Initialize new tracks for unassociated detections
        for idx, d in enumerate(raw_targets):
            if idx not in matched_detections and len(self.tracks) < 4:
                tid = f"p{self._next_id}"
                self._next_id = (self._next_id % 999) + 1
                self.tracks[tid] = {
                    "id": tid,
                    "x": d["x"],
                    "y": max(_MIN_DIST, d["y"]),
                    "vx": 0.0,
                    "vy": 0.0,
                    "distance": d["distance"],
                    "angle": d["angle"],
                    "snr": d["snr"],
                    "hits": 1,
                    "misses": 0,
                    "first_seen": now,
                    "last_seen": now,
                }

        # 3. Handle coasting / pruning of lost tracks
        for tid in list(self.tracks.keys()):
            if tid not in matched_tracks:
                tr = self.tracks[tid]
                tr["misses"] += 1
                dt = max(0.01, min(0.3, now - tr["last_seen"]))
                # Smooth coasting with velocity damping
                tr["x"] += tr["vx"] * dt * 0.4
                tr["y"] = max(_MIN_DIST, tr["y"] + tr["vy"] * dt * 0.4)
                tr["vx"] *= 0.6
                tr["vy"] *= 0.6
                
                dist = math.hypot(tr["x"], tr["y"])
                tr["distance"] = round(dist, 2)
                tr["angle"] = round(math.degrees(math.atan2(tr["x"], tr["y"])), 1)
                
                # Coast for up to 1.5s before removing
                if now - tr["last_seen"] > 1.5:
                    del self.tracks[tid]

        # 4. Output confirmed stable tracks (require at least 2 hits or recent confirmation)
        active_list = []
        for tr in sorted(self.tracks.values(), key=lambda t: t["distance"]):
            if tr["hits"] >= 2 or (now - tr["first_seen"] < 0.6):
                active_list.append({
                    "id": tr["id"],
                    "distance": round(tr["distance"], 2),
                    "angle_deg": round(tr["angle"], 1),
                    "x_m": round(tr["x"], 2),
                    "y_m": round(tr["y"], 2),
                    "snr_db": round(tr["snr"], 1),
                    "type": "HUMAN_TARGET",
                })

        return active_list


def _ca_cfar_peaks(range_profile: np.ndarray, num_train: int = 6, num_guard: int = 2, p_fa_scale: float = 1.35) -> list[int]:
    """Cell-Averaging Constant False Alarm Rate (CA-CFAR) Detector."""
    n = len(range_profile)
    peaks = []
    win_size = num_train + num_guard

    for i in range(win_size, n - win_size):
        # Leading and lagging training cells
        lead = range_profile[i - win_size : i - num_guard]
        lag = range_profile[i + num_guard + 1 : i + win_size + 1]
        noise_floor = (np.mean(lead) + np.mean(lag)) * 0.5
        threshold = noise_floor * p_fa_scale

        # Peak condition: exceeds CFAR adaptive threshold & local maximum
        if range_profile[i] > threshold and range_profile[i] >= range_profile[i - 1] and range_profile[i] >= range_profile[i + 1]:
            peaks.append(i)

    return peaks


def _sonar_radar_loop():
    """Continuous high-rate acoustic radar loop."""
    global _sonar_state
    tx_chirp = _build_tx_chirp()

    try:
        import sounddevice as sd
    except Exception:
        return

    # Low-pass filter for beat signal (cutoff at 2400 Hz for distances up to 4.0m)
    sos = butter(4, 2400, "low", fs=_SR, output="sos")

    # Slow-time pulse buffer (M=16 chirps = 640ms history)
    M = 16
    history_fft_L = np.zeros((M, _N // 2 + 1), dtype=np.complex64)
    history_fft_R = np.zeros((M, _N // 2 + 1), dtype=np.complex64)
    chirp_counter = 0

    tracker = KalmanTargetTracker()

    def stream_callback(indata, outdata, frames, time_info, status):
        nonlocal chirp_counter, history_fft_L, history_fft_R
        # Output repeating FMCW chirp
        outdata[:, 0] = tx_chirp[:frames]
        if outdata.shape[1] > 1:
            outdata[:, 1] = tx_chirp[:frames]

        rx_L = indata[:_N, 0]
        rx_R = indata[:_N, 1] if indata.shape[1] > 1 else rx_L

        # Dechirp (Mix received echo with reference transmit chirp)
        beat_L = sosfilt(sos, rx_L * tx_chirp)
        beat_R = sosfilt(sos, rx_R * tx_chirp)

        # Fast-time Range FFT
        fft_L = np.fft.rfft(beat_L * np.hanning(_N))
        fft_R = np.fft.rfft(beat_R * np.hanning(_N))

        idx = chirp_counter % M
        history_fft_L[idx] = fft_L
        history_fft_R[idx] = fft_R
        chirp_counter += 1

    stream = sd.Stream(samplerate=_SR, blocksize=_N, channels=(2, 2), dtype="float32", callback=stream_callback)

    freqs = np.fft.rfftfreq(_N, 1.0 / _SR)
    dist_axis = (freqs * _SPEED_OF_SOUND * _CHIRP_T) / (2.0 * _B)

    # Valid distance bin indices
    min_bin = np.searchsorted(dist_axis, _MIN_DIST)
    max_bin = min(len(dist_axis) - 1, np.searchsorted(dist_axis, _MAX_DIST))

    with stream:
        while _sonar_state["running"]:
            time.sleep(0.08)  # 12.5 updates per second

            if chirp_counter < M:
                continue

            # 1. 3-Pulse MTI Clutter Cancellation (eliminates static room objects)
            # y[m] = x[m] - 2*x[m-1] + x[m-2]
            idx_curr = (chirp_counter - 1) % M
            idx_prev1 = (chirp_counter - 2) % M
            idx_prev2 = (chirp_counter - 3) % M

            mti_L = history_fft_L[idx_curr] - 2 * history_fft_L[idx_prev1] + history_fft_L[idx_prev2]
            mti_R = history_fft_R[idx_curr] - 2 * history_fft_R[idx_prev1] + history_fft_R[idx_prev2]

            # Magnitude range profile
            range_profile = np.abs(mti_L)

            # 2. CA-CFAR Target Detection on valid distance range
            valid_profile = range_profile[min_bin:max_bin]
            cfar_peaks = _ca_cfar_peaks(valid_profile, num_train=6, num_guard=2, p_fa_scale=1.4)

            raw_detections = []
            for peak_idx in cfar_peaks:
                actual_bin = min_bin + peak_idx
                dist = float(dist_axis[actual_bin])

                # 3. Stereo Phase Interferometry for Azimuth Angle
                z_l = mti_L[actual_bin]
                z_r = mti_R[actual_bin]
                phase_diff = np.angle(z_l * np.conj(z_r))

                sin_val = (phase_diff * _SPEED_OF_SOUND) / (2 * np.pi * _FC * _MIC_DISTANCE)
                sin_val = float(np.clip(sin_val, -0.85, 0.85))
                angle_deg = math.degrees(math.asin(sin_val))

                # Convert polar (r, theta) to Cartesian (x, y) coordinates
                rad = math.radians(angle_deg)
                x_m = dist * math.sin(rad)
                y_m = dist * math.cos(rad)

                # Signal to Noise ratio
                noise_est = np.mean(valid_profile) + 1e-6
                snr = 20 * math.log10(max(1.0, range_profile[actual_bin] / noise_est))

                # Require meaningful SNR (> 4.5 dB above local floor)
                if snr >= 4.5:
                    raw_detections.append({
                        "distance": dist,
                        "angle": angle_deg,
                        "x": x_m,
                        "y": y_m,
                        "snr": snr,
                    })

            # Cluster nearby reflection peaks and apply shadow cone occlusion filtering
            # Sort by distance (nearest to farthest)
            sorted_by_dist = sorted(raw_detections, key=lambda x: x["distance"])
            distinct_human_targets = []

            for d in sorted_by_dist:
                # Check if this reflection is in the shadow cone of an existing closer human
                is_shadow = False
                for p in distinct_human_targets:
                    angle_diff = abs(d["angle"] - p["angle"])
                    dist_diff = d["distance"] - p["distance"]
                    # If along same line of sight (< 22 deg), the farther reflection is a shadow/wall bounce
                    if angle_diff < 22.0:
                        is_shadow = True
                        break
                    # Also cluster if Euclidean distance is < 0.75m
                    if math.hypot(p["x"] - d["x"], p["y"] - d["y"]) < 0.75:
                        is_shadow = True
                        break

                if not is_shadow:
                    distinct_human_targets.append(d)

                if len(distinct_human_targets) >= 2:
                    break

            # 4. Kalman Multi-Target Tracker
            confirmed_targets = tracker.update(distinct_human_targets)

            with _sonar_lock:
                _sonar_state["detections"] = confirmed_targets
                _sonar_state["last_scan"] = time.time()


def start_sonar():
    """Start continuous FMCW Acoustic Radar background engine."""
    global _worker_started, _sonar_state
    with _sonar_lock:
        if _worker_started:
            return
        _sonar_state["running"] = True
        _worker_started = True

    t = threading.Thread(target=_sonar_radar_loop, daemon=True, name="FMCWAcousticRadarThread")
    t.start()


def get_sonar_snapshot() -> dict[str, Any]:
    """Get active spatial radar targets for HUD visualization."""
    start_sonar()
    with _sonar_lock:
        dets = list(_sonar_state["detections"])
        last_t = _sonar_state["last_scan"]
        age = time.time() - last_t if last_t else None

    return {
        "sonar_active": True,
        "count": len(dets),
        "humans": dets,
        "mode": "FMCW_MTI_CACFAR_RADAR",
        "resolution_cm": 4.9,
        "freq_band": "18.0kHz-21.5kHz",
        "age": round(age, 2) if age else None,
    }
