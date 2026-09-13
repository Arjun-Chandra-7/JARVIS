# How Jarvis is put together

Written after the September 2026 restructure. The short version: an utterance goes through three
gates, each cheaper than the next one, and most utterances never reach the expensive one.

```
utterance
   │
   ├─ commands.handle()          explicit slash-style commands
   │
   ├─ intents.IntentRouter       regex templates -> a tool call        ~20 microseconds
   │     .intent files, anchored, typed captures, live slots
   │
   └─ the model                  retrieved tools + retrieved memory     seconds
         tool_router  -> ~14 relevant schemas of ~85
         memory.store -> facts and episodes for this turn
         knowledge    -> standing notes whose triggers fired
```

## Tools

`jarvis/tools/<domain>.py` — one file per area. A tool is a decorated function:

```python
@tool("set_volume", "Set output volume percent (0-150).",
      {"percent": {"type": "string"}}, ["percent"], side_effects=True)
async def set_volume(ctx, a):
    ...
```

Everything true about a tool is stated at the definition. `parallel_safe` tells the agent loop it
may batch the call; `side_effects` tells the audit it must not invoke it; `requires` keeps a tool
out of the menu when its subsystem is unavailable. These used to be hand-maintained sets in
`groq_core.py` and `scripts/audit.py`, which drifted within hours of being written — hence the rule
that a fact about a tool lives with the tool.

`ctx` carries config, the job runner and the confirmation callback. Nothing is closed over, so a
tool can be tested on its own.

Adding an ability is adding a function to a domain module, or a new module. Discovery is automatic.

## The fast path

`jarvis/intents/rules/*.intent` map sentence templates to tools:

```
[set_timer]
[set|start|put] a timer for {seconds:duration}
```

Capture names are the tool's argument names. Templates are anchored — they match the whole
normalised utterance or not at all, so "don't set a timer" cannot fire "set a timer" — and a
failed type conversion is a failed match. Anything unmatched falls through to the model, so this
is only ever a shortcut. Measured: 0.01-0.23s versus 5-9s.

`$slots` are filled from live data at load time, so "message $contacts" resolves against the real
address book rather than the model guessing at a half-heard name.

## Memory

One SQLite file, `<vault>/.jarvis/memory.db`:

| kind | what it is | written by |
|---|---|---|
| `note` | a chunk of a vault Markdown file | `--index`, incremental on mtime |
| `episode` | something that was said, by whom, when | every turn |
| `fact` | a durable distilled statement | nightly consolidation |

Search is BM25 (FTS5) and cosine over nomic-embed vectors, fused with Reciprocal Rank Fusion so
agreement between the two compounds and either alone still surfaces something. Retrieval runs
automatically every turn — waiting for the model to call `recall` never worked on a 3B model.

Nightly consolidation distils episodes into facts and supersedes contradictions, so the store gets
sharper rather than merely bigger.

`Jarvis/knowledge/*.md` holds standing instructions, injected only when their trigger words appear.
Facts are evidence; knowledge notes are policy.

## Frontends

The Electron overlay (`overlay/`) is the primary interface. `reactor.js` is one WebGL2 quad of
signed distance fields — every arc is a live measurement, and nothing animates at constant velocity
unless it depicts something that really sweeps. The browser HUD (`webui/`) is a fallback for
machines without Electron.

Both talk to `jarvis/webserver.py`. `/chat/stream` is the one to use; `/chat` is kept for callers
that cannot consume SSE.

## Checking it

```bash
.venv/bin/python -m pytest -q                # unit tests
.venv/bin/python scripts/audit.py            # exercises every subsystem against the live machine
xvfb-run -a node scripts/overlay-smoke.js    # loads the real overlay and reports what came up
.venv/bin/python scripts/hud-shot.py o.png --demo
```

`audit.py` reports side-effecting tools as explicitly skipped rather than passed, so it never
overstates what it verified.
