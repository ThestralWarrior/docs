"""Relations: declared intent that validators check and rewards can score.

A relation is 'hard' when breaking it is a bug, and 'soft' when it is a
preference that only lowers the score. Relations name nodes by ID.
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import Field

from .common import Id, IRModel, Vec2, WallRef, cfg, ref

NodeId = Annotated[Id, Field(json_schema_extra=ref("node"))]
NodeIds = Annotated[list[Id], Field(min_length=1, json_schema_extra=ref("node"))]
Direction = Literal["left_of", "right_of", "in_front_of", "behind", "above", "below"]


class RelationBase(IRModel):
    """Fields every relation has."""

    model_config = cfg("v1")

    id: Optional[Id] = Field(None, description="Lets issue reports point at this relation.")
    hard: bool = Field(True, description="True: breaking it is a bug. False: a preference.")
    weight: float = Field(1.0, ge=0, description="Importance when scoring soft relations.")
    source: Literal["prompt", "image_brief", "archetype", "default", "user", "builder"] = Field(
        "builder", description="Where the relation came from."
    )
    note: Optional[str] = None


# Support and contact ------------------------------------------------------------


class RelOn(RelationBase):
    """a rests on top of b (on a named surface, if given)."""

    model_config = cfg("v1")
    rel: Literal["on"] = "on"
    a: NodeId
    b: NodeId
    surface: Optional[str] = None


class RelOnFloor(RelationBase):
    """a stands on the floor or ground."""

    model_config = cfg("v1")
    rel: Literal["on_floor"] = "on_floor"
    a: NodeId


class RelMountedOnWall(RelationBase):
    """a hangs on a wall, with its base within a height range."""

    model_config = cfg("v1")
    rel: Literal["mounted_on_wall"] = "mounted_on_wall"
    a: NodeId
    wall: Optional[WallRef] = Field(None, description="Any wall when empty.", json_schema_extra=ref("wall"))
    height: Optional[Vec2] = Field(None, description="[min, max] height of the base, metres.")


class RelHangsFromCeiling(RelationBase):
    """a hangs from the ceiling, its bottom a given distance below it."""

    model_config = cfg("v2")
    rel: Literal["hangs_from_ceiling"] = "hangs_from_ceiling"
    a: NodeId
    drop: Optional[Vec2] = Field(None, description="[min, max] distance below the ceiling, metres.")


class RelInside(RelationBase):
    """a is inside container b, such as books in a cabinet."""

    model_config = cfg("v2")
    rel: Literal["inside"] = "inside"
    a: NodeId
    b: NodeId


class RelAttachedTo(RelationBase):
    """a is fixed to b and must keep the same offset from it."""

    model_config = cfg("v2")
    rel: Literal["attached_to"] = "attached_to"
    a: NodeId
    b: NodeId


# Placement and distance ---------------------------------------------------------


class RelNear(RelationBase):
    """a is within max metres of b, edge to edge."""

    model_config = cfg("v1")
    rel: Literal["near"] = "near"
    a: NodeId
    b: NodeId
    max: float = Field(1.0, gt=0)


class RelFarFrom(RelationBase):
    """a is at least min metres from b, edge to edge."""

    model_config = cfg("v1")
    rel: Literal["far_from"] = "far_from"
    a: NodeId
    b: NodeId
    min: float = Field(2.0, gt=0)


class RelDistance(RelationBase):
    """Edge-to-edge distance between a and b is within a range."""

    model_config = cfg("v2")
    rel: Literal["distance"] = "distance"
    a: NodeId
    b: NodeId
    range: Vec2 = Field(description="[min, max] metres.")
    measure: Literal["edge", "center"] = "edge"


class RelAdjacent(RelationBase):
    """a sits next to b with at most a small gap, optionally on a given side of b."""

    model_config = cfg("v1")
    rel: Literal["adjacent"] = "adjacent"
    a: NodeId
    b: NodeId
    max_gap: float = Field(0.15, ge=0)
    side: Optional[Literal["left", "right", "front", "back"]] = Field(None, description="Side of b, in b's frame.")


class RelAgainstWall(RelationBase):
    """One side of a is within max_gap of a wall: beds, wardrobes, sofas."""

    model_config = cfg("v1")
    rel: Literal["against_wall"] = "against_wall"
    a: NodeId
    wall: Optional[WallRef] = Field(None, description="Any wall when empty.", json_schema_extra=ref("wall"))
    side: Literal["back", "left", "right", "front"] = Field("back", description="Side of a that touches the wall.")
    max_gap: float = Field(0.1, ge=0)


class RelInCorner(RelationBase):
    """a sits in a corner of its room."""

    model_config = cfg("v2")
    rel: Literal["in_corner"] = "in_corner"
    a: NodeId
    max_gap: float = Field(0.2, ge=0)


class RelCentered(RelationBase):
    """a is centred in a room, zone or on another node, along chosen axes."""

    model_config = cfg("v2")
    rel: Literal["centered"] = "centered"
    a: NodeId
    within: NodeId
    axes: Literal["x", "z", "xz"] = "xz"
    tolerance: float = Field(0.1, ge=0)


class RelInsideRegion(RelationBase):
    """a's footprint lies inside a room or zone."""

    model_config = cfg("v1")
    rel: Literal["inside_region"] = "inside_region"
    a: NodeId
    region: NodeId


