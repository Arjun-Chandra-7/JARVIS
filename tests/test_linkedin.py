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
    monkeypatch.setattr(linkedin.apps, "open_url", lambda url: opened.append(url) or url)

    result = linkedin.open_profile()

    assert opened == [profile_url]
    assert result == "Opened your LinkedIn profile."
