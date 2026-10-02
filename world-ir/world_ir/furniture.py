"""The furniture library: Kenney's Furniture Kit 2.0 (CC0), measured, with what each piece is and where it goes.

Sizes, top surfaces and material slots come from the models themselves
(``scripts/measure_assets.py --surfaces``, kept in ``data/kenney_furniture.json``).
What a piece is and how it is mounted is written here by hand. Every model
faces +z with its back at -z; the measured ``back_z`` confirms it for the
pieces that have a back.
"""

import json
import pathlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Optional

from .assets import AssetDef, AssetSurface, License, Placement

Mount = Literal["floor", "wall", "ceiling", "surface", "covering"]

KIT_SCALE = 1.9
URI = "assets/kenney/furniture/{name}.glb"
LICENSE = License(spdx="CC0-1.0", attribution="Kenney Furniture Kit 2.0 (kenney.nl)")
DATA = pathlib.Path(__file__).with_name("data") / "kenney_furniture.json"

# name: (category, mount, function, holds things on top)
CATALOGUE: dict[str, tuple[str, Mount, Optional[str], bool]] = {
    "bathroomCabinet": ("wall cabinet", "wall", "store", False),
    "bathroomCabinetDrawer": ("bathroom cabinet", "floor", "store", True),
    "bathroomMirror": ("mirror", "wall", "decorate", False),
    "bathroomSink": ("sink", "floor", "wash", False),
    "bathroomSinkSquare": ("sink", "floor", "wash", False),
    "bathtub": ("bathtub", "floor", "wash", False),
    "bear": ("teddy bear", "floor", "play", False),
    "bedBunk": ("bunk bed", "floor", "sleep", False),
    "bedDouble": ("bed", "floor", "sleep", True),
    "bedSingle": ("bed", "floor", "sleep", True),
    "bench": ("bench", "floor", "sit", False),
    "benchCushion": ("bench", "floor", "sit", False),
    "benchCushionLow": ("low bench", "floor", "sit", True),
    "bookcaseClosed": ("bookcase", "floor", "store", True),
    "bookcaseClosedDoors": ("bookcase", "floor", "store", True),
    "bookcaseClosedWide": ("bookcase", "floor", "store", True),
    "bookcaseOpen": ("bookcase", "floor", "store", True),
    "bookcaseOpenLow": ("low bookcase", "floor", "store", True),
    "books": ("books", "surface", "decorate", False),
    "cabinetBed": ("nightstand", "floor", "store", True),
    "cabinetBedDrawer": ("nightstand", "floor", "store", True),
    "cabinetBedDrawerTable": ("nightstand", "floor", "store", True),
    "cabinetTelevision": ("tv cabinet", "floor", "store", True),
    "cabinetTelevisionDoors": ("tv cabinet", "floor", "store", True),
    "cardboardBoxClosed": ("box", "floor", "store", True),
    "cardboardBoxOpen": ("box", "floor", "store", False),
    "ceilingFan": ("ceiling fan", "ceiling", None, False),
    "chair": ("chair", "floor", "sit", False),
    "chairCushion": ("chair", "floor", "sit", False),
    "chairDesk": ("desk chair", "floor", "sit", False),
    "chairModernCushion": ("chair", "floor", "sit", False),
    "chairModernFrameCushion": ("chair", "floor", "sit", False),
    "chairRounded": ("chair", "floor", "sit", False),
    "coatRack": ("coat hooks", "wall", "store", False),
    "coatRackStanding": ("coat rack", "floor", "store", False),
    "computerKeyboard": ("keyboard", "surface", "work", False),
    "computerMouse": ("computer mouse", "surface", "work", False),
    "computerScreen": ("computer screen", "surface", "work", False),
    "desk": ("desk", "floor", "work", True),
    "dryer": ("dryer", "floor", "wash", True),
    "hoodLarge": ("range hood", "wall", "cook", False),
    "hoodModern": ("range hood", "wall", "cook", False),
    "kitchenBar": ("bar counter", "floor", "eat", True),
    "kitchenBarEnd": ("bar counter", "floor", "eat", False),
    "kitchenBlender": ("blender", "surface", "cook", False),
    "kitchenCabinet": ("kitchen cabinet", "floor", "cook", True),
    "kitchenCabinetDrawer": ("kitchen cabinet", "floor", "cook", True),
    "kitchenCabinetUpper": ("wall cabinet", "wall", "store", False),
    "kitchenCabinetUpperDouble": ("wall cabinet", "wall", "store", False),
    "kitchenCabinetUpperLow": ("wall cabinet", "wall", "store", False),
    "kitchenCoffeeMachine": ("coffee machine", "surface", "cook", False),
    "kitchenFridge": ("fridge", "floor", "cook", False),
    "kitchenFridgeBuiltIn": ("fridge", "floor", "cook", False),
    "kitchenFridgeLarge": ("fridge", "floor", "cook", False),
    "kitchenFridgeSmall": ("fridge", "floor", "cook", True),
    "kitchenMicrowave": ("microwave", "surface", "cook", False),
    "kitchenSink": ("kitchen sink", "floor", "wash", False),
    "kitchenStove": ("stove", "floor", "cook", False),
    "kitchenStoveElectric": ("stove", "floor", "cook", False),
    "lampRoundFloor": ("floor lamp", "floor", "light", False),
    "lampRoundTable": ("table lamp", "surface", "light", False),
    "lampSquareCeiling": ("ceiling lamp", "ceiling", "light", False),
    "lampSquareFloor": ("floor lamp", "floor", "light", False),
    "lampSquareTable": ("table lamp", "surface", "light", False),
    "lampWall": ("wall lamp", "wall", "light", False),
    "laptop": ("laptop", "surface", "work", False),
    "loungeChair": ("armchair", "floor", "sit", False),
    "loungeChairRelax": ("armchair", "floor", "sit", False),
    "loungeDesignChair": ("armchair", "floor", "sit", False),
    "loungeDesignSofa": ("sofa", "floor", "sit", False),
    "loungeSofa": ("sofa", "floor", "sit", False),
    "loungeSofaLong": ("sofa", "floor", "sit", False),
    "loungeSofaOttoman": ("ottoman", "floor", "sit", True),
    "pillow": ("pillow", "surface", "decorate", False),
    "pillowBlue": ("pillow", "surface", "decorate", False),
    "pillowBlueLong": ("pillow", "surface", "decorate", False),
    "pillowLong": ("pillow", "surface", "decorate", False),
    "plantSmall1": ("small plant", "surface", "decorate", False),
    "plantSmall2": ("small plant", "surface", "decorate", False),
    "plantSmall3": ("small plant", "surface", "decorate", False),
    "pottedPlant": ("potted plant", "floor", "decorate", False),
    "radio": ("radio", "surface", "play", False),
    "rugDoormat": ("doormat", "covering", "decorate", False),
    "rugRectangle": ("rug", "covering", "decorate", False),
    "rugRound": ("rug", "covering", "decorate", False),
    "rugRounded": ("rug", "covering", "decorate", False),
    "rugSquare": ("rug", "covering", "decorate", False),
    "shower": ("shower", "floor", "wash", False),
    "sideTable": ("side table", "floor", "store", True),
    "sideTableDrawers": ("side table", "floor", "store", True),
    "speaker": ("speaker", "floor", "play", False),
    "speakerSmall": ("small speaker", "surface", "play", False),
    "stoolBar": ("bar stool", "floor", "sit", False),
    "stoolBarSquare": ("bar stool", "floor", "sit", False),
    "table": ("dining table", "floor", "eat", True),
    "tableCloth": ("dining table", "floor", "eat", True),
    "tableCoffee": ("coffee table", "floor", "display", True),
    "tableCoffeeGlass": ("coffee table", "floor", "display", True),
    "tableCoffeeGlassSquare": ("coffee table", "floor", "display", True),
    "tableCoffeeSquare": ("coffee table", "floor", "display", True),
    "tableCross": ("dining table", "floor", "eat", True),
    "tableCrossCloth": ("dining table", "floor", "eat", True),
    "tableGlass": ("dining table", "floor", "eat", True),
    "tableRound": ("dining table", "floor", "eat", True),
    "televisionModern": ("television", "surface", "play", False),
    "televisionVintage": ("television", "surface", "play", False),
    "toaster": ("toaster", "surface", "cook", False),
    "toilet": ("toilet", "floor", "wash", False),
    "toiletSquare": ("toilet", "floor", "wash", False),
    "trashcan": ("trash can", "floor", "store", False),
    "washer": ("washing machine", "floor", "wash", True),
    "washerDryerStacked": ("washer dryer", "floor", "wash", False),
}

