"""Does the shortlist still contain the right tool?

With a hundred tools, routing is the thing that regresses without anything failing. A new tool's
description overlaps an old one, the shortlist quietly stops containing the right answer, and
every other test in this suite still passes.

So this measures it. No LLM in the loop, deliberately: 2026 work on LLM-as-judge found no judge
uniformly reliable, rankings shifting by up to fourteen places across benchmarks, and test-retest
failures from paraphrase alone. A labelled list and an exact assertion has none of those
problems, costs nothing, and never flakes.

The floors below are set from what the router measures *today*. They are a ratchet, not a target:
if a change pushes recall below them the test fails and you find out why. If a change improves
things, raise them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jarvis.agent import tool_router

_spec = importlib.util.spec_from_file_location(
    "routing_corpus", Path(__file__).parent / "data" / "routing_corpus.py")
_corpus_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_corpus_module)
CORPUS = _corpus_module.CORPUS
QUESTIONS_THAT_MUST_NOT_WRITE = _corpus_module.QUESTIONS_THAT_MUST_NOT_WRITE

KEEP = 10                # the shortlist size groq_core actually asks for


@pytest.fixture(scope="module")
def schemas():
    """The real tool schemas, built the way the agent builds them."""
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config

    built, _dispatch = build_registry(Config(), None, None)
    assert len(built) > 50, "the registry came back suspiciously small"
    return built


def _names(schemas) -> set[str]:
    return {s.get("function", {}).get("name", "") for s in schemas}


def test_every_label_in_the_corpus_is_a_real_tool(schemas):
    """A corpus that drifts from the tool list measures nothing. This is what notices a rename."""
    known = _names(schemas)
    unknown = sorted({tool for _said, tool in CORPUS if tool not in known})
    assert not unknown, f"the corpus names tools that no longer exist: {unknown}"


def _shortlist(schemas, said):
    # Lexical only. The semantic path needs Ollama, which a test must not depend on — and the
    # lexical path is the fallback every machine without it uses, so it is worth pinning anyway.
    chosen = tool_router.select(schemas, said, keep=KEEP, use_semantic=False)
    return [s.get("function", {}).get("name", "") for s in chosen]


def test_the_right_tool_is_usually_in_the_shortlist(schemas, capsys):
    """Recall at ten: how often the answer is even on the table."""
    misses = []
    for said, expected in CORPUS:
        if expected not in _shortlist(schemas, said):
            misses.append((said, expected))

    recall = 1 - len(misses) / len(CORPUS)
    with capsys.disabled():
        print(f"\n  routing recall@{KEEP} (lexical): {recall:.1%} "
              f"({len(CORPUS) - len(misses)}/{len(CORPUS)})")
        for said, expected in misses[:12]:
            print(f"    miss: {said!r} -> wanted {expected}")

    # Measured at 87.1% (61/70) the day this was written, on the lexical path. The floor sits
    # just under that: a ratchet, not an aspiration. If a change improves it, raise the floor; if
    # a change drops it below, this is what says so.
    assert recall >= 0.80, f"routing recall fell to {recall:.1%}; misses: {misses[:10]}"


def test_a_question_does_not_put_a_write_tool_first(schemas):
    """The failure this guards against is the expensive one: a question routed to something that
    sends a message. The router already de-ranks write tools for questions — this is what keeps
    it doing that."""
    offenders = []
    for said in QUESTIONS_THAT_MUST_NOT_WRITE:
        top = _shortlist(schemas, said)[:1]
        if top and tool_router.is_write_tool(top[0]):
            offenders.append((said, top[0]))
    assert not offenders, f"a question ranked a write tool first: {offenders}"


def test_the_shortlist_is_actually_shorter_than_the_catalogue(schemas):
    """The whole point of routing is not paying for a hundred descriptions per turn."""
    chosen = _shortlist(schemas, "what's on my calendar today")
    assert len(chosen) <= KEEP + len(tool_router.ALWAYS)
    assert len(chosen) < len(schemas) / 2


@pytest.mark.parametrize("said,expected", [
    ("set a timer for ten minutes", "set_timer"),
    ("lock my screen", "lock_screen"),
    ("what's on my calendar today", "google_agenda"),
    ("read my screen", "capture_screen"),
])
def test_the_unmistakable_ones_rank_first(schemas, said, expected):
    """These have exactly one right answer and no near neighbour. If any of them stops ranking
    first, something is wrong with scoring rather than with the corpus."""
    assert _shortlist(schemas, said)[0] == expected
