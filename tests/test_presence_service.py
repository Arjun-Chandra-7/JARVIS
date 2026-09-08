"""Service wiring: sensor opt-in, and a spoken summary that never overstates."""
import pytest

from jarvis.presence import service as svc
from jarvis.presence.tracker import Tracker
from jarvis.presence.types import Contact, SensorStatus


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    svc._service = None
    monkeypatch.delenv("JARVIS_PRESENCE", raising=False)
    yield
    svc._service = None


def test_default_sensor_set_is_camera_free():
    """Defaults must sense something with no webcam — an inverted/lid-shut laptop."""
    assert svc.enabled_sensors() == {"audio", "bluetooth", "network"}
    assert "camera" not in svc.enabled_sensors(), "camera is opt-in; some setups have no usable lens"
    assert "acoustic" not in svc.enabled_sensors(), "sonar holds the speaker; must be opt-in"


def test_summary_reports_bluetooth_device_count_when_nothing_placed():
    from jarvis.presence.types import SensorStatus
    s = svc.PresenceService(config=None)
    s._snapshot = Tracker().update([], [SensorStatus("bluetooth", True, "3 devices near, 3 anon")], now=0.0)
    svc._service = s
    text = svc.summary()
    assert "3 Bluetooth devices nearby" in text and "probably someone" in text


def test_audio_only_contact_admits_it_has_no_direction():
    _service_with([Contact(id="a", source="audio", confidence=0.65, detail="voice heard")])
    text = svc.summary()
    assert "hear someone" in text and "can't tell where" in text
    assert "left" not in text and "right" not in text and "metres" not in text


@pytest.mark.parametrize("value,expected", [
    ("camera", {"camera"}),
    ("camera,acoustic", {"camera", "acoustic"}),
    (" Camera , Network ", {"camera", "network"}),
    ("off", set()),
    ("none", set()),
    ("0", set()),
])
def test_sensor_opt_in_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("JARVIS_PRESENCE", value)
    assert svc.enabled_sensors() == expected


def _service_with(contacts, sensors=None):
    s = svc.PresenceService(config=None)
    s._snapshot = Tracker().update(contacts, sensors or [], now=0.0)
    svc._service = s
    return s


def test_summary_when_nothing_is_seen():
    _service_with([])
    assert "Nobody I can detect" in svc.summary()


def test_summary_surfaces_a_blocked_sensor_instead_of_claiming_empty():
    _service_with([], [SensorStatus("camera", False, "no light (mean 3/255) — privacy shutter closed")])
    text = svc.summary()
    assert "can't see anyone" in text and "shutter" in text


def test_summary_reports_side_and_range_for_a_located_person():
    _service_with([Contact(id="a", source="camera", distance_m=1.8, bearing_deg=-30.0, confidence=0.9)])
    text = svc.summary()
    assert "One person" in text and "1.8 metres" in text and "to your left" in text


def test_summary_uses_ahead_when_centred():
    _service_with([Contact(id="a", source="camera", distance_m=2.0, bearing_deg=3.0, confidence=0.9)])
    assert "ahead" in svc.summary()


def test_summary_counts_multiple_people():
    _service_with([
        Contact(id="a", source="camera", distance_m=1.2, bearing_deg=-20.0, confidence=0.9),
        Contact(id="b", source="camera", distance_m=2.4, bearing_deg=18.0, confidence=0.9, label="Maya"),
    ])
    text = svc.summary()
    assert "2 people" in text and "Maya" in text


def test_summary_admits_unknown_bearing_for_echoes():
    _service_with([Contact(id="e", source="acoustic", distance_m=1.6, bearing_deg=None, confidence=0.6)])
    text = svc.summary()
    assert "1.6 m" in text and "bearing unknown" in text
    assert "left" not in text and "right" not in text, "must not invent a side"


def test_named_only_contact_is_described_as_by_device():
    _service_with([Contact(id="n", source="network", label="Maya", confidence=0.7)])
    assert "by device" in svc.summary()


def test_who_is_around_command_routes_to_presence(monkeypatch):
    """The voice phrase must reach the presence summary, not the LLM."""
    import asyncio
    from jarvis import commands
    from jarvis.config import Config

    monkeypatch.setattr(svc, "summary", lambda config=None: "One person: someone about 1.2 metres ahead.")
    for phrase in ("Jarvis who's around", "who is here", "is anyone nearby",
                   "scan the room", "human radar"):
        reply = asyncio.run(commands.handle(phrase, Config()))
        assert reply is not None and "1.2 metres" in reply, phrase


def test_who_is_around_tool_is_registered():
    from jarvis.agent.groq_tools import build_registry
    from jarvis.config import Config
    schemas, _ = build_registry(Config(), None, None)
    names = {s["function"]["name"] for s in schemas}
    assert "who_is_around" in names and "remember_device" in names
