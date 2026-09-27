"""Getting a reference: a screen region, the active window, a file, the clipboard, video frames.

The rule for the screen: take as little as possible and keep less. Wayland's portal only gives
the whole screen, so the whole frame is cropped in memory the moment it arrives and the
full-screen file is shredded before anything else happens. The crop is then checked for
password, OTP, banking and chat content; a refused crop is shredded too. Only a crop that passes
is written into the project's ``refs/`` folder.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from . import privacy
from .types import Crop, ReferenceAsset, Retention, ViewKind, new_id


class CaptureRefused(RuntimeError):
    def __init__(self, verdict: privacy.PrivacyVerdict) -> None:
        super().__init__(verdict.reason())
        self.verdict = verdict


class CaptureFailed(RuntimeError):
    pass


# ----------------------------------------------------------------------------- screen
def _grab_full(tmpdir: str) -> Optional[str]:
    """A full-resolution PNG of the screen, or None. Uses JARVIS's own portal path."""
    from ..vision import screenshot

    out = str(Path(tmpdir) / f"full-{int(time.time() * 1000)}.png")
    path = None
    if os.environ.get("WAYLAND_DISPLAY"):
        path = screenshot._portal_screenshot(out)
    if not path:
        path = screenshot._tool_screenshot(out)
    return path if path and os.path.exists(path) else None


