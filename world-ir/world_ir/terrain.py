"""Terrain: a heightmap, the edits applied to it, and the materials painted on it.

Terrain is data, never geometry code. A fixed generator turns it into a mesh,
and validators sample the same heights through ``support_height(x, z)``.
"""

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, model_validator

from .common import Id, IRModel, Polygon2, Vec2, cfg, ref


class HeightFlat(IRModel):
    """A flat plane at one height."""

    model_config = cfg("v2")

    source: Literal["flat"] = "flat"
    y: float = 0.0


class HeightNoise(IRModel):
    """Heights from layered noise (fractal Brownian motion)."""

    model_config = cfg("v2")

    source: Literal["noise"] = "noise"
    algorithm: Literal["perlin", "simplex", "value", "worley", "ridged"] = "simplex"
    seed: int = 0
    octaves: int = Field(4, ge=1, le=12)
    frequency: float = Field(0.02, gt=0, description="Features per metre at the first octave.")
    amplitude: float = Field(4.0, ge=0, description="Height range in metres at the first octave.")
    lacunarity: float = Field(2.0, gt=1, description="Frequency multiplier per octave.")
    gain: float = Field(0.5, gt=0, lt=1, description="Amplitude multiplier per octave.")
    offset: float = Field(0.0, description="Added to every height, metres.")


class HeightGrid(IRModel):
    """Heights given directly as a grid of numbers."""

    model_config = cfg("v2")

    source: Literal["grid"] = "grid"
    rows: int = Field(ge=2)
    cols: int = Field(ge=2)
    data: list[float] = Field(description="rows × cols heights in metres, row-major, starting at -x, -z.")

    @model_validator(mode="after")
    def _size_matches(self) -> "HeightGrid":
        if len(self.data) != self.rows * self.cols:
            raise ValueError(f"grid data has {len(self.data)} values, expected {self.rows * self.cols}")
        return self


class HeightImage(IRModel):
    """Heights read from a greyscale image."""

    model_config = cfg("v2")

    source: Literal["image"] = "image"
    uri: str
    min_height: float = 0.0
    max_height: float = 10.0


TerrainHeight = Annotated[
    Union[HeightFlat, HeightNoise, HeightGrid, HeightImage],
    Field(discriminator="source"),
]


class Circle(IRModel):
    """A circular area on the ground."""

    model_config = cfg("v2")

    center: Vec2
    radius: float = Field(gt=0)


Area = Union[Circle, Polygon2]


class ModFlatten(IRModel):
    """Levels an area, e.g. a pad for a building."""

    model_config = cfg("v2")

    op: Literal["flatten"] = "flatten"
    area: Area
    height: float
    falloff: float = Field(2.0, ge=0, description="Width of the blend at the edge, metres.")


class ModBump(IRModel):
    """Raises (positive amount) or lowers (negative) a round area."""

    model_config = cfg("v2")

    op: Literal["bump"] = "bump"
    center: Vec2
    radius: float = Field(gt=0)
    amount: float
    shape: Literal["smooth", "cone", "plateau"] = "smooth"


class ModSmooth(IRModel):
    """Softens heights in an area, or everywhere when no area is given."""

    model_config = cfg("v2")

    op: Literal["smooth"] = "smooth"
    area: Optional[Area] = None
    iterations: int = Field(2, ge=1, le=50)


class ModCarvePath(IRModel):
    """Cuts a channel or levels a strip along a path node: rivers, roads, trails."""

    model_config = cfg("v2")

    op: Literal["carve_path"] = "carve_path"
    path: Id = Field(json_schema_extra=ref("path"))
    depth: float = Field(0.3, description="Metres below the surrounding ground; 0 levels the strip.")
    width: float = Field(3.0, gt=0)
    falloff: float = Field(2.0, ge=0)


class ModTerrace(IRModel):
    """Turns slopes into steps."""

    model_config = cfg("v2")

    op: Literal["terrace"] = "terrace"
    area: Optional[Area] = None
    step_height: float = Field(1.0, gt=0)
    sharpness: float = Field(0.7, ge=0, le=1)


class ModCrater(IRModel):
    """A bowl with a raised rim."""

    model_config = cfg("v2")

    op: Literal["crater"] = "crater"
    center: Vec2
    radius: float = Field(gt=0)
    depth: float = Field(gt=0)
    rim_height: float = Field(0.5, ge=0)


class ModErosion(IRModel):
    """Simulated weathering for more natural slopes."""

    model_config = cfg("later")

    op: Literal["erosion"] = "erosion"
    kind: Literal["hydraulic", "thermal"] = "hydraulic"
    iterations: int = Field(50, ge=1, le=10000)
    strength: float = Field(0.3, ge=0, le=1)


TerrainModifier = Annotated[
    Union[ModFlatten, ModBump, ModSmooth, ModCarvePath, ModTerrace, ModCrater, ModErosion],
    Field(discriminator="op"),
]


class LayerRule(IRModel):
    """Where a terrain material shows. All limits are optional and combine with AND."""

    model_config = cfg("v2")

    height_min: Optional[float] = None
    height_max: Optional[float] = None
    slope_min_deg: Optional[float] = Field(None, ge=0, le=90)
    slope_max_deg: Optional[float] = Field(None, ge=0, le=90)
    zone: Optional[Id] = Field(None, description="Only inside this zone node.", json_schema_extra=ref("zone"))
    blend: float = Field(0.5, ge=0, description="Softness of the boundary, metres.")


class TerrainLayer(IRModel):
    """A material painted onto the terrain by rules. Later layers paint over earlier ones."""

    model_config = cfg("v2")

    material: Id = Field(json_schema_extra=ref("material"))
    rule: LayerRule = Field(default_factory=LayerRule)
