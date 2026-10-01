"""The top-level world file and the checks that tie its parts together."""

from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from .architecture import Opening
from .assets import AssetDef, Material
from .common import Color, Id, IRModel, Provenance, cfg
from .environment import Environment
from .nodes import Node, PathNode, RoomNode, ZoneNode
from .relations import Relation

IR_VERSION = "1.0"


class Requirement(IRModel):
    """Something the brief says the world must contain."""

    model_config = cfg("v1")

    category: str
    min: int = Field(1, ge=0)
    max: Optional[int] = Field(None, ge=0)
    region: Optional[str] = Field(None, description="Room type or zone label, e.g. 'bedroom'.")
    source: Literal["prompt", "image_brief", "archetype", "user"] = "prompt"
    confidence: float = Field(1.0, ge=0, le=1, description="From image briefs: share of images showing it.")


class ImageRef(IRModel):
    """A reference image behind the brief: the mood board."""

    model_config = cfg("v2")

    url: str
    license: Optional[str] = Field(None, description="e.g. 'CC-BY-2.0', from Openverse.")
    caption: Optional[str] = Field(None, description="What a vision model saw in it.")
    relevance: float = Field(1.0, ge=0, le=1)
    approved: bool = Field(False, description="The user kept it on the mood board.")


class Brief(IRModel):
    """What the user asked for, kept with the world so it can be checked against."""

    model_config = cfg("v1")

    prompt: str = Field(description="The user's words, unedited.")
    setting: Literal["interior", "exterior", "mixed"] = Field("interior", description="Indoors, outdoors or both.")
    archetype: Optional[str] = Field(None, description="See ARCHETYPES, e.g. 'bedroom', 'forest_clearing'.")
    style: list[str] = Field(default_factory=list, description="See STYLE_TAGS.")
    palette: list[Color] = Field(default_factory=list)
    time_of_day: Optional[str] = None
    season: Optional[Literal["spring", "summer", "autumn", "winter"]] = None
    mood: list[str] = Field(default_factory=list, description="e.g. ['cosy', 'quiet'].")
    required: list[Requirement] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list, description="Categories that must not appear.")
    references: list[ImageRef] = Field(default_factory=list, json_schema_extra={"x-tier": "v2"})
    notes: Optional[str] = None


class OverlapAllowance(IRModel):
    """Two categories that may overlap by default, e.g. chairs under tables."""

    model_config = cfg("v1")

    a: str
    b: str
    max_fraction: float = Field(0.5, gt=0, le=1)


class ValidationRules(IRModel):
    """Thresholds for the built-in checks. Defaults suit furnished interiors."""

    model_config = cfg("v1")

    float_tolerance: float = Field(0.02, ge=0, description="Metres a base may sit above its support.")
    sink_tolerance: float = Field(0.02, ge=0, description="Metres a base may sit below its support.")
    overlap_fraction: float = Field(
        0.02, ge=0, le=1, description="Footprint overlap allowed, share of the smaller one."
    )
    overlap_height: float = Field(0.05, ge=0, description="Vertical overlap allowed, metres.")
    wall_tolerance: float = Field(0.01, ge=0, description="Metres a footprint may cross a wall.")
    door_clearance: float = Field(0.9, gt=0, description="Depth of the free zone in front of a door.")
    walkway_width: float = Field(0.6, gt=0)
    scale_ratio: tuple[float, float] = Field((0.5, 2.0), description="Allowed multiple of the typical category size.")
    max_walkable_slope_deg: float = Field(35.0, gt=0, le=90)
    floor_covering_max_height: float = Field(
        0.03, ge=0, description="Objects thinner than this (rugs, mats) count as floor."
    )
    allowed_overlaps: list[OverlapAllowance] = Field(
        default_factory=lambda: [
            OverlapAllowance(a="chair", b="table"),
            OverlapAllowance(a="chair", b="desk"),
            OverlapAllowance(a="stool", b="counter"),
            OverlapAllowance(a="stool", b="kitchen island"),
            OverlapAllowance(a="ottoman", b="coffee table", max_fraction=0.3),
        ]
    )
    checks: list[
        Literal[
            "floating",
            "sunk",
            "overlap",
            "out_of_bounds",
            "facing",
            "scale",
            "door_clearance",
            "reachability",
            "relations",
            "requirements",
        ]
    ] = Field(
        default_factory=lambda: [
            "floating",
            "sunk",
            "overlap",
            "out_of_bounds",
            "facing",
            "scale",
            "door_clearance",
            "relations",
        ],
        description="Checks to run. 'reachability' and 'requirements' are off by default.",
    )


class Prefab(IRModel):
    """A saved group of nodes, placed many times with PrefabNode: a dining set, a bunk bed."""

    model_config = cfg("v2")

    name: Optional[str] = None
    description: Optional[str] = None
    nodes: list[Node] = Field(min_length=1, description="Node IDs inside a prefab are local to it.")
    tags: list[str] = Field(default_factory=list)


