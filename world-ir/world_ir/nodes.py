"""Node kinds: everything that can appear in the world's scene tree.

Every node shares the fields of ``NodeBase``. Children are placed relative to
their parent, so moving a table moves the lamp on it.
"""

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field, model_validator

from .architecture import Opening
from .assets import MaterialSlotOverride
from .common import (
    Behavior,
    Color,
    Id,
    IRModel,
    Physics,
    Polygon2,
    Provenance,
    Semantic,
    Support,
    Transform,
    Vec2,
    Vec3,
    cfg,
    ref,
    tiered,
)
from .generators import Generator
from .terrain import TerrainHeight, TerrainLayer, TerrainModifier


class NodeBase(IRModel):
    """Fields every node has."""

    model_config = cfg("v1")

    id: Id
    name: Optional[str] = Field(None, description="Human-readable label.")
    tags: list[str] = Field(default_factory=list)
    semantic: Optional[Semantic] = None
    xform: Transform = Field(default_factory=Transform)
    support: Optional[Support] = Field(
        None, description="What it rests on. Empty means: work it out from parent and placement."
    )
    visible: bool = True
    locked: bool = Field(False, description="The repair model may not change this node.")
    physics: Optional[Physics] = Field(None, json_schema_extra=tiered("v2"))
    behaviors: list[Behavior] = Field(
        default_factory=list,
        description="Animation presets the viewer plays. Validators check the rest pose, where the node is placed.",
        json_schema_extra=tiered("v2"),
    )
    source: Optional[Provenance] = None
    children: list["Node"] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict, description="Free-form data tools may attach.")


class GroupNode(NodeBase):
    """An empty container that moves its children together, e.g. 'reading_corner'."""

    model_config = cfg("v1")

    kind: Literal["group"] = "group"


class AssetNode(NodeBase):
    """One placed copy of an asset from the registry."""

    model_config = cfg("v1")

    kind: Literal["asset"] = "asset"
    asset: Id = Field(json_schema_extra=ref("asset"))
    variant: Optional[str] = Field(None, description="One of the asset's variants.")
    materials: list[MaterialSlotOverride] = Field(default_factory=list)
    state: dict[str, float] = Field(
        default_factory=dict,
        description="Values for the asset's states, e.g. {'open': 0.4}.",
        json_schema_extra=tiered("v2"),
    )


class PrimitiveNode(NodeBase):
    """A simple shape: blockouts, placeholders, colliders, abstract props."""

    model_config = cfg("v1")

    kind: Literal["primitive"] = "primitive"
    shape: Literal["box", "sphere", "cylinder", "cone", "capsule", "plane", "torus", "wedge"]
    size: Vec3 = Field(
        (1.0, 1.0, 1.0),
        description="Bounding size [x, y, z]. Spheres and cylinders fit inside it.",
    )
    material: Optional[Id] = Field(None, json_schema_extra=ref("material"))
    color: Optional[Color] = Field(None, description="Quick colour when no material is set.")
    segments: int = Field(24, ge=3, le=128)


class GeneratorNode(NodeBase):
    """Geometry made by a parametric generator such as stairs, a fence or a tree."""

    model_config = cfg("v1")

    kind: Literal["generator"] = "generator"
    generator: Generator


class NodePatch(IRModel):
    """Changes applied to one node inside a prefab copy."""

    model_config = cfg("v2")

    xform: Optional[Transform] = None
    asset: Optional[Id] = Field(None, json_schema_extra=ref("asset"))
    materials: Optional[list[MaterialSlotOverride]] = None
    visible: Optional[bool] = None


class PrefabNode(NodeBase):
    """A copy of a prefab (a saved group of nodes), with optional per-node changes."""

    model_config = cfg("v2")

    kind: Literal["prefab"] = "prefab"
    prefab: Id = Field(json_schema_extra=ref("prefab"))
    overrides: dict[str, NodePatch] = Field(
        default_factory=dict, description="Keyed by the ID of a node inside the prefab."
    )


