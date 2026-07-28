"""Speech-to-text via Deepgram's pre-recorded HTTP endpoint (raw PCM in, transcript out).

Plain HTTP (httpx) rather than the SDK, to avoid the SDK's cross-version API churn.
"""

from __future__ import annotations

import httpx

_URL = "https://api.deepgram.com/v1/listen"


def transcribe(
    pcm_bytes: bytes,
    api_key: str,
    sample_rate: int = 16000,
    model: str = "nova-3",
    timeout: float = 30.0,
) -> str:
    if not pcm_bytes:
        return ""
    params = {
        "model": model,
        "encoding": "linear16",
        "sample_rate": str(sample_rate),
        "channels": "1",
        "smart_format": "true",
        "punctuate": "true",
    }
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/octet-stream"}
    resp = httpx.post(_URL, params=params, headers=headers, content=pcm_bytes, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    try:
        return data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError):
        return ""
