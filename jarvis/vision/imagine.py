"""Make a picture from a description, on this machine, with no API and no bill.

Why it runs on the processor
----------------------------
Measured on this laptop while Jarvis was up: the card holds 4094 MiB and had **802 MiB free** —
Ollama sits on ~1.4 GB and Whisper on ~476 MiB, both of which have to stay resident or every
spoken turn gets slower. SD-Turbo in half precision wants about 2.5 GB. It does not fit, and
evicting the brain and the ears to draw a fox is a bad trade. So this is a processor capability
by measurement, not by preference.

Why it is quick enough to be worth having
-----------------------------------------
The first honest timing was discouraging, and the reason was not the part anyone would guess:

    1 step, 512px, stock decoder   23.2s
    2 steps, 512px, stock decoder  25.8s

Two steps cost 2.6s more than one, so the diffusion was never the expense — the decoder was,
turning the finished latent into pixels for roughly twenty of those seconds. Swapping it for the
tiny distilled decoder leaves the diffusion untouched and removes almost all of that:

    1 step, 512px, tiny decoder     7.5s     <- the default
    2 steps, 512px, tiny decoder   14.3s
    1 step, 768px, tiny decoder    14.2s

Three times quicker for a decoder that costs 5 MB on disk. The output is very slightly softer
than the full decoder's; against a picture arriving three times sooner that is not a real cost.

One step is the default because SD-Turbo is distilled for exactly that, and because the second
step costs as much as the whole first picture. Guidance must be 0.0 — a turbo model was trained
without it and turns to mush when it is applied.

Nothing here is imported until someone actually asks for a picture. Torch alone takes a second
and a half to import, and most sessions never draw anything.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MODEL = "stabilityai/sd-turbo"
DECODER = "madebyollin/taesd"        # 5 MB, replaces the ~20s decode measured above

# Where the weights live. The root disk is 83% full; the second disk has 925 GB spare, and the
# weights are 2.5 GB. Set HF_HOME yourself and this steps aside.
WEIGHTS = Path.home() / "Madara" / ".cache" / "huggingface"

SAVE_TO = Path.home() / "Pictures" / "Jarvis"

DEFAULT_STEPS = 1
DEFAULT_SIZE = 512

# Generation is the only thing on this machine that will happily take all eight cores for ten
# seconds. Whisper needs some of them to hear the next sentence, so it does not get all eight.
THREADS = max(1, (os.cpu_count() or 4) - 2)

# Loaded, the model holds 5.4 GB of memory — measured — and this machine has 22 GB with Electron,
# a browser and Ollama already in it. Pictures come in bursts and then not for hours, so it is
# kept for a few minutes after the last one and then let go. Reloading costs about ten seconds
# cold and rather less once the files are in the page cache; holding a quarter of the machine's
# memory overnight to save that is the wrong way round.
IDLE_RELEASE = 300.0

_pipe = None
_last_used = 0.0
_lock = threading.Lock()        # one picture at a time; the model is not re-entrant
_reaper: Optional[threading.Timer] = None


def release() -> bool:
    """Drop the model and give the memory back. True when something was actually released.

    Takes the same lock as generation, so it can never pull the model out from under a picture
    that is halfway through.
    """
    global _pipe, _reaper
    with _lock:
        if _pipe is None:
            return False
        _pipe = None
        if _reaper is not None:
            _reaper.cancel()
            _reaper = None
    import gc

    gc.collect()
    # Python hands the pages back to its allocator, not to the kernel. malloc_trim is what
    # actually returns them, and without it the resident size barely moves.
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001 — a nicety, not a requirement
        pass
    return True


def _arm_reaper() -> None:
    """Release the model once it has gone unused for long enough."""
    global _reaper
    if _reaper is not None:
        _reaper.cancel()
    _reaper = threading.Timer(IDLE_RELEASE, _reap)
    _reaper.daemon = True       # never hold up a shutdown for this
    _reaper.start()


def _reap() -> None:
    if _pipe is not None and time.time() - _last_used >= IDLE_RELEASE - 1:
        release()


def _cache() -> Path:
    """The hub cache to read the weights from, passed explicitly rather than through HF_HOME.

    Setting the environment variable here does not work and the failure is quiet: huggingface_hub
    reads HF_HOME once, at its own import, and something else in Jarvis imports it long before
    anyone asks for a picture. The weights then get looked for under the default cache on the root
    disk, which reports "sd-turbo does not appear to have a file named model_index.json" — a
    missing-model message for a model that is present, 2.5 GB of it, on the other disk.

    An explicit directory cannot be beaten to the punch by an import order. A HF_HOME set by the
    user is still honoured, because that is a deliberate choice about where weights live.
    """
    return Path(os.environ.get("HF_HOME") or WEIGHTS) / "hub"


@dataclass(frozen=True)
class Picture:
    path: Path
    prompt: str
    seconds: float
    steps: int
    size: int


class Unavailable(RuntimeError):
    """The pieces needed to generate are not installed or not downloaded."""


def _load():
    """The pipeline, built once and kept. Raises Unavailable with a reason that helps."""
    global _pipe
    if _pipe is not None:
        return _pipe
    try:
        import torch
        from diffusers import AutoencoderTiny, AutoPipelineForText2Image
    except ImportError as exc:
        raise Unavailable(
            "the image model isn't installed — pip install torch diffusers transformers"
        ) from exc

    torch.set_num_threads(THREADS)
    # When the weights are already here, say so, and the load never touches the network. Left to
    # itself the hub checks for a newer revision on every single load: a round trip before each
    # picture, and a hang rather than a picture when the laptop is offline.
    where = {"cache_dir": str(_cache()), "local_files_only": ready()}
    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            MODEL, torch_dtype=torch.float32, variant="fp16", safety_checker=None, **where)
        pipe.vae = AutoencoderTiny.from_pretrained(DECODER, torch_dtype=torch.float32, **where)
    except Exception as exc:  # noqa: BLE001 — usually "not downloaded yet", worth saying plainly
        raise Unavailable(f"the image model weights aren't ready — {exc}") from exc

    pipe.to("cpu")
    pipe.set_progress_bar_config(disable=True)
    _pipe = pipe
    return _pipe


def ready() -> bool:
    """True when a picture can be made without downloading anything first.

    Deliberately checks for the files that are actually loaded rather than asking the hub whether
    the repository is complete. Only the half-precision weights were fetched — that is half the
    download and the only half this uses — so by the hub's reckoning the snapshot is permanently
    "incomplete" and a completeness check reports a working install as missing.
    """
    if _pipe is not None:
        return True
    try:
        import importlib.util

        if importlib.util.find_spec("diffusers") is None:
            return False
    except Exception:  # noqa: BLE001
        return False
    hub = _cache()
    unet = hub / f"models--{MODEL.replace('/', '--')}" / "snapshots"
    decoder = hub / f"models--{DECODER.replace('/', '--')}" / "snapshots"
    return (any(unet.glob("*/unet/*fp16*.safetensors"))
            and any(decoder.glob("*/*.safetensors")))


def _filename(prompt: str) -> Path:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in prompt)[:48].strip()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    SAVE_TO.mkdir(parents=True, exist_ok=True)
    return SAVE_TO / f"{stamp} {keep or 'picture'}.png".replace(" ", "_")


def generate(prompt: str, *, steps: int = DEFAULT_STEPS, size: int = DEFAULT_SIZE,
             seed: Optional[int] = None) -> Picture:
    """Make one picture and write it to disk. Blocking and processor-bound — call it off the loop."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("nothing to draw")

    # 512 is what the model was trained at; asking for much more gives you repeated subjects
    # rather than more detail, and costs twice the time. Rounded to the 8px the latent needs.
    size = max(256, min(768, int(size) // 8 * 8))
    steps = max(1, min(8, int(steps)))

    global _last_used
    with _lock:
        pipe = _load()
        _last_used = time.time()
        import torch

        generator = torch.Generator("cpu").manual_seed(seed) if seed is not None else None
        started = time.time()
        image = pipe(text, num_inference_steps=steps, guidance_scale=0.0,
                     height=size, width=size, generator=generator).images[0]
        elapsed = time.time() - started

    _last_used = time.time()
    _arm_reaper()
    path = _filename(text)
    image.save(path)
    return Picture(path=path, prompt=text, seconds=round(elapsed, 1), steps=steps, size=size)
