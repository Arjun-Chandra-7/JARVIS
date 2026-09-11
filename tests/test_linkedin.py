from jarvis.integrations import linkedin


def test_open_profile_uses_stored_copilot_url(monkeypatch):
    profile_url = "https://www.linkedin.com/in/arjun-chandra-0b0b5826a/"
    monkeypatch.setattr(linkedin, "_ensure", lambda: None)
    monkeypatch.setattr(
        linkedin,
        "_request",
        lambda path: {"values": {"linkedin_profile_url": profile_url}},
    )
    opened = []
    monkeypatch.setattr(
        linkedin.apps,
        "open_url",
        lambda url, browser="opera", **kwargs: opened.append((url, browser, kwargs)) or url,
    )

    result = linkedin.open_profile()

    assert opened == [(profile_url, "opera", {"new_window": True})]
    assert result == "Opened your LinkedIn profile."


def test_linkedin_tools_are_available_to_the_active_semantic_registry():
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config

    schemas, _ = build_registry(Config(), job_runner=None, confirm_fn=None)
    tools = {schema["function"]["name"]: schema["function"] for schema in schemas}

    assert "linkedin_open_profile" in tools
    assert "own public LinkedIn profile" in tools["linkedin_open_profile"]["description"]
    assert "linkedin_stats" in tools
    assert "performance" in tools["linkedin_stats"]["description"]


def test_plain_semantic_tool_label_is_executed_instead_of_spoken():
    from jarvis.agent.groq_core import _parse_calls

    assert _parse_calls("LinkedIn Open Profile - Opening your professional page") == [
        ("linkedin_open_profile", {})
    ]
    assert _parse_calls("LinkedIn Open Console - Opening the analytics view") == [
        ("linkedin_open_console", {"view": "analytics"})
    ]


def test_model_generated_linkedin_labels_dispatch_semantic_actions():
    from jarvis.agent.groq_core import _parse_calls

    response = (
        "LinkedIn Show Performance - Displaying performance metrics\n\n"
        "LinkedIn Open Console - Opening the LinkedIn Dashboard View"
    )

    assert _parse_calls(response) == [
        ("linkedin_stats", {}),
        ("linkedin_open_console", {"view": "dashboard"}),
    ]