class RelBetween(RelationBase):
    """a lies between b and c."""

    model_config = cfg("v2")
    rel: Literal["between"] = "between"
    a: NodeId
    b: NodeId
    c: NodeId


class RelRelative(RelationBase):
    """a is left of, right of, in front of, behind, above or below b."""

    model_config = cfg("v1")
    rel: Literal["relative"] = "relative"
    a: NodeId
    b: NodeId
    direction: Direction
    frame: Literal["b", "world", "viewer"] = Field("b", description="Whose left and front count.")
    range: Optional[Vec2] = Field(None, description="Optional [min, max] distance, metres.")


# Orientation --------------------------------------------------------------------


class RelFaces(RelationBase):
    """a's front points at b: chairs at desks, sofas at TVs."""

    model_config = cfg("v1")
    rel: Literal["faces"] = "faces"
    a: NodeId
    b: NodeId
    tolerance_deg: float = Field(20.0, gt=0, le=180)


class RelFacesAway(RelationBase):
    """a's front points away from b, e.g. a bed's foot away from the wall."""

    model_config = cfg("v2")
    rel: Literal["faces_away"] = "faces_away"
    a: NodeId
    b: NodeId
    tolerance_deg: float = Field(30.0, gt=0, le=180)


class RelFacesDirection(RelationBase):
    """a's front points along a world yaw, or into the room from a wall."""

    model_config = cfg("v2")
    rel: Literal["faces_direction"] = "faces_direction"
    a: NodeId
    yaw: Optional[float] = None
    away_from_wall: Optional[WallRef] = Field(None, json_schema_extra=ref("wall"))
    tolerance_deg: float = Field(20.0, gt=0, le=180)


class RelParallel(RelationBase):
    """a and b are rotated the same way, or opposite ways."""

    model_config = cfg("v2")
    rel: Literal["parallel"] = "parallel"
    a: NodeId
    b: NodeId
    allow_opposite: bool = True
    tolerance_deg: float = Field(5.0, gt=0)


class RelPerpendicular(RelationBase):
    """a is at a right angle to b."""

    model_config = cfg("v2")
    rel: Literal["perpendicular"] = "perpendicular"
    a: NodeId
    b: NodeId
    tolerance_deg: float = Field(5.0, gt=0)


class RelAligned(RelationBase):
    """Items line up along an axis by centre or by an edge."""

    model_config = cfg("v2")
    rel: Literal["aligned"] = "aligned"
    items: NodeIds
    axis: Literal["x", "z"]
    edge: Literal["center", "front", "back", "left", "right"] = "center"
    tolerance: float = Field(0.05, ge=0)


class RelUpright(RelationBase):
    """a is not tilted beyond a tolerance."""

    model_config = cfg("v2")
    rel: Literal["upright"] = "upright"
    a: NodeId
    tolerance_deg: float = Field(3.0, gt=0)


# Arrangement --------------------------------------------------------------------


class RelAround(RelationBase):
    """Items are spread around a centre node, facing it: chairs around a table."""

    model_config = cfg("v2")
    rel: Literal["around"] = "around"
    items: NodeIds
    center: NodeId
    radius: Optional[Vec2] = Field(None, description="[min, max] distance from the centre's edge.")
    evenly: bool = True


class RelRow(RelationBase):
    """Items form a straight row with even spacing."""

    model_config = cfg("v2")
    rel: Literal["row"] = "row"
    items: NodeIds
    spacing: Optional[float] = Field(None, gt=0, description="Centre-to-centre; any even spacing when empty.")
    tolerance: float = Field(0.05, ge=0)


