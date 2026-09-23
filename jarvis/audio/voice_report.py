"""`python -m jarvis --voice-report`: the voice's real latencies, from the safe metrics log."""
from __future__ import annotations

from . import voice_log

_ROWS = [
    ("barge_in", "onset_to_stop_ms", "Voice onset → Jarvis stops speaking"),
    ("barge_in", "detector_ms", "  of which: deciding it was the person"),
    ("barge_in", "onset_to_record_ms", "Voice onset → recording the interruption"),
    ("turn", "end_to_action_ms", "End of speech → action or answer begins"),
    ("turn", "end_to_first_audio_ms", "End of speech → first spoken word"),
    ("stt", "ms", "Transcription"),
    ("fast_path", "ms", "Local command, handled in the voice process"),
    ("tts", "ms", "Synthesis to first audio"),
]


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]


def render(limit: int = 500) -> str:
    lines = ["Voice latency (from ~/.local/state/jarvis/voice-metrics.jsonl)", ""]
    for event, field, label in _ROWS:
        values = [float(r[field]) for r in voice_log.recent(event, limit) if isinstance(r.get(field), (int, float))]
        if not values:
            lines.append(f"  {label:<48} no data yet")
            continue
        lines.append(f"  {label:<48} n={len(values):<4} median {_pct(values, 0.5):6.0f} ms"
                     f"   p90 {_pct(values, 0.9):6.0f} ms")
    wakes = voice_log.recent("wake", limit)
    if wakes:
        dropped = sum(1 for w in wakes if w.get("reason") == "no_request")
        lines += ["", f"  Wakes: {len(wakes)}, of which {dropped} heard no request (likely false wakes)"]
    return "\n".join(lines)
