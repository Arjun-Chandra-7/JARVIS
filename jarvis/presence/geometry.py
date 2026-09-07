"""Camera geometry: pixels -> bearing and metres. Pure functions, no camera needed.

Distance is anchored on interpupillary distance because it is one of the tightest
anthropometric constants available: adult IPD is 63 +/- 3 mm (ANSUR II / Dodgson 2004),
i.e. ~5% 1-sigma, and it is measured between two landmarks a detector localises well.
Face-box width is a much weaker anchor (hair, pose, detector crop) and is only used as
a fallback with correspondingly lower confidence.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

IPD_M = 0.063          # mean adult interpupillary distance, metres
IPD_SIGMA_M = 0.003    # 1-sigma across adults
FACE_WIDTH_M = 0.155   # mean adult bizygomatic breadth incl. detector margin
SHOULDER_M = 0.40      # mean adult biacromial breadth

# Typical integrated-webcam horizontal field of view. Override once measured.
DEFAULT_HFOV_DEG = 68.0


def hfov_deg() -> float:
    try:
        return float(os.environ.get("JARVIS_CAM_HFOV_DEG", DEFAULT_HFOV_DEG))
    except ValueError:
        return DEFAULT_HFOV_DEG


def focal_px(frame_width_px: int, hfov_degrees: float | None = None) -> float:
    """Pinhole focal length in pixels from the horizontal field of view."""
    if frame_width_px <= 0:
        raise ValueError("frame_width_px must be positive")
    fov = math.radians(hfov_degrees if hfov_degrees is not None else hfov_deg())
    if not 0 < fov < math.pi:
        raise ValueError("hfov must be between 0 and 180 degrees")
    return (frame_width_px / 2.0) / math.tan(fov / 2.0)


def bearing_deg(x_px: float, frame_width_px: int, hfov_degrees: float | None = None) -> float:
    """Horizontal angle off the camera axis. Positive = to the viewer's right.

    Uses the pinhole model, not a linear fraction of the FOV — the difference at the
    frame edge is several degrees.
    """
    f = focal_px(frame_width_px, hfov_degrees)
    return math.degrees(math.atan2(x_px - frame_width_px / 2.0, f))


def distance_from_ipd(ipd_px: float, frame_width_px: int, hfov_degrees: float | None = None) -> float:
    """Metric range from the eye-to-eye pixel separation."""
    if ipd_px <= 0:
        raise ValueError("ipd_px must be positive")
    return focal_px(frame_width_px, hfov_degrees) * IPD_M / ipd_px


def distance_from_width(width_px: float, frame_width_px: int, real_width_m: float = FACE_WIDTH_M,
                        hfov_degrees: float | None = None) -> float:
    """Fallback range from a bounding-box width and an assumed real-world width."""
    if width_px <= 0:
        raise ValueError("width_px must be positive")
    return focal_px(frame_width_px, hfov_degrees) * real_width_m / width_px


def range_confidence(distance_m: float, from_ipd: bool) -> float:
    """How much to trust a range estimate: anchor quality, then degrade with distance.

    IPD carries ~5% biological spread; box width is far looser. Both degrade further as
    the subject shrinks in frame and a pixel is worth more metres.
    """
    base = 0.90 if from_ipd else 0.55
    far = max(0.0, min(1.0, (distance_m - 1.0) / 5.0))   # 0 at 1 m, 1 at 6 m
    return round(max(0.15, base * (1.0 - 0.45 * far)), 3)


@dataclass
class Calibration:
    """Solve for the true focal length from one known-distance observation."""

    frame_width_px: int
    focal_px: float

    @classmethod
    def from_known_distance(cls, ipd_px: float, distance_m: float, frame_width_px: int) -> "Calibration":
        """Stand a measured distance away, detect one face, and pin f_px exactly."""
        if ipd_px <= 0 or distance_m <= 0:
            raise ValueError("ipd_px and distance_m must be positive")
        return cls(frame_width_px, ipd_px * distance_m / IPD_M)

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan2(self.frame_width_px / 2.0, self.focal_px))
