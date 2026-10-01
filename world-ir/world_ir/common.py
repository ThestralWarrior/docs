"""Shared types and conventions for the world IR. See CONVENTIONS below."""

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CONVENTIONS: list[str] = [
    "Units are metres, degrees, seconds and kilograms. Light intensity is in candela for point "
    "and spot lights, lux for directional lights and the sun, and nits for area lights. "
    "Colour temperature is in kelvin.",
    "Axes are right-handed with +Y up. An asset's front faces +Z in its own frame, as in glTF.",
    "Yaw is a rotation about +Y in degrees. Yaw 0 means the front faces +Z; positive yaw turns +Z toward +X.",
    "Transforms are local to the parent node, so children move with their parent.",
    "An asset's origin is the centre of the bottom face of its bounding box, so pos[1] is the height of its base.",
    "Colours are sRGB hex strings, #rrggbb.",
    "IDs are lowercase slugs (letters, digits, underscores) and are unique across the whole world file, "
    "including room openings.",
    "Walls are written '<room_id>:<edge>'. Edge n runs from outline point n to point n+1.",
    "Unknown fields are rejected. Put tool-specific data in an 'extras' field.",
]

Tier = Literal["v1", "v2", "later"]

Id = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$"),
    Field(description="Lowercase slug, unique within the world file."),
]
Color = Annotated[
    str,
    StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$"),
    Field(description="sRGB hex colour, #rrggbb."),
]
WallRef = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}:[0-9]{1,3}$"),
    Field(
        description=(
            "A wall, written '<room_id>:<edge>'. Edge n runs from outline " "point n to point n+1 of that room."
        )
    ),
]
Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Polygon2 = Annotated[
    list[Vec2],
    Field(min_length=3, description="Closed polygon of [x, z] points, metres."),
]


def cfg(tier: Tier, **extra: Any) -> ConfigDict:
    """Model config: strict fields plus the tier tag the docs read."""
    return ConfigDict(
        extra="forbid",
        json_schema_extra={"x-tier": tier, **extra},
    )


def ref(kind: str, tier: Optional[Tier] = None, **extra: Any) -> dict[str, Any]:
    """Field metadata marking a cross-reference that the world validates."""
    meta: dict[str, Any] = {"x-ref": kind, **extra}
    if tier:
        meta["x-tier"] = tier
    return meta


def tiered(tier: Tier, **extra: Any) -> dict[str, Any]:
    """Field metadata for a field whose tier differs from its model's tier."""
    return {"x-tier": tier, **extra}


class IRModel(BaseModel):
    """Base class: unknown fields are rejected. Use ``extras`` for anything else."""

    model_config = cfg("v1")


class Transform(IRModel):
    """Position, rotation and scale relative to the parent node."""

    model_config = cfg("v1")

    pos: Vec3 = Field(
        (0.0, 0.0, 0.0),
        description="Position [x, y, z] in metres. For assets, y is the height of the base.",
        json_schema_extra={"x-repair": "v1"},
    )
    yaw: Optional[float] = Field(
        None,
        description="Rotation about +Y in degrees. Use this for almost everything.",
        json_schema_extra={"x-repair": "v1"},
    )
    rot: Optional[Vec3] = Field(
        None,
        description="Euler rotation [x, y, z] in degrees, applied in XYZ order. For tilted objects.",
        json_schema_extra={"x-tier": "v2"},
    )
    quat: Optional[Quat] = Field(
        None,
        description="Rotation as a quaternion [x, y, z, w]. For imported data.",
        json_schema_extra={"x-tier": "later"},
    )
    scale: Union[float, Vec3] = Field(
        1.0,
        description="Uniform scale, or [x, y, z]. Applied after the asset's own normalisation.",
        json_schema_extra={"x-repair": "v1"},
    )

    @model_validator(mode="after")
    def _one_rotation(self) -> "Transform":
        given = [name for name in ("yaw", "rot", "quat") if getattr(self, name) is not None]
        if len(given) > 1:
            raise ValueError(f"give at most one of yaw, rot, quat (got {', '.join(given)})")
        return self


class Semantic(IRModel):
    """What an object is and what it is for."""

    model_config = cfg("v1")

    category: str = Field(description="Plain category, e.g. 'nightstand', 'oak tree'.")
    synset: Optional[str] = Field(
        None, description="WordNet synset, e.g. 'nightstand.n.01' (as in objathor annotations)."
    )
    function: Optional[str] = Field(
        None, description="What the object is used for here, e.g. 'sleep', 'work', 'storage'."
    )
    group: Optional[str] = Field(
        None, description="Functional group it belongs to, e.g. 'reading_corner', 'dining_set'."
    )


class Support(IRModel):
    """What a node rests on. The floating and sinking checks use this."""

    model_config = cfg("v1")

    on: Literal["floor", "terrain", "node", "wall", "ceiling", "none"] = Field(
        description="'none' means deliberately unsupported, e.g. a hovering sign."
    )
    target: Optional[Id] = Field(
        None,
        description="When on='node': the node this rests on.",
        json_schema_extra=ref("node"),
    )
    surface: Optional[str] = Field(
        None,
        description="Named surface of the target's asset, e.g. 'top' or 'shelf_2'.",
    )
    wall: Optional[WallRef] = Field(
        None,
        description="When on='wall': which wall it is mounted on.",
        json_schema_extra=ref("wall"),
    )

    @model_validator(mode="after")
    def _target_matches(self) -> "Support":
        if self.on == "node" and not self.target:
            raise ValueError("support on='node' needs a target")
        if self.on == "wall" and not self.wall:
            raise ValueError("support on='wall' needs a wall")
        return self


class Physics(IRModel):
    """How the object takes part in collisions and simulation."""

    model_config = cfg("v2")

    body: Literal["static", "dynamic", "kinematic", "none"] = Field(
        "static", description="'none' turns off collisions for this node."
    )
    collider: Literal["auto_box", "box", "sphere", "capsule", "convex", "mesh", "none"] = Field(
        "auto_box", description="'auto_box' uses the asset's bounding box."
    )
    mass_kg: Optional[float] = Field(None, gt=0, description="Overrides the asset's mass.")
    friction: float = Field(0.6, ge=0)
    restitution: float = Field(0.0, ge=0, le=1, description="Bounciness, 0 to 1.")


class Provenance(IRModel):
    """Who made a node or a world, and when."""

    model_config = cfg("v1")

    by: Literal["user", "builder", "repair", "template", "generator", "injector", "import"] = Field(
        description="'injector' marks a deliberately planted bug in training data."
    )
    step: Optional[int] = Field(None, ge=0, description="Loop iteration that produced it.")
    model: Optional[str] = Field(None, description="Model or tool name and version.")
    note: Optional[str] = None


class Behavior(IRModel):
    """A preset behaviour with parameters. Never free-form code."""

    model_config = cfg("later")

    preset: Literal[
        "spin",
        "bob",
        "sway",
        "flicker",
        "open_on_approach",
        "toggle_on_click",
        "follow_path",
        "look_at_viewer",
    ]
    params: dict[str, Union[float, str, bool]] = Field(
        default_factory=dict,
        description="Preset parameters, e.g. {'speed_deg_s': 30} for spin.",
    )
    path: Optional[Id] = Field(None, description="For follow_path: the path node.", json_schema_extra=ref("path"))
