"""Sky, sun, fog, ambient light and other world-wide settings."""

from typing import Annotated, Literal, Optional, Union

from pydantic import Field

from .common import Color, IRModel, cfg


class SkyColor(IRModel):
    """A flat background colour."""

    model_config = cfg("v1")

    type: Literal["color"] = "color"
    color: Color = "#bcd3e6"


class SkyGradient(IRModel):
    """A vertical gradient from horizon to zenith."""

    model_config = cfg("v1")

    type: Literal["gradient"] = "gradient"
    zenith: Color = "#6a9bd1"
    horizon: Color = "#dfe9f2"
    ground: Color = "#8a8478"


class SkyProcedural(IRModel):
    """A physical sky model (Three.js Sky). The sun position comes from ``sun``."""

    model_config = cfg("v2")

    type: Literal["procedural"] = "procedural"
    turbidity: float = Field(10.0, ge=0, description="Haze. Clear sky about 2, hazy 10+.")
    rayleigh: float = Field(2.0, ge=0, description="Blue scattering strength.")
    mie_coefficient: float = Field(0.005, ge=0)
    mie_directional_g: float = Field(0.8, ge=0, le=1)


class SkyHdri(IRModel):
    """An equirectangular HDR image used for background and lighting."""

    model_config = cfg("v2")

    type: Literal["hdri"] = "hdri"
    uri: str = Field(description="Path or URL of an .hdr or .exr file, e.g. from Poly Haven.")
    rotation_deg: float = 0.0
    intensity: float = Field(1.0, ge=0)
    use_as_background: bool = True


Sky = Annotated[
    Union[SkyColor, SkyGradient, SkyProcedural, SkyHdri],
    Field(discriminator="type"),
]


class FogLinear(IRModel):
    """Fog that thickens linearly between two distances."""

    model_config = cfg("v1")

    type: Literal["linear"] = "linear"
    color: Color = "#dfe9f2"
    near: float = Field(10.0, ge=0)
    far: float = Field(80.0, gt=0)


class FogExp2(IRModel):
    """Exponential fog, closer to real haze."""

    model_config = cfg("v1")

    type: Literal["exp2"] = "exp2"
    color: Color = "#dfe9f2"
    density: float = Field(0.015, gt=0)


Fog = Annotated[Union[FogLinear, FogExp2], Field(discriminator="type")]


class Ambient(IRModel):
    """Light that reaches everything equally, or from sky and ground (hemisphere)."""

    model_config = cfg("v1")

    type: Literal["ambient", "hemisphere"] = "hemisphere"
    color: Color = "#ffffff"
    ground_color: Color = Field("#6b6157", description="Hemisphere only: colour from below.")
    intensity: float = Field(0.6, ge=0)


class Sun(IRModel):
    """The main directional light for exteriors."""

    model_config = cfg("v1")

    elevation_deg: float = Field(45.0, ge=-90, le=90, description="Angle above the horizon.")
    azimuth_deg: float = Field(135.0, description="Compass direction the sun shines from; 0 is +Z, 90 is +X.")
    color: Optional[Color] = Field(None, description="Overrides kelvin.")
    kelvin: float = Field(5600.0, ge=1000, le=20000)
    intensity_lux: float = Field(3.0, ge=0, description="Renderer units; about 3 reads as daylight.")
    cast_shadows: bool = True
    shadow_map_size: Literal[512, 1024, 2048, 4096] = 2048


class Weather(IRModel):
    """Precipitation and atmosphere effects."""

    model_config = cfg("later")

    kind: Literal["clear", "rain", "snow", "fog", "storm", "dust"] = "clear"
    intensity: float = Field(0.5, ge=0, le=1)


class Wind(IRModel):
    """Wind used by vegetation sway and particles."""

    model_config = cfg("later")

    direction_deg: float = 0.0
    speed_m_s: float = Field(2.0, ge=0)
    gustiness: float = Field(0.2, ge=0, le=1)


class Environment(IRModel):
    """World-wide visual and physical settings."""

    model_config = cfg("v1")

    sky: Sky = Field(default_factory=SkyGradient)
    sun: Optional[Sun] = Field(None, description="Leave empty for windowless interiors.")
    ambient: Ambient = Field(default_factory=Ambient)
    fog: Optional[Fog] = None
    time_of_day: Optional[str] = Field(
        None,
        pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$",
        description="'HH:MM'. When set, tools may derive the sun position from it.",
    )
    tone_mapping: Literal["none", "linear", "reinhard", "cineon", "aces", "agx", "neutral"] = "aces"
    exposure: float = Field(1.0, gt=0)
    gravity_m_s2: float = -9.81
    weather: Optional[Weather] = Field(None, json_schema_extra={"x-tier": "later"})
    wind: Optional[Wind] = Field(None, json_schema_extra={"x-tier": "later"})
