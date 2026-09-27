#!/usr/bin/env python3
"""Real-desktop demonstration of JARVIS 3D Studio, through JARVIS's own command router.

    .venv/bin/python scripts/demo_3d_studio.py [--no-window]

1. Shows a generated (fictional) two-colour logo full-screen on a plain backdrop.
2. "Jarvis, make a 3D model of this logo, 150 mm wide" — captured from the screen (only the
   logo region is kept; the full frame is shredded), reconstructed, opened in Blender.
3. "make the logo blue 20 percent taller" — verified in Blender.
4. "undo the last change" — verified.
5. "export it as glb", "export it for 3D printing" — both files validated.
6. "delete the references".

Prints a JSON summary. Blender is left open on the result.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from jarvis import commands  # noqa: E402
from jarvis.three_d import exports, studio as studio_mod, synthetic  # noqa: E402

SHOW = r"""
import sys, tkinter as tk
root = tk.Tk(); root.title("JARVIS 3D Studio demo reference"); root.configure(bg="white")
root.attributes("-fullscreen", True)
img = tk.PhotoImage(file=sys.argv[1])
tk.Label(root, image=img, bg="white", bd=0).place(relx=0.5, rely=0.5, anchor="center")
root.after(int(sys.argv[2]) * 1000, root.destroy); root.mainloop()
"""


def say(text: str) -> str:
    reply = asyncio.run(commands.handle(text, SimpleNamespace()))
    print(f"> {text}\n  {reply}", flush=True)
    return reply or ""


def wait_ready(st, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        if st.job is not None and st.job.state.terminal or (st.job and st.job.state.value == "needs_input"):
            return
        time.sleep(0.5)


def main() -> int:
    present = "--no-window" not in sys.argv
    out = {}
    tmp = tempfile.mkdtemp(prefix="jarvis3d-demo-")
    ref = os.path.join(tmp, "reference.png")
    synthetic.logo(ref, scale=3)
    viewer = subprocess.Popen([sys.executable, "-c", SHOW, ref, "25"])
    time.sleep(2.5)
    # Only capture if the reference really is what's in front: never whatever else is on screen.
    try:
        wid = subprocess.run(["xdotool", "search", "--pid", str(viewer.pid), "--name", "JARVIS 3D Studio demo"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        if wid:
            subprocess.run(["xdotool", "windowactivate", "--sync", wid[-1]], timeout=5, capture_output=True)
            time.sleep(0.8)
        active = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        wid, active = [], ""
    if not wid or active != wid[-1]:
        viewer.terminate()
        print(json.dumps({"state": "not captured",
                          "reason": "the reference window could not be brought to the front, so nothing was "
                                    "captured (see docs/3D_STUDIO.md, manual test)"}, indent=1))
        return 2
    st = studio_mod.get()
    st.cfg.present = present
    t0 = time.time()
    out["start"] = say("Jarvis, make a 3D model of this logo, 150 mm wide")
    wait_ready(st)
    viewer.terminate()
    out["build_seconds"] = round(time.time() - t0, 1)
    out["state"] = st.job.state.value
    if st.job.state.value != "ready":
        out["status"] = st.status()
        print(json.dumps(out, indent=1))
        return 1
    ref_asset = st.refs[0]
    out["capture"] = {"source": ref_asset.source, "crop": ref_asset.crop.to_dict() if ref_asset.crop else None,
                      "privacy": ref_asset.privacy}
    out["metrics"] = st.metrics
    out["ready"] = st.said if hasattr(st, "said") else st.status()
    scene = {o["name"]: o for o in st.session.scene()["objects"]}
    out["blender"] = {"window": not st.session.background, "file": st.session.scene()["file"],
                      "parts": {n: [round(v * 1000, 2) for v in o["size"]] for n, o in scene.items() if o["role"] == "part"}}
    target = next(n for n in scene if n.endswith("Blue"))
    h0 = scene[target]["size"][2]
    out["edit"] = say(f"make the {target.lower()} 20 percent taller")
    h1 = {o["name"]: o for o in st.session.scene()["objects"]}[target]["size"][2]
    out["edit_verified"] = {"height_before_mm": round(h0 * 1000, 2), "height_after_mm": round(h1 * 1000, 2),
                            "ratio": round(h1 / h0, 4)}
    time.sleep(3)
    out["undo"] = say("undo the last change")
    h2 = {o["name"]: o for o in st.session.scene()["objects"]}[target]["size"][2]
    out["undo_verified"] = {"height_mm": round(h2 * 1000, 2), "restored": abs(h2 - h0) < 1e-6}
    out["glb"] = say("export it as glb")
    out["stl"] = say("export it for 3D printing")
    st.pool.submit(lambda: None).result(600)          # a long export carries on in the background
    out["stl_final"] = st.job.exports[-1] if st.job.exports else None
    exp = st.project.exports_dir()
    out["exports"] = {}
    for f in sorted(os.listdir(exp)):
        p = os.path.join(exp, f)
        chk = exports.validate_glb(p) if f.endswith(".glb") else exports.validate_stl(p)
        out["exports"][f] = {"ok": chk.ok, "faces": chk.faces, "size_mm": chk.size_mm, "problems": chk.problems,
                             "warnings": chk.warnings, "bed": chk.facts.get("bed", {}).get("suggestion")}
    out["delete"] = say("delete the references")
    out["refs_left"] = os.listdir(os.path.join(st.project.path, "refs"))
    out["project"] = str(st.project.path)
    os.remove(ref)
    os.rmdir(tmp)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