TOP_OVERRIDES = {"cardboardBoxClosed": 0.534}
"""Tops measured lower than the model's real top: the closed box's flaps dip, but a box stacked on it rests on the rim."""

SURFACE_ALSO = {"bear", "speakerSmall", "cardboardBoxClosed", "televisionVintage"}
"""Floor pieces that can also stand on a surface (a teddy on a bed, a box on a shelf)."""


@dataclass(frozen=True)
class Piece:
    """One furniture model: what it is, how big, and where it can go."""

    name: str
    category: str
    mount: Mount
    function: Optional[str]
    dims: tuple[float, float, float]
    slots: tuple[str, ...]
    top: Optional[float] = None
    area: Optional[tuple[float, float, float, float]] = None

    @property
    def w(self) -> float:
        return self.dims[0]

    @property
    def h(self) -> float:
        return self.dims[1]

    @property
    def d(self) -> float:
        return self.dims[2]

    @property
    def on_surfaces(self) -> bool:
        return self.mount == "surface" or self.name in SURFACE_ALSO

    def asset(self) -> AssetDef:
        surfaces = []
        if self.top is not None:
            x0, z0, x1, z1 = self.area or (-self.w / 2, -self.d / 2, self.w / 2, self.d / 2)
            area = [(round(x0, 3), round(z0, 3)), (round(x1, 3), round(z0, 3))]
            area += [(round(x1, 3), round(z1, 3)), (round(x0, 3), round(z1, 3))]
            surfaces.append(AssetSurface(name="top", height=self.top, area=area))
        return AssetDef(
            source="kenney",
            uri=URI.format(name=self.name),
            license=LICENSE,
            category=self.category,
            dims=self.dims,
            placement=Placement(
                floor=self.mount in ("floor", "covering"),
                wall=self.mount == "wall",
                ceiling=self.mount == "ceiling",
                on_object=self.on_surfaces,
                receptacle=self.top is not None,
            ),
            surfaces=surfaces,
            material_slots=list(self.slots),
            style=["low_poly"],
            extras={"kit_scale": KIT_SCALE, "model": self.name},
        )


@lru_cache(maxsize=1)
def library() -> dict[str, Piece]:
    """Every catalogued piece, keyed by model name."""
    measured = json.loads(DATA.read_text())
    out = {}
    for name, (category, mount, function, holds) in CATALOGUE.items():
        m = measured[name]
        top = area = None
        if holds and m.get("top"):
            top = min(TOP_OVERRIDES.get(name, m["top"]["height"]), m["dims"][1])
            area = tuple(m["top"]["area"])
        out[name] = Piece(name, category, mount, function, tuple(m["dims"]), tuple(m["material_slots"]), top, area)
    return out


def piece(name: str) -> Piece:
    return library()[name]


def by_category(category: str) -> list[Piece]:
    return [p for p in library().values() if p.category == category]
