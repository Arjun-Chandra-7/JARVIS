"""Deterministic intent matching in front of the model. See router.py for the reasoning."""

from .router import IntentRouter, Resolution, live_slots, load_rules

__all__ = ["IntentRouter", "Resolution", "live_slots", "load_rules"]