class RelGrid(RelationBase):
    """Items form a regular grid."""

    model_config = cfg("later")
    rel: Literal["grid"] = "grid"
    items: NodeIds
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    spacing: Optional[Vec2] = None


class RelSymmetric(RelationBase):
    """a and b mirror each other across c, or across an axis through the room centre."""

    model_config = cfg("v2")
    rel: Literal["symmetric"] = "symmetric"
    a: NodeId
    b: NodeId
    about: Optional[Id] = Field(None, description="Node to mirror across.", json_schema_extra=ref("node"))
    axis: Literal["x", "z"] = "x"
    tolerance: float = Field(0.1, ge=0)


class RelRigidGroup(RelationBase):
    """Items keep their relative positions; a repair must move them together."""

    model_config = cfg("v2")
    rel: Literal["rigid_group"] = "rigid_group"
    items: NodeIds


# Access and visibility ----------------------------------------------------------


class RelClear(RelationBase):
    """A zone, or the space around a node, stays free of objects."""

    model_config = cfg("v1")
    rel: Literal["clear"] = "clear"
    zone: Optional[Id] = Field(None, json_schema_extra=ref("zone"))
    around: Optional[Id] = Field(None, description="Node whose surroundings stay clear.", json_schema_extra=ref("node"))
    margin: float = Field(0.6, ge=0, description="Width of the clear band around the node.")
    sides: list[Literal["front", "back", "left", "right"]] = Field(default_factory=lambda: ["front"])


class RelNotBlocking(RelationBase):
    """a does not block an opening's clearance zone."""

    model_config = cfg("v1")
    rel: Literal["not_blocking"] = "not_blocking"
    a: NodeId
    opening: Id = Field(json_schema_extra=ref("opening"))


class RelWalkway(RelationBase):
    """There is a walkable route of at least min_width from one node to another."""

    model_config = cfg("v2")
    rel: Literal["walkway"] = "walkway"
    from_: Id = Field(alias="from", json_schema_extra=ref("node_or_opening"))
    to: Id = Field(json_schema_extra=ref("node_or_opening"))
    min_width: float = Field(0.6, gt=0)


class RelReachable(RelationBase):
    """A person can walk up to a's front from an entrance."""

    model_config = cfg("v2")
    rel: Literal["reachable"] = "reachable"
    a: NodeId
    entrance: Optional[Id] = Field(
        None, description="Opening or marker; any door when empty.", json_schema_extra=ref("node_or_opening")
    )
    clearance: float = Field(0.6, gt=0)


class RelHeadroom(RelationBase):
    """At least min metres of free height above a's walkable top."""

    model_config = cfg("v2")
    rel: Literal["headroom"] = "headroom"
    a: NodeId
    min: float = Field(2.0, gt=0)


class RelVisibleFrom(RelationBase):
    """a can be seen from a camera or marker."""

    model_config = cfg("later")
    rel: Literal["visible_from"] = "visible_from"
    a: NodeId
    viewpoint: NodeId
    min_fraction: float = Field(0.5, gt=0, le=1, description="Share of a that must be unoccluded.")


class RelLit(RelationBase):
    """a receives at least min_lux of light."""

    model_config = cfg("later")
    rel: Literal["lit"] = "lit"
    a: NodeId
    min_lux: float = Field(150.0, gt=0)


# Composition --------------------------------------------------------------------


class RelCount(RelationBase):
    """Between min and max objects of a category, in a region or the whole world."""

    model_config = cfg("v1")
    rel: Literal["count"] = "count"
    category: str
    min: int = Field(0, ge=0)
    max: Optional[int] = Field(None, ge=0)
    region: Optional[Id] = Field(None, json_schema_extra=ref("node"))


class RelRequires(RelationBase):
    """At least one object of the category exists (in the region, if given)."""

    model_config = cfg("v1")
    rel: Literal["requires"] = "requires"
    category: str
    region: Optional[Id] = Field(None, json_schema_extra=ref("node"))


class RelForbids(RelationBase):
    """No object of the category exists (in the region, if given)."""

    model_config = cfg("v1")
    rel: Literal["forbids"] = "forbids"
    category: str
    region: Optional[Id] = Field(None, json_schema_extra=ref("node"))


class RelStyle(RelationBase):
    """Items (or everything, when empty) carry the given style tags."""

    model_config = cfg("later")
    rel: Literal["style"] = "style"
    tags: list[str] = Field(min_length=1)
    items: list[Id] = Field(default_factory=list, json_schema_extra=ref("node"))


