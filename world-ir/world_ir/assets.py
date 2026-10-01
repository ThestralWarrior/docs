"""The asset and material registries that nodes refer to."""

from typing import Any, Literal, Optional

from pydantic import Field

from .common import Color, Id, IRModel, Polygon2, Vec2, Vec3, cfg, ref


class License(IRModel):
    """Licence of an asset or texture. Needed for a public demo."""

    model_config = cfg("v1")

    spdx: str = Field(description="SPDX identifier, e.g. 'CC0-1.0', 'CC-BY-4.0'.")
    attribution: Optional[str] = Field(None, description="Credit line, when the licence needs one.")
    url: Optional[str] = None
    commercial_ok: bool = Field(True, description="False for NC licences.")


class Placement(IRModel):
    """Where an asset may go. Matches the objathor annotation flags."""

    model_config = cfg("v1")

    floor: bool = True
    wall: bool = False
    ceiling: bool = False
    on_object: bool = Field(False, description="Can rest on top of another object.")
    receptacle: bool = Field(False, description="Other objects can rest on or in it.")


class AssetSurface(IRModel):
    """A flat surface on an asset that other objects can rest on."""

    model_config = cfg("v1")

    name: str = Field(description="'top', 'seat', 'shelf_1', ... unique within the asset.")
    height: float = Field(ge=0, description="Height above the asset's base, metres, at scale 1.")
    area: Optional[Polygon2] = Field(
        None, description="Usable footprint in asset space. Defaults to the full footprint."
    )
    max_load_kg: Optional[float] = Field(None, gt=0)


class AssetDef(IRModel):
    """One reusable 3D model. Nodes refer to it by ID."""

    model_config = cfg("v1")

    source: Literal["kenney", "objaverse", "polyhaven", "quaternius", "generated", "custom", "primitive"] = Field(
        description="Where it comes from. 'generated' means made by a model such as Hunyuan3D."
    )
    uri: Optional[str] = Field(None, description="GLB path or URL. Empty for 'primitive'.")
    source_uid: Optional[str] = Field(None, description="ID in the source library, e.g. the Objaverse UID.")
    license: License
    category: str = Field(description="Plain category, e.g. 'nightstand'.")
    synset: Optional[str] = Field(None, description="WordNet synset, e.g. 'nightstand.n.01'.")
    description: Optional[str] = Field(None, description="One sentence, used for search.")
    dims: Vec3 = Field(description="Width (x), height (y), depth (z) in metres after normalisation, at scale 1.")
    front: Literal["+z", "-z", "+x", "-x"] = Field(
        "+z", description="Which local axis the front faces. The loader rotates it to +z."
    )
    origin: Literal["bottom_center"] = Field(
        "bottom_center", description="Fixed convention: the loader recentres every model."
    )
    placement: Placement = Field(default_factory=Placement)
    surfaces: list[AssetSurface] = Field(default_factory=list, description="Surfaces other objects can rest on.")
    materials: list[str] = Field(default_factory=list, description="What it looks made of, e.g. ['oak', 'brass'].")
    mass_kg: Optional[float] = Field(None, gt=0)
    style: list[str] = Field(default_factory=list, description="Style tags, e.g. ['low_poly'].")
    variants: dict[str, str] = Field(default_factory=dict, description="Named alternatives: variant name to GLB URI.")
    material_slots: list[str] = Field(
        default_factory=list, description="Names of mesh materials that nodes may override."
    )
    states: dict[str, Vec2] = Field(
        default_factory=dict,
        description="Animatable states and their range, e.g. {'open': [0, 1]} for a cabinet door.",
        json_schema_extra={"x-tier": "v2"},
    )
    thumbnail: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)


class TextureMaps(IRModel):
    """Image maps for a PBR material. All optional."""

    model_config = cfg("v2")

    base_color: Optional[str] = None
    normal: Optional[str] = None
    roughness: Optional[str] = None
    metallic: Optional[str] = None
    ao: Optional[str] = Field(None, description="Ambient occlusion.")
    emissive: Optional[str] = None
    displacement: Optional[str] = None
    opacity: Optional[str] = None


