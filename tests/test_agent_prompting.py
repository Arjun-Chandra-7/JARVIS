"""Turning what was said into the prompt the agent receives."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.coding import prompts


def test_chatgpt_is_told_the_model_it_is_writing_for():
    """The prompt is shaped by which agent and model will read it, so both are stated."""
    brief = prompts.brief("add a toggle", "claude", "opus", "medium", "/home/x/Dev/App")
    assert "CODING AGENT: claude" in brief
    assert "MODEL: opus" in brief
    assert "REASONING EFFORT: medium" in brief
    assert "WORKSPACE: /home/x/Dev/App" in brief
    assert "WHAT I SAID: add a toggle" in brief


def test_only_facts_are_sent():
    """How to rewrite lives in the account's custom instructions, where it can be edited without
    touching this code, so nothing here tells ChatGPT how to do its job."""
    brief = prompts.brief("add a toggle", "claude", "opus", "medium", "/w")
    for instructing in ("you are", "please", "rewrite", "make sure", "your task"):
        assert instructing not in brief.lower()


def test_handover_notes_are_passed_on_when_there_are_any():
    brief = prompts.brief("carry on", "codex", "gpt-5.6-terra", "high", "/w",
                          carrying_on="Claude did the parser, tests still failing")
    assert "HANDOVER NOTES" in brief and "tests still failing" in brief


@pytest.mark.parametrize("reply, prompt", [
    ("Add a dark mode toggle.", "Add a dark mode toggle."),
    ("Prompt: Add a toggle.", "Add a toggle."),
    ("Here is the prompt: Do the thing.", "Do the thing."),
    ("Here's the rewritten prompt — Add a toggle.", "Add a toggle."),
    ("```\nAdd a toggle to settings.\n```", "Add a toggle to settings."),
    ("```text\nAdd it.\n```", "Add it."),
])
def test_the_prompt_is_taken_out_of_whatever_shape_it_arrives_in(reply, prompt):
    assert prompts._just_the_prompt(reply) == prompt


def test_the_prompt_comes_back_as_one_line():
    """Typed into a terminal, a newline submits half a prompt."""
    got = prompts._just_the_prompt("Do this.\nThen do that.\nAnd finally that.")
    assert "\n" not in got


def test_silence_from_chatgpt_is_not_a_prompt():
    assert prompts._just_the_prompt("") is None
    assert prompts._just_the_prompt("   ") is None


def test_the_spoken_words_are_used_when_chatgpt_cannot_be_reached(monkeypatch):
    """A terse prompt is worth far more than no prompt, and this must never be why a request
    does nothing at all."""
    async def no_session():
        return None

    monkeypatch.setattr(prompts, "_reach_chatgpt", no_session)
    prompt, source = asyncio.run(
        prompts.rewrite("add a dark mode toggle", "claude", "opus", "medium", "/w"))
    assert prompt == "add a dark mode toggle"
    assert "not reachable" in source


def test_nothing_said_is_not_sent_anywhere():
    prompt, source = asyncio.run(prompts.rewrite("   ", "claude", "opus", "medium", "/w"))
    assert prompt == "" and "nothing was said" in source