class WallOverride(IRModel):
    """Changes to a single wall of a room."""

    model_config = cfg("v1")

    edge: int = Field(ge=0)
    material: Optional[Id] = Field(None, json_schema_extra=ref("material"))
    height: Optional[float] = Field(None, gt=0)
    hidden: bool = Field(False, description="Leave this wall out, e.g. for an open-plan side.")


class RoomNode(NodeBase):
    """An interior room: floor, walls, ceiling and openings. Its children are placed in room space."""

    model_config = cfg("v1")

    kind: Literal["room"] = "room"
    room_type: str = Field(description="See ROOM_TYPES, e.g. 'bedroom', 'kitchen'. Other strings are allowed.")
    outline: Polygon2 = Field(
        description="Floor outline as [x, z] points in either winding. Wall n runs from point n to point n+1."
    )
    height: float = Field(2.6, gt=1.8, le=12)
    wall_thickness: float = Field(0.12, gt=0, le=1)
    floor_material: Optional[Id] = Field(None, json_schema_extra=ref("material"))
    wall_material: Optional[Id] = Field(None, json_schema_extra=ref("material"))
    ceiling_material: Optional[Id] = Field(
        None, description="Empty with has_ceiling true uses the wall material.", json_schema_extra=ref("material")
    )
    has_ceiling: bool = True
    openings: list[Opening] = Field(default_factory=list)
    walls: list[WallOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def _openings_fit(self) -> "RoomNode":
        edges = len(self.outline)
        for opening in self.openings:
            if opening.wall >= edges:
                raise ValueError(f"opening {opening.id} is on wall {opening.wall}, but the room has {edges} walls")
            a = self.outline[opening.wall]
            b = self.outline[(opening.wall + 1) % edges]
            length = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
            if opening.offset - opening.width / 2 < -1e-6 or opening.offset + opening.width / 2 > length + 1e-6:
                raise ValueError(f"opening {opening.id} does not fit on wall {opening.wall} ({length:.2f} m long)")
            if opening.sill + opening.height > self.height + 1e-6:
                raise ValueError(f"opening {opening.id} is taller than the room")
        return self


class TerrainNode(NodeBase):
    """Ground made from a heightmap. Its origin is the centre of the area at height 0."""

    model_config = cfg("v2")

    kind: Literal["terrain"] = "terrain"
    size: Vec2 = Field(description="[width along x, depth along z] in metres.")
    resolution: int = Field(129, ge=2, le=4097, description="Height samples per side.")
    height: TerrainHeight
    modifiers: list[TerrainModifier] = Field(default_factory=list, description="Applied in order.")
    layers: list[TerrainLayer] = Field(default_factory=list, description="Painted in order.")
    water_level: Optional[float] = Field(None, description="Fills everything below this height with water.")
    water_material: Optional[Id] = Field(
        None,
        description="What fills it: water by default, or e.g. the 'lava' or 'acid' preset.",
        json_schema_extra=ref("material"),
    )
    holes: list[Polygon2] = Field(default_factory=list, description="Areas with no ground, e.g. cave mouths.")
    collider: bool = True


class WaterNode(NodeBase):
    """A body of water with a flat surface."""

    model_config = cfg("later")

    kind: Literal["water"] = "water"
    area: Polygon2
    level: float = 0.0
    depth: float = Field(1.5, gt=0)
    color: Color = "#2e6f8e"
    clarity: float = Field(0.5, ge=0, le=1)
    waves: float = Field(0.05, ge=0, description="Wave height in metres.")
    flow: Vec2 = Field((0.0, 0.0), description="Current direction and speed, metres per second.")


class PathNode(NodeBase):
    """A line through the world that other things follow: roads, trails, rivers, fence lines."""

    model_config = cfg("v2")

    kind: Literal["path"] = "path"
    points: list[Vec3] = Field(min_length=2, description="Control points. y is ignored when conforming to terrain.")
    closed: bool = False
    smooth: bool = Field(True, description="Curve through the points instead of straight segments.")
    width: float = Field(2.0, gt=0)
    purpose: Literal["road", "trail", "river", "fence_line", "rail", "guide", "walkway"] = "trail"
    conform_to_terrain: bool = True
    material: Optional[Id] = Field(None, json_schema_extra=ref("material"))


class ScatterItem(IRModel):
    """One asset that a scatter may place."""

    model_config = cfg("v2")

    asset: Id = Field(json_schema_extra=ref("asset"))
    weight: float = Field(1.0, gt=0, description="Relative share among the scatter's items.")
    scale: Vec2 = Field((0.8, 1.2), description="Random uniform scale range.")
    yaw: Vec2 = Field((0.0, 360.0), description="Random yaw range in degrees.")
    materials: list[MaterialSlotOverride] = Field(
        default_factory=list, description="Material overrides for every copy, as on asset nodes."
    )


class ScatterNode(NodeBase):
    """Many copies of assets spread over an area: forests, rocks, debris, crowds of chairs."""

    model_config = cfg("v2")

    kind: Literal["scatter"] = "scatter"
    area: Optional[Polygon2] = Field(None, description="Where to place, in local space.")
    zone: Optional[Id] = Field(None, description="Or: inside this zone node.", json_schema_extra=ref("zone"))
    items: list[ScatterItem] = Field(min_length=1)
    density: Optional[float] = Field(None, gt=0, description="Copies per square metre.")
    count: Optional[int] = Field(None, ge=1, description="Exact number of copies, instead of density.")
    min_spacing: float = Field(1.0, ge=0, description="Minimum distance between copies.")
    on: Literal["floor", "terrain", "surface"] = "terrain"
    max_slope_deg: float = Field(30.0, ge=0, le=90)
    height_range: Optional[Vec2] = Field(None, description="Only place between these ground heights.")
    align_to_slope: float = Field(0.0, ge=0, le=1, description="0 keeps upright, 1 follows the ground.")
    avoid: list[Id] = Field(
        default_factory=list,
        description="Zones, paths or nodes to keep clear of.",
        json_schema_extra=ref("node"),
    )
    seed: int = 0

    @model_validator(mode="after")
    def _where_and_how_many(self) -> "ScatterNode":
        if (self.area is None) == (self.zone is None):
            raise ValueError("scatter needs exactly one of area or zone")
        if (self.density is None) == (self.count is None):
            raise ValueError("scatter needs exactly one of density or count")
        return self


class ZoneNode(NodeBase):
    """A named area with a purpose. Validators and scatters use zones."""

    model_config = cfg("v1")

    kind: Literal["zone"] = "zone"
    purpose: Literal[
        "clearance",
        "walkway",
        "no_build",
        "activity",
        "spawn",
        "biome",
        "view",
        "audio",
        "region",
    ] = Field(description="'clearance' and 'walkway' must stay free of objects.")
    area: Polygon2 = Field(description="Outline in local space.")
    y_range: Vec2 = Field((0.0, 2.2), description="Bottom and top of the zone, metres.")
    label: Optional[str] = Field(None, description="e.g. 'reading', 'dining', 'pine forest'.")


class LightNode(NodeBase):
    """A light source. Make it a child of a lamp asset so they move together."""

    model_config = cfg("v1")

    kind: Literal["light"] = "light"
    type: Literal["point", "spot", "area", "directional"]
    color: Optional[Color] = Field(None, description="Overrides kelvin.")
    kelvin: float = Field(2700.0, ge=1000, le=20000, description="2700 warm bulb, 4000 neutral, 6500 daylight.")
    intensity: float = Field(60.0, ge=0, description="Candela for point and spot, nits for area, lux for directional.")
    range: float = Field(0.0, ge=0, description="Cut-off distance in metres; 0 means no cut-off.")
    angle_deg: float = Field(45.0, gt=0, le=90, description="Spot only: half-angle of the cone.")
    penumbra: float = Field(0.3, ge=0, le=1, description="Spot only: softness of the cone edge.")
    size: Vec2 = Field((1.0, 0.5), description="Area only: [width, height] of the panel.")
    target: Optional[Union[Vec3, Id]] = Field(
        None, description="Spot and directional: point or node to aim at.", json_schema_extra=ref("node")
    )
    cast_shadows: bool = False


class CameraNode(NodeBase):
    """A viewpoint: for inspection renders, the demo, or where a player starts."""

    model_config = cfg("v1")

    kind: Literal["camera"] = "camera"
    projection: Literal["perspective", "orthographic"] = "perspective"
    fov_deg: float = Field(60.0, gt=1, lt=180, description="Vertical field of view.")
    near: float = Field(0.05, gt=0)
    far: float = Field(500.0, gt=0)
    ortho_height: float = Field(10.0, gt=0, description="Orthographic only: visible height in metres.")
    look_at: Optional[Union[Vec3, Id]] = Field(None, json_schema_extra=ref("node"))
    purpose: Literal["inspection", "hero", "top_down", "player_start", "thumbnail"] = "inspection"


class MarkerNode(NodeBase):
    """A named point: spawn points, waypoints, things to look at."""

    model_config = cfg("v2")

    kind: Literal["marker"] = "marker"
    purpose: Literal["spawn", "waypoint", "look_target", "anchor", "label", "point_of_interest"]
    radius: float = Field(0.5, ge=0)


class DecalNode(NodeBase):
    """An image projected onto a surface: posters, stains, road markings."""

    model_config = cfg("later")

    kind: Literal["decal"] = "decal"
    image: str
    size: Vec2 = (1.0, 1.0)
    on: Optional[Id] = Field(None, json_schema_extra=ref("node"))


class TextNode(NodeBase):
    """Text in the world: signs, labels."""

    model_config = cfg("later")

    kind: Literal["text"] = "text"
    text: str = Field(max_length=200)
    font_size: float = Field(0.2, gt=0, description="Letter height in metres.")
    color: Color = "#111111"
    billboard: bool = Field(False, description="Always face the viewer.")


class AudioNode(NodeBase):
    """A sound source."""

    model_config = cfg("later")

    kind: Literal["audio"] = "audio"
    uri: str
    loop: bool = True
    volume: float = Field(0.8, ge=0, le=1)
    radius: float = Field(10.0, gt=0, description="Audible distance; ignored when ambient.")
    ambient: bool = False


class ParticlesNode(NodeBase):
    """A particle effect from a preset."""

    model_config = cfg("later")

    kind: Literal["particles"] = "particles"
    preset: Literal["fire", "smoke", "steam", "rain", "snow", "dust", "sparks", "fireflies", "leaves"]
    rate: float = Field(50.0, gt=0, description="Particles per second.")
    area: Vec3 = (1.0, 1.0, 1.0)


Node = Annotated[
    Union[
        GroupNode,
        AssetNode,
        PrimitiveNode,
        GeneratorNode,
        PrefabNode,
        RoomNode,
        TerrainNode,
        WaterNode,
        PathNode,
        ScatterNode,
        ZoneNode,
        LightNode,
        CameraNode,
        MarkerNode,
        DecalNode,
        TextNode,
        AudioNode,
        ParticlesNode,
    ],
    Field(discriminator="kind"),
]

NODE_KINDS: list[type[NodeBase]] = [
    GroupNode,
    AssetNode,
    PrimitiveNode,
    GeneratorNode,
    PrefabNode,
    RoomNode,
    TerrainNode,
    WaterNode,
    PathNode,
    ScatterNode,
    ZoneNode,
    LightNode,
    CameraNode,
    MarkerNode,
    DecalNode,
    TextNode,
    AudioNode,
    ParticlesNode,
]

for _cls in [NodeBase, *NODE_KINDS]:
    _cls.model_rebuild()
