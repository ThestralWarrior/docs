"""Parametric generators: hand-written functions that turn numbers into geometry.

Agents choose a generator and its parameters. They never write geometry code.
Each generator's output is placed at its node's transform, with the
generator's own origin at the bottom centre of what it builds unless noted.
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import Field

from .architecture import Opening, RoofSpec
from .common import Color, Id, IRModel, Polygon2, Vec2, Vec3, cfg, ref

MaterialRef = Annotated[Optional[Id], Field(json_schema_extra=ref("material"))]
AssetRef = Annotated[Id, Field(json_schema_extra=ref("asset"))]


# Architecture -----------------------------------------------------------------


class GenStairs(IRModel):
    """A staircase. The origin is the centre of the bottom step's front edge; it climbs toward +Z."""

    model_config = cfg("v1")

    gen: Literal["stairs"] = "stairs"
    shape: Literal["straight", "l_shape", "u_shape", "spiral"] = "straight"
    steps: int = Field(12, ge=1, le=60)
    rise: float = Field(0.18, gt=0, le=0.3, description="Height of each step.")
    run: float = Field(0.28, gt=0.1, le=0.6, description="Depth of each step.")
    width: float = Field(1.0, gt=0.4)
    turn: Literal["left", "right"] = Field("left", description="L and U shapes: direction of the turn.")
    landing_depth: float = Field(1.0, gt=0)
    railing: Literal["none", "left", "right", "both"] = "right"
    open_risers: bool = False
    material: MaterialRef = None


class GenRamp(IRModel):
    """A sloped ramp climbing toward +Z."""

    model_config = cfg("v2")

    gen: Literal["ramp"] = "ramp"
    length: float = Field(gt=0)
    width: float = Field(1.2, gt=0)
    rise: float = Field(gt=0)
    railing: Literal["none", "left", "right", "both"] = "both"
    material: MaterialRef = None


class GenRoof(IRModel):
    """A roof over a rectangular or polygonal footprint."""

    model_config = cfg("v2")

    gen: Literal["roof"] = "roof"
    footprint: Union[Vec2, Polygon2] = Field(description="[width, depth], or a polygon of [x, z] points.")
    roof: RoofSpec = Field(default_factory=RoofSpec)


class GenWallRun(IRModel):
    """Free-standing walls along a polyline, with optional openings."""

    model_config = cfg("v1")

    gen: Literal["wall_run"] = "wall_run"
    points: list[Vec2] = Field(min_length=2, description="[x, z] corner points.")
    closed: bool = False
    height: float = Field(2.6, gt=0)
    thickness: float = Field(0.15, gt=0)
    openings: list[Opening] = Field(default_factory=list)
    material: MaterialRef = None


class GenColumn(IRModel):
    """A column or pillar."""

    model_config = cfg("v2")

    gen: Literal["column"] = "column"
    profile: Literal["round", "square", "octagonal"] = "round"
    height: float = Field(2.6, gt=0)
    width: float = Field(0.3, gt=0, description="Diameter or side length.")
    base: bool = True
    capital: Literal["none", "simple", "doric", "ionic"] = "simple"
    material: MaterialRef = None


class GenArch(IRModel):
    """An arch spanning along X."""

    model_config = cfg("v2")

    gen: Literal["arch"] = "arch"
    span: float = Field(gt=0)
    rise: float = Field(gt=0, description="Height of the curve above the springing line.")
    pier_height: float = Field(2.0, ge=0)
    depth: float = Field(0.4, gt=0)
    thickness: float = Field(0.3, gt=0)
    profile: Literal["round", "pointed", "segmental", "flat"] = "round"
    material: MaterialRef = None


class GenFence(IRModel):
    """A fence along a path node or a list of points."""

    model_config = cfg("v2")

    gen: Literal["fence"] = "fence"
    path: Optional[Id] = Field(None, json_schema_extra=ref("path"))
    points: Optional[list[Vec2]] = Field(None, description="Used when no path is given.")
    style: Literal["picket", "rail", "wire", "stone_wall", "hedge", "iron", "panel"] = "picket"
    height: float = Field(1.0, gt=0)
    post_spacing: float = Field(2.0, gt=0)
    gates: list[float] = Field(default_factory=list, description="Distances along the fence where gates go.")
    gate_width: float = Field(1.0, gt=0)
    material: MaterialRef = None


class GenRailing(IRModel):
    """A handrail or balustrade along points."""

    model_config = cfg("v2")

    gen: Literal["railing"] = "railing"
    points: list[Vec3] = Field(min_length=2)
    height: float = Field(0.9, gt=0)
    style: Literal["bar", "glass", "balusters", "cable"] = "bar"
    material: MaterialRef = None


