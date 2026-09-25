"""Bounded self-repair: diagnose a reported bug, prepare a fix in an isolated worktree, test it,
and activate it only as the safety policy allows. See docs/SELF_REPAIR.md.

    classify.py  what kind of request this is (one classifier for everything)
    policy.py    what a repair may touch, run and activate — protected; loaded from the live checkout
    jobs.py      durable repair jobs, their state machine, and the audit log
    sandbox.py   worktrees, path checks, the git allowlist, walled-in test commands
    editor.py    who writes the change (the coding agent, boxed in; or a prepared recipe)
    checks.py    the diff boundary: scope, protected areas, size, dependencies, secrets
    pipeline.py  one job from evidence to activation
    activate.py  apply, restart, health-check, probe, roll back
    worker.py    the separate process a job runs in
    command.py   what the owner says: start, status, cancel, show, activate, undo
"""
