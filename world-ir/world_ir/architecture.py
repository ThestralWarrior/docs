"""Pieces shared by rooms, wall runs and buildings: openings and roofs."""

from typing import Literal, Optional

from pydantic import Field

from .common import Id, IRModel, cfg, ref


class Opening(IRModel):
    """A door, window or gap in a wall."""

    model_config = cfg("v1")

    id: Id
    type: Literal["door", "window", "archway", "gap"] = "door"
    wall: int = Field(ge=0, description="Edge index of the outline: edge n runs from point n to point n+1.")
    offset: float = Field(ge=0, description="Distance from the edge's start point to the opening's centre, metres.")
    width: float = Field(0.9, gt=0)
    height: float = Field(2.1, gt=0)
    sill: float = Field(0.0, ge=0, description="Height of the bottom edge. Windows are usually 0.8 to 1.0.")
    swing: Literal["in", "out", "sliding", "none"] = Field(
        "in", description="Doors only. 'in' swings into the room that owns the opening."
    )
    hinge: Literal["left", "right"] = Field("left", description="Seen from inside the owning room.")
    asset: Optional[Id] = Field(
        None, description="Door or window model. Empty draws a plain frame.", json_schema_extra=ref("asset")
    )
    connects_to: Optional[Id] = Field(
        None,
        description="The room on the other side. Empty means outside.",
        json_schema_extra=ref("node"),
    )
    open: float = Field(0.0, ge=0, le=1, description="How open it is drawn, 0 to 1.")
    clearance: Optional[float] = Field(
        None, gt=0, description="Overrides the default free zone in front of it, metres."
    )


class RoofSpec(IRModel):
    """Shape of a roof over a footprint."""

    model_config = cfg("v2")

    style: Literal["flat", "shed", "gable", "hip", "mansard", "gambrel", "dome", "pyramid"] = "gable"
    pitch_deg: float = Field(30.0, ge=0, le=75)
    overhang: float = Field(0.3, ge=0, description="Metres beyond the walls.")
    thickness: float = Field(0.15, gt=0)
    ridge_axis: Literal["x", "z", "auto"] = Field("auto", description="Gable and shed only.")
    material: Optional[Id] = Field(None, json_schema_extra=ref("material"))