class GenBridge(IRModel):
    """A bridge spanning along +Z."""

    model_config = cfg("later")

    gen: Literal["bridge"] = "bridge"
    span: float = Field(gt=0)
    width: float = Field(2.0, gt=0)
    deck_height: float = Field(1.0, ge=0)
    style: Literal["beam", "arch", "plank", "suspension", "stone"] = "plank"
    railing: bool = True
    material: MaterialRef = None


class WindowGrid(IRModel):
    """Regular windows on a building's facades."""

    model_config = cfg("v2")

    per_floor: int = Field(3, ge=0, description="Windows per long facade per floor.")
    width: float = Field(1.0, gt=0)
    height: float = Field(1.3, gt=0)
    sill: float = Field(0.9, ge=0)
    asset: Optional[Id] = Field(None, json_schema_extra=ref("asset"))


class GenBuilding(IRModel):
    """A building exterior: walls, floors, windows, door and roof.

    A [width, depth] footprint is centred on the origin and its walls are numbered
    like a room outline that starts at (-width/2, -depth/2) and runs along +x.
    """

    model_config = cfg("v2")

    gen: Literal["building"] = "building"
    footprint: Union[Vec2, Polygon2]
    floors: int = Field(1, ge=1, le=60)
    floor_height: float = Field(3.0, gt=0)
    roof: RoofSpec = Field(default_factory=RoofSpec)
    windows: WindowGrid = Field(default_factory=WindowGrid)
    entrance: Optional[Opening] = None
    facade_style: list[str] = Field(default_factory=list, description="e.g. ['timber', 'alpine'].")
    wall_material: MaterialRef = None
    trim_material: MaterialRef = None
    floor_material: MaterialRef = None
    interior: Literal["none", "shell"] = Field("none", description="'shell' adds floors and leaves rooms empty.")


class GenPlatform(IRModel):
    """A raised deck, stage or porch. Its walkable top is the surface 'top'."""

    model_config = cfg("v2")

    gen: Literal["platform"] = "platform"
    footprint: Union[Vec2, Polygon2]
    height: float = Field(0.5, ge=0)
    legs: Literal["solid", "posts", "none"] = "posts"
    railing: bool = False
    material: MaterialRef = None


# Interior ---------------------------------------------------------------------


class GenShelving(IRModel):
    """A shelving unit. Each shelf becomes a named surface: shelf_1, shelf_2, ..."""

    model_config = cfg("v2")

    gen: Literal["shelving"] = "shelving"
    width: float = Field(0.9, gt=0)
    depth: float = Field(0.35, gt=0)
    height: float = Field(1.8, gt=0)
    shelves: int = Field(4, ge=1, le=20)
    back_panel: bool = True
    material: MaterialRef = None


class GenTableSet(IRModel):
    """A table with chairs placed around it, all facing it."""

    model_config = cfg("v1")

    gen: Literal["table_set"] = "table_set"
    table: AssetRef
    chair: AssetRef
    chairs: int = Field(4, ge=0, le=16)
    layout: Literal["around", "two_sides", "one_side", "ends"] = "around"
    chair_gap: float = Field(0.1, ge=0, description="Gap between chair front and table edge.")


class KitchenModule(IRModel):
    """One unit in a kitchen run."""

    model_config = cfg("v2")

    type: Literal["base", "drawers", "sink", "stove", "oven", "dishwasher", "fridge", "tall", "gap", "corner"]
    width: float = Field(0.6, gt=0)


class GenKitchenRun(IRModel):
    """Cabinets and appliances along a wall, with a counter on top."""

    model_config = cfg("v2")

    gen: Literal["kitchen_run"] = "kitchen_run"
    modules: list[KitchenModule] = Field(min_length=1)
    counter_height: float = Field(0.9, gt=0)
    depth: float = Field(0.6, gt=0)
    upper_cabinets: bool = True
    counter_material: MaterialRef = None
    cabinet_material: MaterialRef = None


class GenShelfFill(IRModel):
    """Fills a surface of another node with small items such as books or jars."""

    model_config = cfg("v2")

    gen: Literal["shelf_fill"] = "shelf_fill"
    target: Id = Field(json_schema_extra=ref("node"))
    surfaces: list[str] = Field(default_factory=lambda: ["*"], description="Surface names, or '*' for all.")
    items: list[Id] = Field(min_length=1, json_schema_extra=ref("asset"))
    fill: float = Field(0.7, ge=0, le=1, description="How full, 0 to 1.")
    seed: int = 0


