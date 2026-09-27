"""Runtime settings the owner can change by asking: typed, persisted, applied live, verified.

    registry.py  what each setting is — type, range, default, component, aliases
    runtime.py   how a change reaches the running components, and how they report back
    parse.py     "disable your animations", "thoda tez bolo" → a change request
    command.py   the deterministic handler that ties them together and answers

None of this edits source code. A request that needs code goes to ``jarvis.selfrepair``.
"""
