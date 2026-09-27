"""Render the teaching overlay's lessons offscreen and composite them over sample desktops.

    python scripts/teach_preview.py OUTDIR [--scale 1.25] [--size 1920x1080] [--scenes pyth,rag]

For looking at, not for asserting: text sharpness, stroke weight, spacing and colour on a dark and
a light background, at any size and scale, without drawing over the real desktop. The samples
are synthetic — no screenshot of this machine is taken or kept.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jarvis.teach.bus import Overlay, _NullTransport  # noqa: E402


def area_for(w: int, h: int, top: int) -> dict:
    return {"index": 0, "scale": 1.0, "w": w, "h": h, "work": {"x": 0, "y": top, "w": w, "h": h - top}}


def lesson_batches(plan, step_gap: int = 90) -> list[dict]:
    ov = Overlay(_NullTransport())
    ov.new_generation(plan.lesson_id)
    ov.send(plan.setup)
    for s in plan.steps:
        for p in s.phrases:
            ov.send(p.visuals)
    return [{"wait": 0 if i == 0 else step_gap, "batch": b} for i, b in enumerate(ov.transport.batches())]


def scenes(names: list[str], area: dict) -> list[dict]:
    from jarvis.teach.lessons import pythagoras

    out = []
    if "pyth" in names:
        for lang in ("en", "hi-pure"):
            out.append({"name": f"pythagoras-standalone-{lang}", "batches": lesson_batches(pythagoras.plan(lang, area)),
                        "settle": 1400})
        W, H = area["w"], area["h"]
        found = pythagoras.FoundTriangle((W * 0.26, H * 0.66), (W * 0.26, H * 0.3), (W * 0.47, H * 0.66), 0.9)
        out.append({"name": "pythagoras-traced-en", "batches": lesson_batches(pythagoras.plan("en", area, found=found)),
                    "settle": 1400})
    if "rag" in names:
        from jarvis.teach.lessons import rag
        for lang in ("en", "hinglish"):
            out.append({"name": f"rag-{lang}", "batches": lesson_batches(rag.plan(lang, area)), "settle": 1600})
        plan = rag.plan("en", area)
        batches = lesson_batches(plan)
        from jarvis.teach.lessons.rag import follow_up
        for kind in ("compare_finetune",):
            fu = follow_up(kind, plan, "en")
            ov = Overlay(_NullTransport())
            ov.new_generation(plan.lesson_id)
            for p in fu.phrases:
                ov.send(p.visuals)
            out.append({"name": f"rag-{kind}", "batches": batches + [{"wait": 60, "batch": b} for b in ov.transport.batches()],
                        "settle": 1600})
    return out


def backgrounds(w: int, h: int, top: int):
    from PIL import Image, ImageDraw

    dark = Image.new("RGB", (w, h), (24, 26, 31))
    d = ImageDraw.Draw(dark)
    d.rectangle([0, 0, w, top], fill=(12, 12, 14))
    for i, y in enumerate(range(top + 40, h, 26)):
        x0 = 60 + (i * 37) % 180
        d.rectangle([x0, y, x0 + 180 + (i * 71) % 520, y + 9], fill=(70 + i % 3 * 30, 90, 120))
    light = Image.new("RGB", (w, h), (250, 250, 252))
    d = ImageDraw.Draw(light)
    d.rectangle([0, 0, w, top], fill=(12, 12, 14))
    for i, y in enumerate(range(top + 60, h, 30)):
        d.rectangle([140, y, 140 + 300 + (i * 97) % 900, y + 10], fill=(60, 60, 70))
    return {"dark": dark, "light": light}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--scale", default="1")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--top", type=int, default=28)
    ap.add_argument("--scenes", default="pyth,rag")
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    area = area_for(w, h, a.top)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spec = scenes(a.scenes.split(","), area)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(spec, f)
        scenes_file = f.name
    electron = os.environ.get("ELECTRON") or str(Path("/home/xor_sensei/Madara/Dev/Jarvis/overlay/node_modules/.bin/electron"))
    if not Path(electron).exists():
        electron = str(ROOT / "overlay" / "node_modules" / ".bin" / "electron")
    raw = out / "raw"
    done = subprocess.run([electron, str(ROOT / "overlay" / "teach" / "preview-main.js"), scenes_file, str(raw),
                           f"--scale={a.scale}", f"--size={a.size}", f"--top={a.top}"],
                          capture_output=True, text=True, timeout=180)
    os.unlink(scenes_file)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    for ln in lines:
        print(ln)
    if done.returncode != 0:
        print(done.stderr[-2000:], file=sys.stderr)
    from PIL import Image

    phys = (round(w * float(a.scale)), round(h * float(a.scale)))
    bgs = {k: v.resize(phys) for k, v in backgrounds(w, h, a.top).items()}
    for ln in lines:
        info = json.loads(ln)
        if "file" not in info:
            continue
        layer = Image.open(info["file"]).convert("RGBA")
        for name, bg in bgs.items():
            canvas = bg.copy().convert("RGBA")
            canvas.alpha_composite(layer, (0, round(a.top * float(a.scale))))
            canvas.convert("RGB").save(out / f"{Path(info['file']).stem}-{name}.png")


if __name__ == "__main__":
    main()
