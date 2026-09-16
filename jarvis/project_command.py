"""«tell me about this project» — the files are read first, then described.

Asked this, the local model answered "I'll check your files now" and "I'll look into your files
to find out more. Waiting for the results." — and then did nothing. Nothing was waiting; there is
no later. A promise with no action behind it is as useless as a false claim of having acted, and
harder to notice, because it sounds like progress.

So the reading is not left to the model's judgement. The files are gathered here, deterministically,
and the model is handed their contents with one job: say what this is. That is the split that has
worked everywhere else in this program — the machine does the retrieving, the model does the
language.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_ASK = re.compile(
    r"""^(?:please\s+)?(?:
        (?:read|look\s+at|go\s+through|check)\s+(?:my|the|these)?\s*(?:files?|code|codebase|repo|project)
            (?:\s+and\s+.*)?|
        (?:tell|explain|describe)\s+(?:me\s+)?(?:about\s+)?(?:this|my|the)\s+
            (?:project|codebase|repo|repository|code)(?:\s+.*)?|
        what(?:'?s|\s+is)\s+(?:this|my|the)\s+(?:project|codebase|repo|repository|code)\s*(?:about|for|do(?:ing)?)?|
        (?:summari[sz]e|sum\s+up)\s+(?:this|my|the)\s+(?:project|codebase|repo|code)
    )\s*\??$""",
    re.IGNORECASE | re.VERBOSE,
)

# Files that say what a project is, in the order a person would open them.
_TELLING = ("README.md", "readme.md", "README.rst", "README", "pyproject.toml",
            "package.json", "Cargo.toml", "go.mod", "HANDOFF.md", "CLAUDE.md")
# What a small local model can actually take in alongside its system prompt. Sent six thousand
# characters of README it returned nothing at all — not an error, an empty string.
MAX_CHARS = 2200


def wants_a_summary(text: str) -> bool:
    return bool(_ASK.match((text or "").strip().rstrip(".!?")))


def gather(folder: Optional[str] = None) -> Optional[dict]:
    """The listing plus whichever files actually explain the project."""
    from .integrations import coding

    root = folder or coding.active_folder()
    if not root:
        return None
    path = Path(root)
    if not path.is_dir():
        return None

    excerpts = []
    spent = 0
    for name in _TELLING:
        candidate = path / name
        if not candidate.is_file():
            continue
        try:
            body = candidate.read_text(errors="ignore").strip()
        except OSError:
            continue
        if not body:
            continue
        room = MAX_CHARS - spent
        if room <= 200:
            break
        excerpts.append((name, body[:room]))
        spent += min(len(body), room)

    return {"folder": str(path), "overview": coding.overview(str(path))[:2500],
            "files": excerpts}


async def handle(text: str, config=None) -> Optional[str]:
    """None means 'not mine'."""
    if not wants_a_summary(text):
        return None

    found = await _to_thread(gather)
    if found is None:
        return "I don't see a project open, sir — open one in your editor first."

    if not found["files"]:
        # Nothing that explains itself; the listing is still an honest answer.
        return (f"{Path(found['folder']).name} has no README or manifest to go on. "
                f"Its files:\n{found['overview'][:800]}")

    described = "\n\n".join(f"--- {name} ---\n{body}" for name, body in found["files"])
    prompt = (
        "Say what this project is, in three or four sentences, for someone who wrote it and "
        "wants reminding. Be concrete about what it does. Do not describe the files themselves.\n\n"
        f"Folder: {found['folder']}\n\n{described}\n\nFile listing:\n{found['overview'][:1200]}"
    )
    reply = await _summarise(prompt, config)

    if not reply:
        return f"I read {Path(found['folder']).name} but couldn't summarise it."
    return reply


async def _to_thread(fn, *args):
    import asyncio

    return await asyncio.to_thread(fn, *args)


async def _summarise(prompt: str, config=None) -> str:
    """One plain completion: no tools, no history, no chance of it deciding to do something else.

    Going through the full agent for this sent the file contents alongside ninety-four tool
    schemas and the whole system prompt, and the model answered with an empty string.
    """
    import asyncio

    from .config import CONFIG

    settings = config or CONFIG

    def ask() -> str:
        try:
            from openai import OpenAI

            base_url, api_key, model = settings.llm_params()
            client = OpenAI(base_url=base_url, api_key=api_key or "none",
                            max_retries=0, timeout=60)
            done = client.chat.completions.create(
                model=model,
                messages=[{"role": "system",
                           "content": "You describe software projects plainly and briefly."},
                          {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return (done.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001 - no summary is better than a traceback read aloud
            return ""

    return await asyncio.to_thread(ask)