class GenRug(IRModel):
    """A flat rug. Treated as part of the floor by the overlap check."""

    model_config = cfg("v1")

    gen: Literal["rug"] = "rug"
    shape: Literal["rectangle", "round", "oval", "runner"] = "rectangle"
    size: Vec2 = Field((2.0, 1.4), description="[width, depth]; diameter for round.")
    pattern: Literal["plain", "striped", "bordered", "checked", "persian"] = "plain"
    colors: list[Color] = Field(default_factory=lambda: ["#8c6b5a"])
    thickness: float = Field(0.01, gt=0, le=0.05)


class GenCurtains(IRModel):
    """Curtains hung over an opening."""

    model_config = cfg("v2")

    gen: Literal["curtains"] = "curtains"
    room: Id = Field(json_schema_extra=ref("node"))
    opening: Id = Field(json_schema_extra=ref("opening"))
    open: float = Field(0.5, ge=0, le=1)
    length: Literal["sill", "floor"] = "floor"
    material: MaterialRef = None


# Nature -----------------------------------------------------------------------


class GenTree(IRModel):
    """A procedural tree."""

    model_config = cfg("v2")

    gen: Literal["tree"] = "tree"
    species: Literal["conifer", "deciduous", "birch", "palm", "willow", "dead", "fruit"] = "deciduous"
    height: float = Field(6.0, gt=0.5, le=60)
    crown_radius: float = Field(2.0, gt=0)
    trunk_radius: float = Field(0.2, gt=0)
    leaf_color: Optional[Color] = None
    season: Literal["spring", "summer", "autumn", "winter"] = "summer"
    style: Literal["low_poly", "stylised", "realistic"] = "low_poly"
    seed: int = 0


class GenRock(IRModel):
    """A procedural rock or boulder."""

    model_config = cfg("v2")

    gen: Literal["rock"] = "rock"
    size: Vec3 = (1.0, 0.7, 0.9)
    roughness: float = Field(0.5, ge=0, le=1, description="How jagged the shape is.")
    embed: float = Field(0.2, ge=0, le=0.9, description="Share of its height sunk into the ground.")
    seed: int = 0
    material: MaterialRef = None


class GenBush(IRModel):
    """A shrub or hedge segment."""

    model_config = cfg("v2")

    gen: Literal["bush"] = "bush"
    size: Vec3 = (1.0, 0.8, 1.0)
    density: float = Field(0.7, ge=0, le=1)
    flowers: Optional[Color] = None
    seed: int = 0


class GenGrass(IRModel):
    """Instanced grass blades or tufts over an area."""

    model_config = cfg("v2")

    gen: Literal["grass"] = "grass"
    area: Polygon2
    density: float = Field(40.0, gt=0, description="Tufts per square metre.")
    height: float = Field(0.25, gt=0)
    color: Color = "#6b8f3c"
    seed: int = 0


class GenFlowerBed(IRModel):
    """Flowers planted over an area."""

    model_config = cfg("v2")

    gen: Literal["flower_bed"] = "flower_bed"
    area: Polygon2
    palette: list[Color] = Field(default_factory=lambda: ["#d9465f", "#f2c14e", "#f7f7f2"])
    density: float = Field(12.0, gt=0, description="Plants per square metre.")
    border: Optional[Literal["stone", "wood", "brick"]] = None
    seed: int = 0


# Urban ------------------------------------------------------------------------


class GenRoad(IRModel):
    """A road built along a path node."""

    model_config = cfg("v2")

    gen: Literal["road"] = "road"
    path: Id = Field(json_schema_extra=ref("path"))
    lanes: int = Field(2, ge=1, le=8)
    lane_width: float = Field(3.25, gt=0)
    sidewalk_width: float = Field(1.8, ge=0)
    curb_height: float = Field(0.15, ge=0)
    markings: bool = True
    surface: Literal["asphalt", "cobblestone", "gravel", "dirt"] = "asphalt"


class GenAlongPath(IRModel):
    """Places copies of an asset at intervals along a path: street lamps, benches, trees."""

    model_config = cfg("v2")

    gen: Literal["along_path"] = "along_path"
    path: Id = Field(json_schema_extra=ref("path"))
    asset: AssetRef
    spacing: float = Field(8.0, gt=0)
    offset: float = Field(0.0, description="Sideways distance from the path centre; negative is left.")
    both_sides: bool = False
    face: Literal["path", "away", "forward"] = "path"
    start: float = Field(0.0, ge=0, description="Distance along the path before the first copy.")


class GenParkingLot(IRModel):
    """Rows of marked parking bays."""

    model_config = cfg("later")

    gen: Literal["parking_lot"] = "parking_lot"
    area: Polygon2
    bay_width: float = Field(2.5, gt=0)
    bay_depth: float = Field(5.0, gt=0)
    angle_deg: float = Field(90.0, ge=30, le=90)


