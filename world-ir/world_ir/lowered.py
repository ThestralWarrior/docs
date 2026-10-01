"""The lowered scene: what lowering produces and what both the validators and the Three.js loader read.

Everything complex in the world IR (rooms, generators, presets, nested
transforms) is flattened into a list of simple items with world-space
matrices. Every item keeps the ID of the IR node it came from, so an issue
found on an item points back to something the repair model can edit.
"""

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field

from .common import Color, IRModel, Vec2, Vec3, cfg
from .environment import Environment

LOWERED_FORMAT = "lowered-1"
LOWERING_VERSION = "0.3.0"
THREE_VERSION = "0.186.1"

Matrix = Annotated[
    tuple[
        float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float
    ],
    Field(description="4×4 world matrix, column-major (the order of Three.js Matrix4.elements)."),
]


class LMaterial(IRModel):
    """A material with presets already applied."""

    model_config = cfg("v1")

    base_color: Color
    metallic: float
    roughness: float
    emissive: Optional[Color] = None
    emissive_intensity: float = 0.0
    opacity: float = 1.0
    transmission: float = 0.0
    ior: float = 1.5
    double_sided: bool = False


class LAsset(IRModel):
    """What the loader needs to place a GLB: where it is, its size in metres, and which way it faces."""

    model_config = cfg("v1")

    uri: Optional[str]
    dims: Vec3
    front: Literal["+z", "-z", "+x", "-x"] = "+z"


class ItemBase(IRModel):
    """Fields every lowered item has."""

    model_config = cfg("v1")

    id: str = Field(description="Unique in the scene. Single-item nodes use the node ID; parts add '/part'.")
    node: str = Field(description="ID of the IR node this came from.")
    role: str = Field(description="What it is, e.g. 'asset', 'wall', 'floor', 'step', 'door', 'window'.")
    matrix: Matrix
    visible: bool = True
    aabb: Optional[tuple[Vec3, Vec3]] = Field(None, description="World-space [min, max], for framing and quick checks.")


class LAssetItem(ItemBase):
    """A GLB, normalised by the loader to bottom-centre origin, +Z front and the asset's dims."""

    type: Literal["asset"] = "asset"
    asset: str
    materials: dict[str, str] = Field(default_factory=dict, description="Mesh material slot to lowered material ID.")


class LShape(ItemBase):
    """A simple shape. Its origin is the centre of its bottom face, like assets."""

    type: Literal["shape"] = "shape"
    shape: Literal["box", "sphere", "cylinder", "cone", "capsule", "plane", "torus", "wedge"]
    size: Vec3
    material: str
    facing: Optional[Vec2] = Field(
        None,
        description="Walls, doors and windows: outward normal in world [x, z], so the loader can cut away near walls.",
    )
    segments: Optional[int] = Field(None, ge=3, description="Round shapes: facets around. Low values look low-poly.")
    flat: bool = Field(False, description="Flat shading, for a faceted low-poly look.")


class LSlab(ItemBase):
    """A flat polygon with thickness: floors and ceilings. Its top face is at local y = 0."""

    type: Literal["slab"] = "slab"
    polygon: list[Vec2] = Field(description="Outline in local [x, z].")
    thickness: float
    material: str


class LHeightfield(ItemBase):
    """Terrain heights on a regular grid centred on the item's origin."""

    type: Literal["heightfield"] = "heightfield"
    size: Vec2 = Field(description="[width along x, depth along z] in metres.")
    rows: int
    cols: int
    heights: list[float] = Field(description="rows × cols heights, row-major, starting at -x, -z.")
    layer_materials: list[str] = Field(default_factory=list, description="Material of each painted layer.")
    layers: list[int] = Field(default_factory=list, description="Layer index at each vertex, same order as heights.")


class LMesh(ItemBase):
    """Explicit triangles: roofs, rocks, path ribbons."""

    type: Literal["mesh"] = "mesh"
    positions: list[float] = Field(description="Flat [x, y, z, ...] in local space.")
    indices: list[int] = Field(description="Three per triangle, counter-clockwise seen from outside.")
    material: str
    flat: bool = True
    double_sided: bool = False


class LInstances(ItemBase):
    """Many copies of one asset or shape: forests, grass, flowers, pickets."""

    type: Literal["instances"] = "instances"
    asset: Optional[str] = Field(None, description="Asset to copy; or give shape, size and material.")
    materials: dict[str, str] = Field(default_factory=dict, description="Asset copies: slot to lowered material ID.")
    shape: Optional[Literal["box", "sphere", "cylinder", "cone"]] = None
    size: Optional[Vec3] = None
    material: Optional[str] = None
    segments: Optional[int] = None
    flat: bool = False
    transforms: list[tuple[float, float, float, float, float]] = Field(
        description="One [x, y, z, yaw_deg, scale] per copy, in the item's local space."
    )


class LLight(ItemBase):
    """A light, with colour already worked out from kelvin."""

    type: Literal["light"] = "light"
    light: Literal["point", "spot", "area", "directional"]
    color: Color
    intensity: float = Field(
        description="Candela (point, spot), nits (area), lux (directional): Three.js physical units."
    )
    range: float
    angle_deg: float
    penumbra: float
    size: Vec2
    cast_shadows: bool
    target: Optional[Vec3] = Field(None, description="World point it aims at.")


class LCamera(ItemBase):
    """A viewpoint."""

    type: Literal["camera"] = "camera"
    purpose: str
    projection: Literal["perspective", "orthographic"]
    fov_deg: float
    near: float
    far: float
    ortho_height: float
    look_at: Optional[Vec3] = Field(None, description="World point it looks at.")


class LZone(ItemBase):
    """An area that must stay clear, or that has a purpose. Drawn only in debug view."""

    type: Literal["zone"] = "zone"
    purpose: str
    polygon: list[Vec2] = Field(description="World [x, z] outline.")
    y_range: Vec2 = Field(description="World bottom and top.")
    label: Optional[str] = None


Item = Annotated[
    Union[LAssetItem, LShape, LSlab, LHeightfield, LMesh, LInstances, LLight, LCamera, LZone],
    Field(discriminator="type"),
]


class LBehavior(IRModel):
    """An animation the loader plays on a set of items. Item matrices stay the rest pose."""

    model_config = cfg("v2")

    node: str = Field(description="The IR node that has the behaviour.")
    preset: Literal["spin", "bob", "sway", "flicker", "follow_path"]
    items: list[str] = Field(description="Items it moves: everything lowered from the node and its children.")
    origin: Matrix = Field(description="The node's world matrix: the pivot and axes for spin, bob and sway.")
    params: dict[str, Union[float, str, bool]] = Field(default_factory=dict)
    path: Optional[list[Vec3]] = Field(None, description="follow_path: world points the node's origin travels through.")
    closed: bool = False


class Unsupported(IRModel):
    """A node this lowering version skipped. Its children are still lowered."""

    model_config = cfg("v1")

    node: str
    kind: str
    reason: str


class LoweredScene(IRModel):
    """Everything the loader and the validators need, in world space."""

    model_config = cfg("v1")

    format: Literal["lowered-1"] = LOWERED_FORMAT
    world_id: str
    versions: dict[str, str] = Field(description="ir, lowering and three versions, for reproducible renders.")
    environment: Environment
    materials: dict[str, LMaterial]
    assets: dict[str, LAsset]
    items: list[Item]
    bounds: Optional[tuple[Vec3, Vec3]] = Field(None, description="World [min, max] of all geometry.")
    behaviors: list[LBehavior] = Field(default_factory=list)
    unsupported: list[Unsupported] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)
