"""Optional image-to-3D providers — adapters, never requirements.

None is installed or downloaded by JARVIS. Each adapter says what it needs and why it is not
usable right now, and any provider that would receive a reference picture from this computer
needs an approval that names it, every time.

Researched September 2026 (see docs/3D_STUDIO.md for sources):

* **TRELLIS.2** (Microsoft, MIT licence). GLB/OBJ/PLY output with PBR textures. 8 GB VRAM for
  256-resolution generation, 12 GB for 512, 16–24 GB for full quality; community low-VRAM modes
  claim ~6 GB. Not runnable on a 4 GB laptop GPU that also carries the voice models.
* **Hunyuan3D-2.1** (Tencent Hunyuan Community Licence — excludes the EU, UK and South Korea;
  over 1 M MAU needs a commercial licence). ~10 GB VRAM for shape, ~21 GB for textures. GLB.
* **Remote endpoint** — any HTTPS service the user configures (``JARVIS_3D_REMOTE_URL``) that
  takes an image and returns a GLB. The image leaves the machine, so it is approval-gated.
"""
from __future__ import annotations

import os
import struct
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class Provider:
    name: str
    kind: str                  # local | remote
    licence: str
    min_vram_gb: float
    output: str
    notes: str = ""
    url: str = ""


def providers() -> list[Provider]:
    out = [
        Provider("TRELLIS.2", "local", "MIT", 8.0, "GLB (PBR)", "weights ~ several GB; not installed"),
        Provider("Hunyuan3D-2.1", "local", "Tencent Hunyuan Community Licence (territory-restricted)", 10.0,
                 "GLB", "shape 10 GB VRAM, textures 21 GB; not installed"),
    ]
    url = os.environ.get("JARVIS_3D_REMOTE_URL", "").strip()
    if url:
        out.append(Provider("Remote 3D endpoint", "remote", "per the service's terms", 0.0, "GLB",
                            f"sends the reference to {urlparse(url).hostname}", url=url))
    return out


def availability(p: Provider, free_vram_gb: float) -> tuple[bool, str]:
    if p.kind == "remote":
        if not p.url.startswith("https://"):
            return False, "the remote endpoint must use HTTPS"
        return True, f"needs your approval to send the picture to {urlparse(p.url).hostname}"
    if free_vram_gb < p.min_vram_gb:
        return False, (f"{p.name} needs about {p.min_vram_gb:g} GB of GPU memory; this machine has "
                       f"{free_vram_gb:.1f} GB free")
    return False, f"{p.name} is not installed (JARVIS does not download model weights on its own)"


def choose(free_vram_gb: float) -> tuple[Optional[Provider], list[str]]:
    """The first usable provider, and why each other one is not."""
    reasons = []
    for p in providers():
        ok, why = availability(p, free_vram_gb)
        if ok:
            return p, reasons
        reasons.append(why)
    return None, reasons


class ApprovalRequired(RuntimeError):
    pass


def generate_remote(p: Provider, image_path: str, *, approved_for: str = "", timeout: float = 180.0) -> tuple[list, list]:
    """Send one image to the approved remote provider; returns (vertices_mm, faces).

    ``approved_for`` must be the provider's hostname exactly — an approval for a different
    provider, or a generic "yes", does not count.
    """
    host = urlparse(p.url).hostname or ""
    if not approved_for or approved_for != host:
        raise ApprovalRequired(f"sending the reference to {host} needs your approval")
    import httpx

    with open(image_path, "rb") as fh:
        r = httpx.post(p.url, files={"image": ("reference.png", fh, "image/png")}, timeout=timeout)
    r.raise_for_status()
    return read_glb_mesh(r.content)


# ----------------------------------------------------------------------------- GLB reading
def parse_glb(blob: bytes) -> tuple[dict, bytes]:
    if len(blob) < 20 or blob[:4] != b"glTF":
        raise ValueError("not a GLB file")
    version, length = struct.unpack("<II", blob[4:12])
    if version != 2 or length != len(blob):
        raise ValueError("unsupported or truncated GLB")
    off = 12
    doc, binchunk = None, b""
    while off + 8 <= len(blob):
        clen, ctype = struct.unpack("<II", blob[off:off + 8])
        chunk = blob[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            binchunk = chunk
        off += 8 + clen
    if doc is None:
        raise ValueError("GLB has no JSON chunk")
    return doc, binchunk


def _accessor(doc, binchunk, idx):
    import numpy as np

    acc = doc["accessors"][idx]
    view = doc["bufferViews"][acc["bufferView"]]
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[acc["type"]]
    dtype = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}[acc["componentType"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    n = acc["count"] * comps
    arr = np.frombuffer(binchunk, dtype=dtype, count=n, offset=start)
    return arr.reshape(acc["count"], comps) if comps > 1 else arr


def read_glb_mesh(blob: bytes, max_faces: int = 150_000) -> tuple[list, list]:
    """All triangles of a GLB as (vertices in mm, faces). Node transforms are ignored (y-up → z-up)."""
    doc, binchunk = parse_glb(blob)
    verts, faces = [], []
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            pos = _accessor(doc, binchunk, prim["attributes"]["POSITION"])
            base = len(verts)
            verts += [[float(x) * 1000, float(-z) * 1000, float(y) * 1000] for x, y, z in pos]
            if "indices" in prim:
                ind = _accessor(doc, binchunk, prim["indices"]).reshape(-1, 3)
            else:
                ind = [(i, i + 1, i + 2) for i in range(0, len(pos), 3)]
            faces += [[base + int(a), base + int(b), base + int(c)] for a, b, c in ind]
            if len(faces) > max_faces:
                raise ValueError("the generated mesh exceeds the polygon limit")
    return verts, faces
