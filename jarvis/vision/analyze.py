"""Image understanding for a text-only brain (Groq): route a picture through a vision model and
return a text description the brain can act on.

Two backends, auto-selected:
  - ollama : fully local (e.g. `moondream`), $0 and unlimited — great with a GPU.
  - gemini : Google's free API (needs GEMINI_API_KEY), no local resources.
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path
from typing import Optional

_DEFAULT_Q = "Describe what is on this screen in detail, including any text, windows, and errors."


def _b64(path: str) -> Optional[str]:
    try:
        return base64.b64encode(Path(path).read_bytes()).decode()
    except Exception:  # noqa: BLE001
        return None


def _ollama_up(model: str) -> bool:
    import httpx

    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=2).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
        return any(n == model or (":" not in model and n.split(":", 1)[0] == model)
                   for n in names)
    except Exception:  # noqa: BLE001
        return False


def _ollama(path: str, question: str, model: str) -> Optional[str]:
    import httpx

    img = _b64(path)
    if not img:
        return None
    payload = {"model": model, "prompt": question, "images": [img], "stream": False,
               "think": False, "options": {"num_predict": 350}}
    try:
        for _ in range(2):  # first call may return empty while the model cold-loads
            r = httpx.post("http://localhost:11434/api/generate", json=payload, timeout=120)
            out = (r.json().get("response") or "").strip()
            if out:
                return out
        return None
    except Exception as exc:  # noqa: BLE001
        return f"(vision error: {exc})"


def _gemini(path: str, question: str, key: str, model: str = "gemini-3.6-flash") -> Optional[str]:
    import httpx

    img = _b64(path)
    if not img:
        return None
    try:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": key},
            json={"contents": [{"parts": [
                {"text": question},
                {"inline_data": {"mime_type": "image/jpeg", "data": img}},
            ]}]},
            timeout=30,
        )
        j = r.json()
        if not r.is_success:
            message = (j.get("error") or {}).get("message", f"HTTP {r.status_code}")
            return f"(vision error: {message[:200]})"
        return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"]).strip()
    except Exception as exc:  # noqa: BLE001
        return f"(vision error: {exc})"


def available(config) -> str | None:
    prov = getattr(config, "vision_provider", "auto")
    if prov == "none":
        return None
    if prov == "gemini":
        return "gemini" if config.gemini_api_key else None
    if prov == "ollama":
        return "ollama" if _ollama_up(config.ollama_vision_model) else None
    # auto: local first (free, no quota), then Gemini
    if _ollama_up(config.ollama_vision_model):
        return "ollama"
    return "gemini" if config.gemini_api_key else None


def describe(path: str, question: str, config) -> Optional[str]:
    """Return a text description/answer about the image, or None if no vision backend is set up."""
    q = question or _DEFAULT_Q
    prov = available(config)
    if prov == "ollama":
        return _ollama(path, q, config.ollama_vision_model)
    if prov == "gemini":
        return _gemini(path, q, config.gemini_api_key,
                       getattr(config, "gemini_model", "gemini-3.6-flash"))
    return None


def ground(path: str, target: str, config) -> tuple[Optional[str], str]:
    """Ground one target. Qwen boxes use normalized 0-1000 coordinates."""
    import json

    model = getattr(config, "ground_model", "qwen3.5:4b")
    provider = getattr(config, "vision_provider", "auto")
    if provider == "none":
        return None, "pixel"
    if provider in ("auto", "ollama") and model and _ollama_up(model):
        if "playlist" in target.casefold():
            title = re.sub(r"\b(?:the|my|playlist)\b", "", target, flags=re.I).strip()
            what = (f"the exact VISIBLE playlist title text {json.dumps(title)}. "
                    "Put the box tightly around that text, not around a nearby icon or app window")
        else:
            what = f"the exact visible GUI target {json.dumps(target)}"
        question = (f"Locate {what}. Return ONLY JSON with bbox_2d "
                    "[x1,y1,x2,y2] normalized to 0-1000 and its visible label. "
                    "Return [] if absent. Include all matches if ambiguous; do not guess.")
        return _ollama(path, question, model), "bbox_1000"
    question = (
        f"Locate one visible GUI target described as {json.dumps(target)}. "
        "Return ONLY JSON with its center coordinates in screenshot pixels, confidence 0-1, "
        "and visible evidence, or {\"found\": false} if absent or ambiguous."
    )
    return describe(path, question, config), "pixel"


def verify_point(path: str, target: str, x: int, y: int, config) -> bool:
    """Ask a vision model whether a grounded target is inside a marked crop."""
    from PIL import Image, ImageDraw

    model = getattr(config, "ground_model", "qwen3.5:4b")
    if not model or not _ollama_up(model):
        return False
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
        if not (0 <= x < image.width and 0 <= y < image.height):
            return False
        left, top = max(0, x - 160), max(0, y - 100)
        right, bottom = min(image.width, x + 160), min(image.height, y + 100)
        crop = image.crop((left, top, right, bottom))
        cx, cy = x - left, y - top
        draw = ImageDraw.Draw(crop)
        draw.rectangle((cx - 22, cy - 22, cx + 22, cy + 22), outline="red", width=3)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as tmp:
            crop.save(tmp.name, "JPEG", quality=85)
            answer = _ollama(tmp.name,
                             "What exact text or app/control name is INSIDE the red box? "
                             "Reply with only that name, or NONE if empty. Ignore all items "
                             "outside the red box.", model)
        return matches_verified_label(target, answer or "")
    except Exception:
        return False


def matches_verified_label(target: str, answer: str) -> bool:
    wanted = {w for w in re.findall(r"[\w']+", target.casefold())
              if len(w) > 2 and w not in {"the", "button", "icon", "playlist", "item", "here"}}
    observed = set(re.findall(r"[\w']+", answer.casefold()))
    if observed & {"not", "no", "none", "outside", "nearby", "unsure"}:
        return False
    return bool(wanted and wanted & observed)
