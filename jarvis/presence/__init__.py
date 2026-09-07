"""Human presence sensing: camera bearing/range, acoustic range, network identity.

Every contact carries its provenance and an honest confidence. A contact whose
bearing is genuinely unknown (acoustic-only) never gets a fabricated angle.
"""
from .types import Contact, SensorStatus, Snapshot  # noqa: F401
