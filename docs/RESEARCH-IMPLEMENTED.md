# What was built from the capability research, and what was not

Four parallel surveys of open-source work in voice, desktop agents, agent memory and agent
infrastructure produced a ranked list of gaps. This is what came of it, what it measured, and —
more usefully six months from now — what was deliberately left alone and why.

## Built

### Browser control without restarting the browser

The largest item, because it had the best evidence: four of the ten entries in
`~/.local/share/jarvis/failures.jsonl` were the same sentence — *"Opera GX is running without
control enabled"* — and the remedy cost every open tab, so it was never taken.

`browser-extension/` is an MV3 extension using `chrome.debugger`; `jarvis/integrations/
browser_relay.py` is the loopback server it dials out to. The relay presents tabs in exactly the
shape Chromium's `/json` returns, so `_targets()` and `_Session` in `browser.py` work against it
unchanged.

**Verified end to end against a real Opera GX**, not just unit-tested: the extension loaded, the
worker connected, tabs were enumerated, and `Runtime.evaluate` read `"Example Domain"` out of a
live page. That run also found a bug no amount of reading would have — the first tab the browser
offered was Opera's own GX Corner, which returns *"The extensions gallery cannot be scripted."*
It is an ordinary `https` URL, so the scheme filter let it through, and it was first in the list.

### A sandbox, because the permission gate is a denylist

`permissions.py` already said so in its own docstring: *"It is a heuristic, not a sandbox."*
`jarvis/agent/sandbox.py` makes it enforcement. Two policies — `STRICT` (read-only system, one
scratch directory, no network) and `GUARDED` (your home, credentials masked), with `run_bash` on
the latter.

Measured rather than assumed: outside the sandbox `~/.ssh` holds `authorized_keys` and
`known_hosts`; inside `GUARDED`, `ls -A ~/.ssh` returns nothing. `STRICT` gets `000` from curl
where `GUARDED` gets `200`.

Running it found three bugs the design did not predict: `STRICT` had no `/tmp` so bwrap could not
chdir; `/etc/resolv.conf` is a symlink into `/run`, which the tmpfs replaced, so `network:true`
produced a namespace that could route and not resolve; and `bash -lc` printed a GIO warning into
every result.

`GUARDED` stops a command reading your keys. It does not stop one deleting your work, and the
module says so.

### A voice

Kokoro 82M, Apache-2.0, on the processor — loads in 1.0 s, synthesises at 2.4× realtime, 54
voices, `bm_george` by default.

The ONNX build deliberately: `pip install kokoro` pulls misaki → spacy, which does not build
here, and wants espeak-ng from apt. `kokoro-onnx` needs onnxruntime, already present.

Kokoro has **no emotion conditioning** and nothing here pretends otherwise. What it has is a
speed control, which is enough for four *deliveries* chosen from the words before any model sees
them — neutral, brisk, grave, warm. Trouble is checked before greeting, so *"Good morning, sir.
The overnight backup failed."* is read as a failure.

### An instrument for the router

`tests/test_tool_routing.py` over 73 labelled utterances. No LLM judge: 2026 work found none
uniformly reliable, with rankings shifting up to fourteen places across benchmarks.

Measured: **recall@10 = 87.7%** on the lexical path. The floor is 80% — a ratchet, not a target.

The drift test earned itself immediately by catching two labels invented rather than checked
(`open_url` and `launch_app` do not exist; they are `browser_open` and `open_app`).

### Memory

- **Hybrid retrieval, actually merged.** `recall()` printed two lists under two headings and left
  the reader to do the merging. Now BM25 ranks the keyword half — it was a yes/no filter, so a
  note mentioning a term once outranked the note about it, by filename — and reciprocal rank
  fusion merges by position, because a cosine and a BM25 score do not add.
- **Bi-temporal facts.** `- Machine: ThinkPad X1 <!-- until:2026-03-04 -->`. One file answers
  both "what is true now" and "what was true in January". Unstamped lines are true and always
  have been, so the existing vault needs no migration.
- **Decay.** A recency prior on the score, applied *after* fusion so it breaks ties rather than
  competing with relevance. Nothing is deleted.
- **One name for the embedding model.** It was five default arguments across two subsystems, and
  changing four of five silently mixes two vector spaces. `JARVIS_EMBED_MODEL` now selects it.

### Document search

`find_document` — tool 101. There was no way to answer "find that PDF about the hackathon", which
is how people look for their own files. BM25 over extracted text, no index on disk (a full fresh
scan of the real folders is ~1.5 s, faster than deciding whether a cache is stale).

Found by running it: the size limit was shared between formats and wrong. A 13 MB illustrated
guide was thrown out as a dataset; it holds 23,711 characters of text.

## Deliberately not built

The research's most useful output was the list of things **not** to adopt. Recorded here so the
same ground is not covered twice.

| Not adopted | Why |
|---|---|
| **MCP server** (exposing our tools) | Serialisation and a second schema source of truth, to serve exactly one client. An MCP *client* is still worth having; that is unbuilt, not rejected. |
| **mem0, Letta, Graphiti, cognee** | Every one imports a graph DB, a Postgres+Redis stack, or an LLM call per write, for ideas that are a few hundred lines against the vault we have. The four schema ideas were taken; the code was not. |
| **LiteLLM** | Already built here, and ours handles a browser-driven ChatGPT backend it does not. |
| **vLLM** | Wants ≥16 GB VRAM. There are 674 MiB. |
| **Outlines / XGrammar** | `llama.cpp` does constrained decoding natively. |
| **NeMo-Guardrails, Guardrails AI, PurpleLlama** | Extra LLM calls or a DSL, to express rules already expressed in Python. The deterministic pre-model layer *is* the architecture they sell. |
| **Agent frameworks** | All would invert control of a loop already built and tested. |
| **Chatterbox, Kyutai DSM** | ~10 GB VRAM and datacentre cards respectively. |
| **Screenpipe** | The largest genuine capability gap, and the largest privacy decision. Not a weekend's work, and the retention policy has to be decided before the first line. |

## The honest caveat

The one number worth remembering from the memory literature: a controlled study found that
**swapping only the embedding model moved accuracy 6.2 points** — more than adopting any memory
architecture it tested. Nothing here has been measured against that, because the model has not
been swapped yet. `JARVIS_EMBED_MODEL` and `tests/test_tool_routing.py` are the two halves of
that experiment; running it is the next thing worth doing.
