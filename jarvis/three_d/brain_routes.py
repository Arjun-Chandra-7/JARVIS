"""3D Studio's requests, routed by the Daily Brain — never by a router of its own.

3D Studio's reconstruction is local and deterministic: colour-layer segmentation, the scene
planner, the Blender bridge and the CPU validation rasteriser. They are registered with the Brain
as *engines* for the capabilities they serve, so every reconstruction is a Brain decision
(``capability.complete`` → route ``engine``, logged without content) that never needs a cloud
model. A capability no local engine serves (``image_to_3d``) needs a model verified for it, and
none is; the optional remote endpoint is handled below, behind the Brain's privacy policy *and* an
approval naming the host.

    mode           capabilities                                           served by
    vector         image_segmentation, structured_scene_planning,         imaging, planner,
                   blender_editing                                        blender_bridge
    dimensioned    structured_scene_planning, 3d_reasoning, blender_…    planner, blender_bridge
    parametric     image_segmentation, 3d_reasoning, blender_…,          + validation
                   multimodal_comparison
    organic        (as parametric)
    multiview      (as parametric)
    artistic       (as parametric)
"""
from __future__ import annotations

from ..brain import capability
from ..brain.request import Cap, Privacy

ENGINES = {
    Cap.SEGMENTATION: "three_d.imaging",
    Cap.SCENE_PLANNING: "three_d.planner",
    Cap.REASONING_3D: "three_d.planner",
    Cap.BLENDER: "three_d.blender_bridge",
    Cap.COMPARE: "three_d.validation",
}
_SHAPE = {Cap.SEGMENTATION, Cap.REASONING_3D, Cap.BLENDER, Cap.COMPARE}
MODE_CAPS = {
    "vector": {Cap.SEGMENTATION, Cap.SCENE_PLANNING, Cap.BLENDER},
    "dimensioned": {Cap.SCENE_PLANNING, Cap.REASONING_3D, Cap.BLENDER},
    "parametric": set(_SHAPE),
    "organic": set(_SHAPE),
    "multiview": set(_SHAPE),
    "artistic": set(_SHAPE),
    "edit": {Cap.SCENE_PLANNING, Cap.BLENDER},
}


def register() -> None:
    for cap, engine in ENGINES.items():
        capability.register_engine(cap, engine)


def route(mode: str, purpose: str = "") -> capability.CapabilityResult:
    """The Brain's decision for one reconstruction or edit. Screen references are sensitive and
    local-only: nothing here may choose a cloud model."""
    register()
    caps = MODE_CAPS.get(str(getattr(mode, "value", mode)), set(_SHAPE))
    return capability.complete(capability.CapabilityRequest(
        purpose=purpose or f"3d.{getattr(mode, 'value', mode)}", prompt="", capabilities=set(caps),
        privacy=Privacy.SENSITIVE, local_only=True, screenshot=True))


def remote_upload_verdict() -> tuple[bool, str]:
    """May a reference picture leave this machine at all, under the Brain's privacy settings?
    (An approval naming the host is still needed every time; this only decides whether one may be
    asked for.)"""
    from ..brain import daily
    from ..brain.registry import BrainSettings

    if not daily.enabled():
        return False, "sending pictures off this machine needs the Daily Brain's privacy policy, which is off"
    settings = BrainSettings()
    pv = settings["privacy"]
    if pv.get("mode") == "always_local":
        return False, "your privacy setting keeps everything on this machine"
    if not pv.get("allow_screenshots", True):
        return False, "screenshot uploads are turned off in the Brain's privacy settings"
    if not settings["limits"].get("external_vision", True):
        return False, "external image upload is turned off in the Brain's settings"
    return True, ""
