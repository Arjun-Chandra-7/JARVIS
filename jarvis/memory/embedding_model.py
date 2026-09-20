"""Which embedding model everything uses, said once.

`nomic-embed-text` was hard-coded as a default argument in five places across two subsystems —
the vault's semantic recall and the router that picks among a hundred tools. Changing it meant
finding all five and hoping, and because both subsystems cache vectors, changing it in four
places out of five produces an index quietly mixing two vector spaces, which does not error. It
returns worse answers.

Why this is worth doing at all
------------------------------
The most rigorous 2026 study of agent memory — MemDelta, a controlled one-variable-at-a-time
protocol on LongMemEval — found that **swapping only the embedding model, in an otherwise
identical pipeline, moved accuracy by 6.2 percentage points (p = 0.004)**. That was larger than
the gain from adopting any of the memory architectures it tested, several of which cost an LLM
call per write. It is the cheapest real improvement available here, and it was unreachable
because the model was five default arguments.

The 2026 small tier worth trying: `qwen3-embedding:0.6b` and `embeddinggemma`. Both support
instruction prefixes, which matters more for the router than any benchmark score — the router can
embed the *task* ("which tool does this request need") rather than generic similarity.

Changing it invalidates every cached vector
-------------------------------------------
Two models do not share a vector space, so a cosine between them is a number with no meaning. The
fingerprint the router already keeps includes the model name, so its cache rebuilds on its own.
The vault index does not, so `stale_vault_index` exists to say so — and the answer is to rebuild,
not to mix.
"""

from __future__ import annotations

import os

# What the vault was built with, and therefore what stays the default until somebody chooses.
DEFAULT = "nomic-embed-text"

# Set JARVIS_EMBED_MODEL to try another. Measure with tests/test_tool_routing.py before keeping
# it: MTEB is saturated and over-fit, and a two-point gain there may be nothing at all on these
# hundred tools.
ENV_VAR = "JARVIS_EMBED_MODEL"


def name() -> str:
    """The model every embedding in this program should be made with."""
    return os.environ.get(ENV_VAR, "").strip() or DEFAULT


def stale_vault_index(recorded: str | None) -> bool:
    """Whether a vault index was built with a different model than the one in use now.

    A cosine between two models' vectors is a number with no meaning, so an index that mixes
    them is worse than no index — and silently so, which is why this is checked rather than
    assumed.
    """
    return bool(recorded) and recorded != name()