def active_window_geometry() -> Optional[Crop]:
    """The focused window's rectangle, when the desktop will say (X11 / XWayland windows)."""
    if not shutil.which("xdotool"):
        return None
    try:
        out = subprocess.run(["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    vals = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        return Crop(int(vals["X"]), int(vals["Y"]), int(vals["WIDTH"]), int(vals["HEIGHT"]))
    except (KeyError, ValueError):
        return None


def salient_region(image, pad: int = 12) -> Optional[Crop]:
    """The box around the main thing on a plain backdrop: what differs from the border colour.

    Used for "make a 3D model of this" when a reference is shown on its own (an image viewer,
    a picture on a page). Returns None when nothing stands out clearly — then JARVIS asks for a
    region instead of guessing a crop that might include private content.
    """
    import cv2
    import numpy as np

    arr = np.asarray(image.convert("RGB"), dtype=np.int16)
    h, w = arr.shape[:2]
    border = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.abs(arr - bg).sum(axis=2) > 40
    diff = cv2.morphologyEx(diff.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(diff, 8)
    if n <= 1:
        return None
    idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, bw, bh, area = stats[idx]
    if area < 0.002 * w * h:
        return None
    # Merge only pieces that nearly touch the object (a logo's separate letters, a dot over an i).
    # A generous merge once swept a notification banner above the object into the crop.
    x0, y0, x1, y1 = x, y, x + bw, y + bh
    gap = max(4, int(0.06 * max(bw, bh)))
    changed = True
    while changed:
        changed = False
        for i in range(1, n):
            sx, sy, sw, sh, sa = stats[i]
            if sa < 0.0005 * w * h:
                continue
            inside = sx >= x0 and sy >= y0 and sx + sw <= x1 and sy + sh <= y1
            near = sx < x1 + gap and sx + sw > x0 - gap and sy < y1 + gap and sy + sh > y0 - gap
            if near and not inside:
                x0, y0, x1, y1 = min(x0, sx), min(y0, sy), max(x1, sx + sw), max(y1, sy + sh)
                changed = True
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(w, x1 + pad), min(h, y1 + pad)
    return Crop(int(x0), int(y0), int(x1 - x0), int(y1 - y0))


def describe_crop(crop: Crop, screen: tuple[int, int]) -> str:
    sw, sh = screen
    cx, cy = crop.x + crop.width / 2, crop.y + crop.height / 2
    horiz = "left" if cx < sw / 3 else "right" if cx > 2 * sw / 3 else "middle"
    vert = "top" if cy < sh / 3 else "bottom" if cy > 2 * sh / 3 else "centre"
    where = "centre" if (horiz, vert) == ("middle", "centre") else f"{vert} {horiz}".replace("centre ", "")
    return f"a {crop.width}×{crop.height} area at the {where} of the screen"


def capture_screen(refs: privacy.References, *, region: Optional[Crop] = None, how: str = "auto",
                   view: ViewKind = ViewKind.UNKNOWN, window: tuple[str, str] = ("", ""),
                   grab: Optional[Callable[[str], Optional[str]]] = None,
                   ocr_words: Optional[Callable[[str], list]] = None) -> ReferenceAsset:
    """Capture part of the screen as a reference.

    ``how``: "region" (``region`` given), "window" (the focused window), "auto" (the salient
    object on screen). ``window`` is the focused (app, title), checked before anything is kept.
    ``grab``/``ocr_words`` are injectable for tests; by default the real screen and local OCR.
    """
    from PIL import Image

    verdict = privacy.check_title(*window)
    if not verdict.ok:
        privacy.security_event("capture_refused", category=verdict.category, source="title")
        raise CaptureRefused(verdict)
    grab = grab or _grab_full
    with tempfile.TemporaryDirectory(prefix="jarvis3d-") as tmp:
        os.chmod(tmp, 0o700)
        with privacy.quiet_notifications():
            full = grab(tmp)
        if not full:
            raise CaptureFailed("I couldn't capture the screen.")
        try:
            with Image.open(full) as im:
                im.load()
                screen = im.size
                if how == "region" and region is not None:
                    crop = region
                elif how == "window":
                    crop = active_window_geometry()
                    if crop is None:
                        raise CaptureFailed("I can't tell where the focused window is on this desktop. "
                                            "Show me the area instead.")
                else:
                    crop = salient_region(im)
                    if crop is None:
                        raise CaptureFailed("Nothing on screen stands out clearly enough to crop safely. "
                                            "Show me the area to use.")
                crop = Crop(max(0, crop.x), max(0, crop.y), min(crop.width, screen[0] - max(0, crop.x)),
                            min(crop.height, screen[1] - max(0, crop.y)))
                if crop.width < 16 or crop.height < 16:
                    raise CaptureFailed("That area is too small to reconstruct.")
                part = im.crop((crop.x, crop.y, crop.x + crop.width, crop.y + crop.height)).convert("RGB")
        finally:
            privacy.shred(full)           # the full screen never outlives the crop
        ref_id = new_id("ref")
        staged = Path(tmp) / f"{ref_id}.png"
        part.save(staged)
        words = (ocr_words or (lambda p: privacy.read_words(p)))(str(staged))
        verdict = privacy.check_words(words)
        del words
        if not verdict.ok:
            privacy.shred(staged)
            privacy.security_event("capture_refused", category=verdict.category, source="content")
            raise CaptureRefused(verdict)
        dest = refs.path_for(ref_id)
        shutil.move(str(staged), dest)
        os.chmod(dest, 0o600)
    return ReferenceAsset(id=ref_id, source="region" if how == "region" else how, path=str(dest),
                          width=crop.width, height=crop.height, crop=crop, privacy="clear",
                          retention=Retention.PROJECT, view=view,
                          perspective="unknown")


# ----------------------------------------------------------------------------- other sources
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")


def from_file(path: str, refs: privacy.References, view: ViewKind = ViewKind.UNKNOWN,
              source: str = "file") -> ReferenceAsset:
    """A copy of an image the user named. The original is not touched or remembered."""
    from PIL import Image

    p = Path(path).expanduser()
    if p.suffix.lower() not in IMAGE_SUFFIXES or not p.is_file():
        raise CaptureFailed("That isn't an image file I can use.")
    ref_id = new_id("ref")
    dest = refs.path_for(ref_id)
    with Image.open(p) as im:
        im = im.convert("RGB")
        if max(im.size) > 4096:
            im.thumbnail((4096, 4096))
        im.save(dest)
        size = im.size
    os.chmod(dest, 0o600)
    return ReferenceAsset(id=ref_id, source=source, path=str(dest), width=size[0], height=size[1],
                          privacy="clear", view=view)


def from_clipboard(refs: privacy.References, view: ViewKind = ViewKind.UNKNOWN) -> ReferenceAsset:
    if not shutil.which("wl-paste"):
        raise CaptureFailed("I can't read the clipboard on this desktop.")
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=3).stdout
        if "image/png" not in types:
            raise CaptureFailed("There's no image on the clipboard.")
        data = subprocess.run(["wl-paste", "--type", "image/png"], capture_output=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        raise CaptureFailed("I couldn't read the clipboard.") from None
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(data)
        tmp = fh.name
    try:
        ref = from_file(tmp, refs, view, source="clipboard")
        verdict = privacy.check_words(privacy.read_words(ref.path))
        if not verdict.ok:
            privacy.shred(ref.path)
            privacy.security_event("capture_refused", category=verdict.category, source="clipboard")
            raise CaptureRefused(verdict)
        return ref
    finally:
        privacy.shred(tmp)


def video_frames(path: str, refs: privacy.References, count: int = 12, max_side: int = 768) -> list[ReferenceAsset]:
    """Evenly spaced frames of a local video, for an orbit reference. Frames are transient."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise CaptureFailed("ffmpeg is needed to read a video.")
    count = max(4, min(count, 48))
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                    "default=nw=1:nk=1", path], capture_output=True, text=True,
                                   timeout=15).stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise CaptureFailed("I couldn't read that video.") from None
    out = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        ref_id = new_id("frame")
        dest = refs.path_for(ref_id)
        subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                        f"scale='min({max_side},iw)':-2", "-y", str(dest)], timeout=30, check=False)
        if dest.exists():
            os.chmod(dest, 0o600)
            out.append(ReferenceAsset(id=ref_id, source="video_frame", path=str(dest), privacy="clear",
                                      retention=Retention.TRANSIENT, view=ViewKind.PERSPECTIVE))
    return out


def camera_capture(approved: bool) -> ReferenceAsset:
    """The webcam is off-limits unless the user approved this one capture."""
    if not approved:
        raise CaptureRefused(privacy.PrivacyVerdict(False, "the camera (it needs your permission first)"))
    raise CaptureFailed("Camera capture isn't wired up yet; show me a picture on screen instead.")
