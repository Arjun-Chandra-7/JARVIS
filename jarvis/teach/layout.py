"""Where a standalone lesson goes on the screen: out of the way, but big enough to read.

All coordinates are logical pixels relative to the monitor's top-left, the same space the
renderer draws in. The work area (the screen minus GNOME's top bar and any dock) is what a
lesson may use.
"""
from __future__ import annotations

from typing import Optional


def panel(area: dict, want_w: float, want_h: float, side: str = "right", margin: float = 28) -> dict:
    """A panel of about the wanted size inside the work area, shrunk to fit a small screen."""
    work = area["work"]
    w = min(want_w, work["w"] - 2 * margin)
    h = min(want_h, work["h"] - 2 * margin)
    if side == "center":
        x = work["x"] + (work["w"] - w) / 2
    elif side == "left":
        x = work["x"] + margin
    else:
        x = work["x"] + work["w"] - w - margin
    y = work["y"] + max(margin, (work["h"] - h) / 2 * 0.6)
    return {"x": round(x), "y": round(y), "w": round(w), "h": round(h)}


def beside(box: dict, area: dict, w: float, h: float, gap: float = 36) -> dict:
    """A panel next to something on screen — right of it if there is room, else left, else
    below — and never over it."""
    work = area["work"]
    right = box["x"] + box["w"] + gap
    if right + w <= work["x"] + work["w"] - 12:
        x, y = right, box["y"] + (box["h"] - h) / 2
    elif box["x"] - gap - w >= work["x"] + 12:
        x, y = box["x"] - gap - w, box["y"] + (box["h"] - h) / 2
    else:
        x, y = box["x"] + (box["w"] - w) / 2, box["y"] + box["h"] + gap
    x = min(max(x, work["x"] + 12), work["x"] + work["w"] - w - 12)
    y = min(max(y, work["y"] + 12), work["y"] + work["h"] - h - 12)
    return {"x": round(x), "y": round(y), "w": round(w), "h": round(h)}


def scale_for(area: dict, base_w: float = 1920.0) -> float:
    """Text and strokes grow a little on big logical screens and shrink on small ones."""
    return max(0.72, min(1.35, area["w"] / base_w))


def fits(box: dict, area: dict) -> bool:
    work = area["work"]
    return (box["x"] >= work["x"] and box["y"] >= work["y"] and box["x"] + box["w"] <= work["x"] + work["w"]
            and box["y"] + box["h"] <= work["y"] + work["h"])


def bbox(points) -> Optional[dict]:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {"x": min(xs), "y": min(ys), "w": max(1.0, max(xs) - min(xs)), "h": max(1.0, max(ys) - min(ys))}
