"""Hindi and Hinglish commands must reach the same handlers as their English equivalents."""
import pytest

from jarvis.hinglish import looks_hindi, normalise


@pytest.mark.parametrize("said,english", [
    # object-then-verb, which is the normal Hindi order
    ("YouTube kholo", "open YouTube"),
    ("Netflix chalu karo", "open Netflix"),
    ("Opera GX khol do", "open Opera GX"),
    ("Spotify band karo", "close Spotify"),
    ("gaana bajao", "play gaana"),
    # politeness and filler carry no instruction
    ("zara Netflix khol do na", "open Netflix"),
    ("bhai YouTube kholo please", "open YouTube"),
    # levels
    ("awaaz badhao", "turn the volume up"),
    ("awaaz kam karo", "turn the volume down"),
    ("brightness kam karo", "turn the brightness down"),
    ("roshni badhao", "turn the brightness up"),
    ("volume 50 kar do", "set the volume to 50"),
    ("brightness 30 karo", "set the brightness to 30"),
    ("awaaz kitni hai", "what is the volume"),
    ("mute karo", "mute"),
    ("awaaz band karo", "mute"),
    # two objects
    ("Netflix pe friends chalao", "play friends on Netflix"),
    ("YouTube par lofi dhoondo", "search lofi on YouTube"),
    # clicking
    ("allow button pe click karo", "click allow button on my screen"),
    ("allow dabao", "click allow on my screen"),
    # Devanagari, both word orders
    ("आवाज़ बढ़ाओ", "turn the volume up"),
    ("खोलो YouTube", "open YouTube"),
    ("चलाओ Netflix", "play Netflix"),
    ("आवाज़ 40 कर दो", "set the volume to 40"),
])
def test_hindi_becomes_the_english_the_handlers_know(said, english):
    assert normalise(said) == english


@pytest.mark.parametrize("said", [
    "open netflix",
    "what is the volume",
    "set the volume to 30",
    "play the latest video",
    "how are you",
    "what's the weather like today",
    "click the allow button on my screen",
    "",
])
def test_english_is_left_exactly_as_it_was(said):
    """A translation layer that edits English is worse than no translation layer."""
    assert normalise(said) == said


def test_unrecognised_hindi_is_passed_on_unchanged():
    """Better handed to the model as it was said than mangled into a command it is not."""
    said = "mujhe kal ke baare mein batao ki kya hua tha"
    assert normalise(said) == said


def test_the_cheap_check_does_not_fire_on_english():
    assert not looks_hindi("open netflix and turn the volume up")
    assert looks_hindi("YouTube kholo")
    assert looks_hindi("आवाज़ बढ़ाओ")
