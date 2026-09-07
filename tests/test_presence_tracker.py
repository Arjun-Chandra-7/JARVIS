"""Fusion: stable identities across frames, and evidence used only for what it proves."""
import pytest

from jarvis.presence.tracker import ACOUSTIC_GATE_M, COAST_S, Tracker
from jarvis.presence.types import Contact


def cam(distance, bearing, conf=0.9, label=None):
    return Contact(id="x", source="camera", distance_m=distance, bearing_deg=bearing,
                   confidence=conf, label=label)


def echo(distance, conf=0.6):
    return Contact(id="y", source="acoustic", distance_m=distance, bearing_deg=None,
                   confidence=conf, moving=True)


def net(label, conf=0.7):
    return Contact(id="z", source="network", label=label, confidence=conf)


def test_same_person_keeps_one_id_across_frames():
    t = Tracker()
    first = t.update([cam(2.0, 10.0)], now=0.0)
    second = t.update([cam(2.1, 12.0)], now=0.2)
    assert len(second.contacts) == 1
    assert first.contacts[0].id == second.contacts[0].id


def test_two_separated_people_stay_separate():
    t = Tracker()
    snap = t.update([cam(1.5, -25.0), cam(2.5, 25.0)], now=0.0)
    assert len(snap.contacts) == 2
    assert len({c.id for c in snap.contacts}) == 2


def test_smoothing_damps_a_noisy_frame():
    t = Tracker()
    t.update([cam(2.0, 0.0)], now=0.0)
    snap = t.update([cam(2.5, 0.0)], now=0.2)          # a noisy but plausible reading
    assert len(snap.contacts) == 1
    assert 2.1 < snap.contacts[0].distance_m < 2.4     # moved toward it, not all the way


def test_an_implausible_jump_becomes_a_different_person():
    """1 m in 0.2 s is 5 m/s — faster than walking, so it is not the same track."""
    t = Tracker()
    t.update([cam(2.0, 0.0)], now=0.0)
    snap = t.update([cam(3.2, 0.0)], now=0.2)
    assert len(snap.contacts) == 2


def test_acoustic_confirms_a_camera_track_without_moving_it():
    t = Tracker()
    t.update([cam(2.0, 30.0)], now=0.0)
    snap = t.update([cam(2.0, 30.0), echo(2.05)], now=0.2)
    assert len(snap.contacts) == 1, "the echo should merge, not create a phantom"
    c = snap.contacts[0]
    assert c.bearing_deg == pytest.approx(30.0, abs=0.1)   # bearing untouched by range-only data
    assert c.moving is True                                # but motion was learned
    assert "camera" in c.source and "acoustic" in c.source
    assert c.confidence > 0.9                              # corroborated by two sensors


def test_acoustic_alone_stays_bearingless():
    t = Tracker()
    snap = t.update([echo(1.4)], now=0.0)
    c = snap.contacts[0]
    assert c.bearing_deg is None and c.position() is None
    assert snap.people == 0, "a range-only echo is not a located person"
    assert snap.count == 1, "...but it is still reported as a contact"


def test_distant_echo_is_not_merged_into_a_track():
    t = Tracker()
    t.update([cam(1.0, 0.0)], now=0.0)
    snap = t.update([cam(1.0, 0.0), echo(1.0 + ACOUSTIC_GATE_M + 0.5)], now=0.2)
    assert len(snap.contacts) == 2


def test_network_supplies_a_name_and_no_geometry():
    t = Tracker()
    snap = t.update([net("Maya")], now=0.0)
    c = snap.contacts[0]
    assert c.label == "Maya" and c.distance_m is None and c.bearing_deg is None
    assert snap.people == 1, "a named person counts even without a position"


def test_a_named_track_is_not_duplicated_each_frame():
    t = Tracker()
    t.update([net("Maya")], now=0.0)
    snap = t.update([net("Maya")], now=0.5)
    assert len(snap.contacts) == 1


def test_tracks_coast_then_retire():
    t = Tracker()
    t.update([cam(2.0, 0.0)], now=0.0)
    coasting = t.update([], now=1.0)
    assert len(coasting.contacts) == 1
    assert coasting.contacts[0].confidence < 0.9           # decayed while unsupported
    gone = t.update([], now=COAST_S + 1.5)
    assert gone.contacts == []


def test_people_count_ignores_low_confidence():
    t = Tracker()
    snap = t.update([cam(2.0, 5.0, conf=0.2)], now=0.0)
    assert snap.count == 1 and snap.people == 0


def test_snapshot_serialises_unknown_bearing_safely():
    t = Tracker()
    data = t.update([echo(1.2), cam(2.0, -8.0)], now=0.0).to_dict()
    by_known = {c["bearing_known"]: c for c in data["contacts"]}
    assert by_known[False]["x_m"] is None and by_known[False]["y_m"] is None
    assert by_known[True]["x_m"] is not None
    assert data["people"] == 1 and data["count"] == 2


def voice(detail="voice heard"):
    """The passive audio sensor: no range, no bearing, no label, but a stable id."""
    return Contact(id="audio-voice", source="audio", confidence=0.65, detail=detail)


def test_one_voice_stays_one_contact_across_many_ticks():
    """Regression: bearingless contacts were matched by label only, so a label-less
    audio contact spawned a fresh track on every fusion tick — one voice became ten."""
    t = Tracker()
    for i in range(10):
        snap = t.update([voice()], now=i * 0.25)
    assert len(snap.contacts) == 1, f"expected 1 contact, got {len(snap.contacts)}"
    assert snap.contacts[0].source == "audio"


def test_a_voice_and_a_named_device_stay_distinct():
    t = Tracker()
    snap = t.update([voice(), net("Maya")], now=0.0)
    assert len(snap.contacts) == 2
    snap = t.update([voice(), net("Maya")], now=0.3)
    assert len(snap.contacts) == 2, "neither should duplicate on the second tick"


def test_voice_track_retires_when_it_stops():
    t = Tracker()
    t.update([voice()], now=0.0)
    assert t.update([], now=COAST_S + 1.0).contacts == []
