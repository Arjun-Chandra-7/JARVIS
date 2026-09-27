"""A 3D Studio project on disk: versions, checkpoints, undo and redo.

    ~/Documents/JARVIS 3D/<name>-<id>/
        project.json          owner, versions, current version, job id
        model.blend           the current version (what Blender shows)
        versions/v003.blend   an atomic checkpoint per version
        versions/v003.json    the parametric model of that version
        versions/v003-*.png   small silhouettes, for "show me before and after"
        versions/autosave-*.blend   the scene just before anything destructive
        refs/                 reference crops (private, deletable)
        exports/              what the user asked to export
        renders/              previews

Every write is atomic (temporary file, then rename), so a crash mid-save leaves the previous
version intact. Only folders under the projects root are ever opened or written by Blender.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .types import StudioModel, new_id


def projects_root() -> Path:
    root = Path(os.environ.get("JARVIS_3D_ROOT", "~/Documents/JARVIS 3D")).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:40] or "model"


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass
class Version:
    number: int
    note: str
    created: float
    op: str = ""                        # the edit operation that produced it
    parts: list = field(default_factory=list)


class Project:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.meta_path = self.path / "project.json"
        self.meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

    # ------------------------------------------------------------------ creation
    @classmethod
    def create(cls, name: str, job_id: str = "", root: Optional[Path] = None) -> "Project":
        root = root or projects_root()
        pid = new_id("p")
        path = root / f"{_slug(name)}-{pid.split('-')[-1]}"
        for sub in ("", "versions", "refs", "exports", "renders"):
            (path / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
        p = cls(path)
        p.meta = {"id": pid, "name": name, "owner": "jarvis", "job": job_id, "created": time.time(),
                  "versions": [], "current": 0, "locked": []}
        p.save_meta()
        return p

    def save_meta(self) -> None:
        atomic_write(self.meta_path, json.dumps(self.meta, indent=1, default=str))

    # ------------------------------------------------------------------ paths
    @property
    def name(self) -> str:
        return self.meta.get("name", self.path.name)

    @property
    def blend(self) -> Path:
        return self.path / "model.blend"

    def version_blend(self, n: int) -> Path:
        return self.path / "versions" / f"v{n:03d}.blend"

    def version_json(self, n: int) -> Path:
        return self.path / "versions" / f"v{n:03d}.json"

    @property
    def current(self) -> int:
        return int(self.meta.get("current", 0))

    @property
    def latest(self) -> int:
        return max((v["number"] for v in self.meta["versions"]), default=0)

    def versions(self) -> list[Version]:
        return [Version(**v) for v in self.meta.get("versions", [])]

    def version(self, n: int) -> Optional[Version]:
        return next((v for v in self.versions() if v.number == n), None)

    # ------------------------------------------------------------------ versions
    def checkpoint(self, session, model: StudioModel, note: str, op: str = "", parts: Optional[list] = None,
                   silhouettes: Optional[dict] = None) -> int:
        """Save the scene and the model as the next version, and make it current.

        Versions after the current one (undone edits) stay on disk but leave the redo line: a new
        edit after an undo starts a new branch, as in any editor.
        """
        n = self.latest + 1
        session.save(str(self.version_blend(n)))
        atomic_write(self.version_json(n), json.dumps(model.to_dict(), default=str))
        session.save(str(self.blend))
        if silhouettes:
            self._save_silhouettes(n, silhouettes)
        keep = [v for v in self.meta["versions"] if v["number"] <= self.current]
        keep.append({"number": n, "note": note[:120], "created": time.time(), "op": op, "parts": parts or []})
        self.meta["versions"] = keep
        self.meta["current"] = n
        self.save_meta()
        return n

    def _save_silhouettes(self, n: int, masks: dict) -> None:
        from PIL import Image
        import numpy as np

        for view, mask in masks.items():
            if mask is None:
                continue
            Image.fromarray((np.asarray(mask) * 255).astype("uint8")).save(self.path / "versions" / f"v{n:03d}-{view}.png")

    def silhouette(self, n: int, view: str = "front"):
        from PIL import Image
        import numpy as np

        p = self.path / "versions" / f"v{n:03d}-{view}.png"
        if not p.exists():
            return None
        with Image.open(p) as im:
            return np.asarray(im) > 127

    def load_model(self, n: Optional[int] = None) -> StudioModel:
        n = n or self.current
        return StudioModel.from_dict(json.loads(self.version_json(n).read_text()))

    def autosave(self, session) -> Optional[str]:
        """The scene exactly as it is now — including anything the user changed by hand."""
        path = self.path / "versions" / f"autosave-{time.strftime('%Y%m%d-%H%M%S')}.blend"
        try:
            session.save(str(path), checkpoint=False)
        except Exception:  # noqa: BLE001 — an autosave that fails must not block the undo itself
            return None
        autos = sorted((self.path / "versions").glob("autosave-*.blend"))
        for old in autos[:-5]:
            old.unlink(missing_ok=True)
        return str(path)

    def restore(self, session, n: int) -> StudioModel:
        """Make version ``n`` current: Blender reopens its checkpoint, the model is reloaded."""
        if self.version(n) is None or not self.version_blend(n).exists():
            raise KeyError(f"there is no version {n}")
        self.autosave(session)
        # Copy the checkpoint over model.blend and open *that*: if Blender had the checkpoint itself
        # open, the user's own Ctrl+S would overwrite history.
        tmp = self.blend.with_name(".model.restoring.blend")
        shutil.copy2(self.version_blend(n), tmp)
        os.replace(tmp, self.blend)
        session.open(str(self.blend))
        self.meta["current"] = n
        self.save_meta()
        return self.load_model(n)

    def undo(self, session) -> Optional[StudioModel]:
        if self.current <= 1:
            return None
        return self.restore(session, self.current - 1)

    def redo(self, session) -> Optional[StudioModel]:
        nxt = self.current + 1
        if not self.version_blend(nxt).exists() or self.version(nxt) is None:
            later = [v for v in self.versions() if v.number > self.current]
            if not later:
                return None
            nxt = later[0].number
        return self.restore(session, nxt)

    def exports_dir(self) -> Path:
        return self.path / "exports"
