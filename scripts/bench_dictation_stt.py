"""Benchmark dictation speech-to-text on this machine: latency, accuracy, CPU and memory.

    .venv/bin/python scripts/bench_dictation_stt.py [--providers groq-turbo,groq-v3,local-small]

The test speech is synthesised with the local Kokoro voices (English and Hindi), so it is the
same every run and contains no one's real voice; audio exists only in memory. Accuracy is word
error rate for English and character error rate for Hindi and Hinglish, against the text that
was spoken. Synthetic speech is cleaner than a person in a room, so treat the numbers as an
upper bound on accuracy and a fair comparison between providers.
"""
from __future__ import annotations

import argparse
import os
import re
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = [
    ("en", "af_heart", "I think we should meet tomorrow at six and finish the Viralyst dashboard."),
    ("en", "am_michael", "Please push the fix to GitHub and update the NCERT chapter notes."),
    ("en", "af_heart", "Buy eggs, milk and bread, then call Riya about the physics project."),
    ("hi", "hf_alpha", "आज मुझे विज्ञान का अध्याय दोहराना है।"),
    ("hi", "hm_omega", "कल गणित का गृहकार्य जमा करना है।"),
    ("hi", "hf_alpha", "पाइथागोरस प्रमेय में कर्ण का वर्ग बाकी दोनों भुजाओं के वर्गों के योग के बराबर होता है।"),
    ("mix", "hf_alpha", "कल maths का homework submit करना है।"),
    ("mix", "hm_omega", "आज electricity का chapter revise करना है।"),
]


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\sऀ-ॿ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _edit(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def error_rate(ref: str, hyp: str, chars: bool) -> float:
    r, h = _norm(ref), _norm(hyp)
    a, b = (list(r.replace(" ", "")), list(h.replace(" ", ""))) if chars else (r.split(), h.split())
    return _edit(a, b) / max(1, len(a))


def synth(text: str, voice: str) -> bytes:
    import numpy as np
    from scipy.signal import resample_poly

    from jarvis.audio import kokoro_tts
    model = kokoro_tts._get_pipeline(voice)
    samples, rate = model.create(text, voice=voice, speed=1.0, lang="hi" if voice.startswith("h") else "en-us")
    audio = resample_poly(np.asarray(samples, dtype=np.float32), 16000, int(rate))
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()


def providers(names: list[str]):
    from jarvis.flow import dictionary, stt
    vocab = dictionary.vocabulary()
    table = {}
    for name in names:
        if name.startswith("groq-"):
            model = {"groq-turbo": "whisper-large-v3-turbo", "groq-v3": "whisper-large-v3"}[name]

            def run(pcm, model=model):
                os.environ["JARVIS_DICTATION_GROQ_MODEL"] = model
                return stt.groq(pcm, 16000, vocab)[0]
        else:
            size = name.split("-", 1)[1]

            def run(pcm, size=size):
                return stt.local(pcm, 16000, vocab, "auto", model=size)[0]
        table[name] = run
    return table


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="groq-turbo,groq-v3,local-base,local-small")
    args = ap.parse_args()
    names = [n.strip() for n in args.providers.split(",") if n.strip()]
    clips = [(lang, text, synth(text, voice)) for lang, voice, text in SAMPLES]
    print(f"{len(clips)} clips, {sum(len(c[2]) for c in clips) / 32000:.1f} s of speech\n")
    for name, run in providers(names).items():
        try:
            run(clips[0][2])                                   # warm: model load / connection
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: unavailable ({type(exc).__name__}: {str(exc)[:80]})\n")
            continue
        ru0 = resource.getrusage(resource.RUSAGE_SELF)
        rows = []
        for lang, text, pcm in clips:
            t = time.monotonic()
            try:
                heard = run(pcm)
            except Exception as exc:  # noqa: BLE001
                heard = f"<{type(exc).__name__}>"
            rows.append((lang, time.monotonic() - t, error_rate(text, heard, chars=lang != "en"), text, heard))
        ru1 = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)
        print(f"== {name}   cpu {cpu:.1f}s for {len(rows)} clips   peak rss {ru1.ru_maxrss / 1024:.0f} MB")
        for lang in ("en", "hi", "mix"):
            sub = [r for r in rows if r[0] == lang]
            if sub:
                lat = sorted(r[1] for r in sub)
                print(f"   {lang:3}  latency median {lat[len(lat) // 2]:.2f}s max {lat[-1]:.2f}s   "
                      f"{'WER' if lang == 'en' else 'CER'} {sum(r[2] for r in sub) / len(sub):.1%}")
        for lang, secs, err, text, heard in rows:
            print(f"   [{lang}] {secs:.2f}s {err:5.1%}  {heard}")
        print()


if __name__ == "__main__":
    main()