class Material(IRModel):
    """A physically based material, as in glTF and Three.js MeshStandardMaterial."""

    model_config = cfg("v1")

    preset: Optional[str] = Field(
        None,
        description="Start from a named preset (see MATERIAL_PRESETS) and override fields below.",
    )
    base_color: Color = "#cccccc"
    metallic: float = Field(0.0, ge=0, le=1)
    roughness: float = Field(0.7, ge=0, le=1)
    emissive: Optional[Color] = None
    emissive_intensity: float = Field(0.0, ge=0)
    opacity: float = Field(1.0, ge=0, le=1)
    transmission: float = Field(0.0, ge=0, le=1, description="Glass-like see-through, 0 to 1.")
    ior: float = Field(1.5, ge=1, le=2.5, description="Index of refraction, used with transmission.")
    double_sided: bool = False
    maps: Optional[TextureMaps] = Field(None, json_schema_extra={"x-tier": "v2"})
    uv_scale: Vec2 = Field((1.0, 1.0), description="Texture repeats per metre.")
    uv_rotation_deg: float = 0.0
    tags: list[str] = Field(default_factory=list, description="e.g. ['wood', 'floor'].")
    license: Optional[License] = None


class MaterialSlotOverride(IRModel):
    """Replaces one of an asset's mesh materials on a single node."""

    model_config = cfg("v1")

    slot: str = Field(description="One of the asset's material_slots, or '*' for all.")
    material: Id = Field(json_schema_extra=ref("material"))


MATERIAL_PRESETS: dict[str, dict[str, Any]] = {
    "plaster_white": {"base_color": "#ece9e2", "roughness": 0.9},
    "paint_warm_grey": {"base_color": "#b9b2a7", "roughness": 0.85},
    "wood_oak": {"base_color": "#b58a5a", "roughness": 0.6},
    "wood_walnut": {"base_color": "#5e4130", "roughness": 0.55},
    "wood_pine": {"base_color": "#d4b483", "roughness": 0.65},
    "floor_parquet": {"base_color": "#a87a4f", "roughness": 0.5},
    "tile_ceramic_white": {"base_color": "#f2f2ee", "roughness": 0.25},
    "concrete": {"base_color": "#9a9a96", "roughness": 0.95},
    "brick_red": {"base_color": "#8e4a3a", "roughness": 0.9},
    "stone_grey": {"base_color": "#7d7b76", "roughness": 0.9},
    "metal_steel": {"base_color": "#b4b8bd", "metallic": 1.0, "roughness": 0.35},
    "metal_brass": {"base_color": "#c9a24a", "metallic": 1.0, "roughness": 0.3},
    "metal_black": {"base_color": "#2a2a2c", "metallic": 1.0, "roughness": 0.5},
    "glass_clear": {"base_color": "#ffffff", "roughness": 0.05, "transmission": 1.0},
    "fabric_linen": {"base_color": "#d9d2c3", "roughness": 0.95},
    "fabric_velvet_green": {"base_color": "#2f5a46", "roughness": 0.9},
    "leather_brown": {"base_color": "#6b4429", "roughness": 0.6},
    "plastic_white": {"base_color": "#f0f0f0", "roughness": 0.4},
    "grass": {"base_color": "#5d8a3a", "roughness": 1.0},
    "dirt": {"base_color": "#7a5c3e", "roughness": 1.0},
    "sand": {"base_color": "#d8c391", "roughness": 1.0},
    "rock": {"base_color": "#6f6a63", "roughness": 0.95},
    "snow": {"base_color": "#f4f7fa", "roughness": 0.8},
    "water": {"base_color": "#2e6f8e", "roughness": 0.05, "transmission": 0.6},
    "asphalt": {"base_color": "#3b3b3d", "roughness": 0.9},
    "foliage": {"base_color": "#3f6d2e", "roughness": 0.9},
    "bark": {"base_color": "#5a4632", "roughness": 1.0},
    "regolith": {"base_color": "#8a7d70", "roughness": 1.0},
    "regolith_dark": {"base_color": "#4f4650", "roughness": 1.0},
    "ice": {"base_color": "#cfe6f2", "roughness": 0.15, "transmission": 0.3},
    "lava": {"base_color": "#1a0602", "roughness": 0.9, "emissive": "#ff4510", "emissive_intensity": 1.6},
    "acid": {"base_color": "#2f4a0c", "roughness": 0.2, "emissive": "#8cff2a", "emissive_intensity": 1.2},
    "hull_white": {"base_color": "#d9dde2", "metallic": 0.2, "roughness": 0.45},
    "hull_grey": {"base_color": "#6d747c", "metallic": 0.3, "roughness": 0.5},
    "glass_tinted": {"base_color": "#9fd8e6", "roughness": 0.25, "transmission": 0.85},
    "neon_cyan": {"base_color": "#0a1a1f", "emissive": "#38f2ff", "emissive_intensity": 2.5, "roughness": 0.4},
    "neon_magenta": {"base_color": "#1f0a1a", "emissive": "#ff3ad2", "emissive_intensity": 2.5, "roughness": 0.4},
    "neon_amber": {"base_color": "#1f150a", "emissive": "#ffb238", "emissive_intensity": 2.5, "roughness": 0.4},
}