# Generic ----------------------------------------------------------------------


class SweepSupports(IRModel):
    """Posts under a sweep, from its underside down to the ground."""

    model_config = cfg("v2")

    spacing: float = Field(6.0, gt=0.5, description="Distance between posts along the line.")
    shape: Literal["box", "cylinder"] = "cylinder"
    width: float = Field(0.3, gt=0)
    asset: Optional[Id] = Field(
        None,
        description="Place this asset on the ground instead of a post; it is not stretched.",
        json_schema_extra=ref("asset"),
    )
    material: MaterialRef = None


class GenSweep(IRModel):
    """A cross-section swept along a line: pipes, cables, rails, monorail beams, neon tubes, conduits.

    The line runs through the centre of the profile. Give a path node or points in the
    node's own space. With conform_to_terrain the line keeps `elevation` metres above
    the ground; otherwise it keeps the line's own heights plus `elevation`.
    """

    model_config = cfg("v2")

    gen: Literal["sweep"] = "sweep"
    path: Optional[Id] = Field(None, json_schema_extra=ref("path"))
    points: Optional[list[Vec3]] = Field(None, description="[x, y, z] points, used when no path is given.")
    closed: bool = Field(False, description="Points only: join the end back to the start.")
    smooth: bool = Field(True, description="Points only: curve through the points.")
    profile: Literal["circle", "rect"] = "circle"
    radius: float = Field(0.15, gt=0, description="Circle profile radius.")
    size: Vec2 = Field((0.4, 0.3), description="Rect profile [width, height].")
    segments: int = Field(12, ge=3, le=64, description="Facets around a circle profile.")
    elevation: float = Field(0.0, description="Height of the line above the ground or above its points.")
    offset: float = Field(0.0, description="Sideways shift from the line; positive is to the right of travel.")
    conform_to_terrain: Optional[bool] = Field(
        None, description="Follow the ground. Empty means: as the path does, or no for points."
    )
    supports: Optional[SweepSupports] = None
    material: MaterialRef = None


class GenArray(IRModel):
    """Copies an asset in a 1D, 2D or 3D grid: rows of seats, columns, crates."""

    model_config = cfg("v1")

    gen: Literal["array"] = "array"
    asset: AssetRef
    count: tuple[int, int, int] = Field((3, 1, 1), description="Copies along [x, y, z].")
    spacing: Vec3 = Field((1.0, 1.0, 1.0), description="Centre-to-centre distance along each axis.")
    jitter: Vec3 = Field((0.0, 0.0, 0.0), description="Random offset range per axis.")
    yaw_jitter_deg: float = Field(0.0, ge=0)
    seed: int = 0


class GenRadialArray(IRModel):
    """Copies an asset around a circle: chairs around a round table, stones in a ring."""

    model_config = cfg("v2")

    gen: Literal["radial_array"] = "radial_array"
    asset: AssetRef
    count: int = Field(6, ge=1, le=360)
    radius: float = Field(1.0, gt=0)
    start_deg: float = 0.0
    sweep_deg: float = Field(360.0, gt=0, le=360)
    face: Literal["center", "outward", "tangent", "fixed"] = "center"


Generator = Annotated[
    Union[
        GenStairs,
        GenRamp,
        GenRoof,
        GenWallRun,
        GenColumn,
        GenArch,
        GenFence,
        GenRailing,
        GenBridge,
        GenBuilding,
        GenPlatform,
        GenShelving,
        GenTableSet,
        GenKitchenRun,
        GenShelfFill,
        GenRug,
        GenCurtains,
        GenTree,
        GenRock,
        GenBush,
        GenGrass,
        GenFlowerBed,
        GenRoad,
        GenAlongPath,
        GenParkingLot,
        GenArray,
        GenRadialArray,
        GenSweep,
    ],
    Field(discriminator="gen"),
]

GENERATOR_GROUPS: dict[str, list[type[IRModel]]] = {
    "Architecture": [
        GenStairs,
        GenRamp,
        GenRoof,
        GenWallRun,
        GenColumn,
        GenArch,
        GenFence,
        GenRailing,
        GenBridge,
        GenBuilding,
        GenPlatform,
    ],
    "Interior": [GenShelving, GenTableSet, GenKitchenRun, GenShelfFill, GenRug, GenCurtains],
    "Nature": [GenTree, GenRock, GenBush, GenGrass, GenFlowerBed],
    "Urban": [GenRoad, GenAlongPath, GenParkingLot],
    "Generic": [GenArray, GenRadialArray, GenSweep],
}