class World(IRModel):
    """One world file: everything needed to rebuild, check and repair a scene."""

    model_config = cfg("v1")

    ir_version: Literal["1.0"] = Field(IR_VERSION, description="Schema version. Bump it when the format changes.")
    id: Id
    name: Optional[str] = Field(None, description="Human-readable title.")
    units: Literal["metres"] = Field("metres", description="Fixed. Present so readers don't have to guess.")
    up_axis: Literal["+y"] = Field("+y", description="Fixed. +Y is up.")
    seed: int = Field(0, description="Seed for every random choice that has no seed of its own.")
    brief: Optional[Brief] = Field(None, description="What was asked for. Checks and rewards can compare against it.")
    environment: Environment = Field(default_factory=Environment, description="Sky, sun, fog and lighting.")
    assets: dict[Id, AssetDef] = Field(default_factory=dict, description="Asset registry, keyed by asset ID.")
    materials: dict[Id, Material] = Field(default_factory=dict, description="Material registry, keyed by ID.")
    prefabs: dict[Id, Prefab] = Field(
        default_factory=dict, description="Reusable node groups, keyed by ID.", json_schema_extra={"x-tier": "v2"}
    )
    nodes: list[Node] = Field(default_factory=list, description="The scene tree's top-level nodes.")
    relations: list[Relation] = Field(default_factory=list, description="Declared intent the validators check.")
    rules: ValidationRules = Field(default_factory=ValidationRules, description="Thresholds for the built-in checks.")
    provenance: Optional[Provenance] = Field(None, description="Who made this file.")
    extras: dict[str, Any] = Field(default_factory=dict, description="Free-form data tools may attach.")

    # Walking --------------------------------------------------------------------

    def walk(self) -> Iterator[tuple[Any, Optional[Any]]]:
        """Yields (node, parent) for every node in the scene tree, parents first."""

        def visit(node: Any, parent: Optional[Any]) -> Iterator[tuple[Any, Optional[Any]]]:
            yield node, parent
            for child in node.children:
                yield from visit(child, node)

        for root in self.nodes:
            yield from visit(root, None)

    def node(self, node_id: str) -> Any:
        """Returns the node with this ID, or raises KeyError."""
        for node, _ in self.walk():
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    # Cross-reference checks ------------------------------------------------------

    @model_validator(mode="after")
    def _references_resolve(self) -> "World":
        errors: list[str] = []
        nodes: dict[str, Any] = {}
        for node, _ in self.walk():
            if node.id in nodes:
                errors.append(f"duplicate node id '{node.id}'")
            nodes[node.id] = node

        rooms = {n.id: n for n in nodes.values() if isinstance(n, RoomNode)}
        opening_list = [o for n in nodes.values() for o in _find(n, Opening, skip_children=True)]
        openings = {o.id for o in opening_list}
        if len(openings) != len(opening_list):
            errors.append("opening ids must be unique")
        zones = {n.id for n in nodes.values() if isinstance(n, ZoneNode)}
        paths = {n.id for n in nodes.values() if isinstance(n, PathNode)}
        clash = openings & set(nodes)
        if clash:
            errors.append(f"opening ids clash with node ids: {sorted(clash)}")

        known = {
            "node": set(nodes),
            "asset": set(self.assets),
            "material": set(self.materials),
            "prefab": set(self.prefabs),
            "opening": openings,
            "zone": zones,
            "path": paths,
            "node_or_opening": set(nodes) | openings,
        }

        def check(kind: str, value: Any, where: str, scope: dict[str, set[str]]) -> None:
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str):
                    continue  # coordinates given instead of an ID
                if kind == "wall":
                    room_id, _, edge = item.partition(":")
                    room = rooms.get(room_id)
                    if room is None:
                        errors.append(f"{where}: no room '{room_id}'")
                    elif int(edge) >= len(room.outline):
                        errors.append(f"{where}: room '{room_id}' has no wall {edge}")
                elif item not in scope[kind]:
                    errors.append(f"{where}: unknown {kind} '{item}'")

        def scan(model: BaseModel, where: str, scope: dict[str, set[str]], skip_children: bool = False) -> None:
            for name, field in type(model).model_fields.items():
                if skip_children and name == "children":
                    continue
                value = getattr(model, name)
                extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
                kind = extra.get("x-ref")
                if kind and value is not None:
                    check(kind, value, f"{where}.{name}", scope)
                for sub, label in _models_in(value, f"{where}.{name}"):
                    scan(sub, label, scope)

        for node in nodes.values():
            scan(node, f"node '{node.id}'", known, skip_children=True)
        for i, relation in enumerate(self.relations):
            scan(relation, f"relation {relation.id or i}", known)
        for name, asset in self.assets.items():
            scan(asset, f"asset '{name}'", known)
        if self.brief:
            scan(self.brief, "brief", known)
        for name, prefab in self.prefabs.items():
            local = [sub for root in prefab.nodes for sub in _iter_tree(root)]
            local_ids = {sub.id for sub in local}
            scope = {**known, "node": local_ids, "node_or_opening": local_ids}
            for sub in local:
                scan(sub, f"prefab '{name}' node '{sub.id}'", scope, skip_children=True)

        if errors:
            raise ValueError("world references do not resolve:\n  " + "\n  ".join(sorted(set(errors))))
        return self


def _models_in(value: Any, where: str) -> Iterator[tuple[BaseModel, str]]:
    if isinstance(value, BaseModel):
        yield value, where
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            if isinstance(item, BaseModel):
                yield item, f"{where}[{i}]"
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, BaseModel):
                yield item, f"{where}[{key}]"


def _find(model: BaseModel, cls: type, skip_children: bool = False) -> Iterator[Any]:
    """Yields every instance of cls nested inside model."""
    for name in type(model).model_fields:
        if skip_children and name == "children":
            continue
        for sub, _ in _models_in(getattr(model, name), name):
            if isinstance(sub, cls):
                yield sub
            yield from _find(sub, cls)


def _iter_tree(node: Any) -> Iterator[Any]:
    yield node
    for child in node.children:
        yield from _iter_tree(child)