class RelPalette(RelationBase):
    """Dominant colours stay close to a palette."""

    model_config = cfg("later")
    rel: Literal["palette"] = "palette"
    colors: list[str] = Field(min_length=1)
    tolerance: float = Field(0.15, gt=0, le=1, description="Colour distance allowed, 0 to 1.")


# Physical -----------------------------------------------------------------------


class RelNoOverlap(RelationBase):
    """a does not intersect b, or anything at all when b is empty. On by default for every pair."""

    model_config = cfg("v1")
    rel: Literal["no_overlap"] = "no_overlap"
    a: NodeId
    b: Optional[Id] = Field(None, json_schema_extra=ref("node"))


class RelAllowOverlap(RelationBase):
    """Lets a and b overlap, up to a share of the smaller footprint: a chair tucked under a desk."""

    model_config = cfg("v1")
    rel: Literal["allow_overlap"] = "allow_overlap"
    a: NodeId
    b: NodeId
    max_fraction: float = Field(0.5, gt=0, le=1)


class RelWithinBounds(RelationBase):
    """a stays inside its room's walls, or inside the terrain's edges."""

    model_config = cfg("v1")
    rel: Literal["within_bounds"] = "within_bounds"
    a: NodeId


class RelPlausibleScale(RelationBase):
    """a's size is within a ratio of the typical size for its category."""

    model_config = cfg("v1")
    rel: Literal["plausible_scale"] = "plausible_scale"
    a: NodeId
    ratio: Vec2 = Field((0.5, 2.0), description="[min, max] multiple of the typical size.")


class RelMaxSlope(RelationBase):
    """The ground under a zone or path is no steeper than max_deg."""

    model_config = cfg("v2")
    rel: Literal["max_slope"] = "max_slope"
    region: NodeId
    max_deg: float = Field(15.0, gt=0, le=90)


class RelStable(RelationBase):
    """a's centre of mass sits over what supports it, so it would not tip."""

    model_config = cfg("later")
    rel: Literal["stable"] = "stable"
    a: NodeId


Relation = Annotated[
    Union[
        RelOn,
        RelOnFloor,
        RelMountedOnWall,
        RelHangsFromCeiling,
        RelInside,
        RelAttachedTo,
        RelNear,
        RelFarFrom,
        RelDistance,
        RelAdjacent,
        RelAgainstWall,
        RelInCorner,
        RelCentered,
        RelInsideRegion,
        RelBetween,
        RelRelative,
        RelFaces,
        RelFacesAway,
        RelFacesDirection,
        RelParallel,
        RelPerpendicular,
        RelAligned,
        RelUpright,
        RelAround,
        RelRow,
        RelGrid,
        RelSymmetric,
        RelRigidGroup,
        RelClear,
        RelNotBlocking,
        RelWalkway,
        RelReachable,
        RelHeadroom,
        RelVisibleFrom,
        RelLit,
        RelCount,
        RelRequires,
        RelForbids,
        RelStyle,
        RelPalette,
        RelNoOverlap,
        RelAllowOverlap,
        RelWithinBounds,
        RelPlausibleScale,
        RelMaxSlope,
        RelStable,
    ],
    Field(discriminator="rel"),
]

RELATION_GROUPS: dict[str, list[type[RelationBase]]] = {
    "Support and contact": [RelOn, RelOnFloor, RelMountedOnWall, RelHangsFromCeiling, RelInside, RelAttachedTo],
    "Placement and distance": [
        RelNear,
        RelFarFrom,
        RelDistance,
        RelAdjacent,
        RelAgainstWall,
        RelInCorner,
        RelCentered,
        RelInsideRegion,
        RelBetween,
        RelRelative,
    ],
    "Orientation": [RelFaces, RelFacesAway, RelFacesDirection, RelParallel, RelPerpendicular, RelAligned, RelUpright],
    "Arrangement": [RelAround, RelRow, RelGrid, RelSymmetric, RelRigidGroup],
    "Access and visibility": [RelClear, RelNotBlocking, RelWalkway, RelReachable, RelHeadroom, RelVisibleFrom, RelLit],
    "Composition": [RelCount, RelRequires, RelForbids, RelStyle, RelPalette],
    "Physical": [RelNoOverlap, RelAllowOverlap, RelWithinBounds, RelPlausibleScale, RelMaxSlope, RelStable],
}
