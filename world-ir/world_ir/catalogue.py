"""Reads the models and returns a plain-data catalogue for the reference docs.

The docs are generated from the code, so the reference cannot drift from the schema.
"""

import inspect
from typing import Any, Optional

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from . import architecture, assets, common, environment, lowered, terrain, world
from .actions import ACTIONS_BY_TIER
from .generators import GENERATOR_GROUPS, KitchenModule, SweepSupports, WindowGrid
from .nodes import NODE_KINDS, NodeBase, NodePatch, ScatterItem, WallOverride
from .relations import RELATION_GROUPS, RelationBase
from .vocab import ARCHETYPES, BIOMES, FUNCTIONS, ODDNESS_LICENCES, ROOM_TYPES, STYLE_TAGS

DISCRIMINATORS = ("kind", "gen", "rel", "action", "type", "source", "op")
UNION_NAMES = {
    "kind": "Node",
    "gen": "Generator",
    "rel": "Relation",
    "action": "Action",
    "op": "TerrainModifier",
    "source": "TerrainHeight",
}


def _tier(model: type[BaseModel]) -> str:
    extra = model.model_config.get("json_schema_extra") or {}
    return extra.get("x-tier", "v1") if isinstance(extra, dict) else "v1"


def _doc(model: type[BaseModel]) -> str:
    return inspect.cleandoc(model.__doc__ or "").split("\n\n")[0].replace("\n", " ")


def _render_type(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return repr(schema["const"]).replace("'", '"')
    if "enum" in schema:
        return " | ".join(f'"{v}"' if isinstance(v, str) else str(v) for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            prop_name = schema.get("discriminator", {}).get("propertyName")
            if prop_name and len(schema[key]) > 4:
                return UNION_NAMES.get(prop_name, "one of several kinds")
            parts = [_render_type(s, defs) for s in schema[key]]
            parts = [p for p in parts if p != "null"]
            text = " | ".join(dict.fromkeys(parts))
            nullable = any(s.get("type") == "null" for s in schema[key])
            return f"{text} (optional)" if nullable and len(parts) == 1 else text
    kind = schema.get("type")
    if kind == "array":
        if "prefixItems" in schema:
            inner = [_render_type(s, defs) for s in schema["prefixItems"]]
            if len(set(inner)) == 1:
                return f"[{inner[0]} × {len(inner)}]"
            return "[" + ", ".join(inner) + "]"
        item = _render_type(schema.get("items", {}), defs)
        bounds = ""
        if "minItems" in schema:
            bounds = f", ≥{schema['minItems']}"
        return f"list[{item}{bounds}]"
    if kind == "object":
        if isinstance(schema.get("additionalProperties"), dict):
            return f"map[string → {_render_type(schema['additionalProperties'], defs)}]"
        if schema.get("patternProperties"):
            inner = next(iter(schema["patternProperties"].values()))
            return f"map[id → {_render_type(inner, defs)}]"
        return "object"
    if kind == "string" and "pattern" in schema:
        pattern = schema["pattern"]
        if pattern.startswith("^[a-z][a-z0-9_]{0,63}:"):
            return "wall ref"
        if pattern.startswith("^[a-z][a-z0-9_]"):
            return "id"
        if pattern.startswith("^#"):
            return "colour"
        return "string"
    if kind == "number":
        return "number"
    return kind or "any"


def _limits(schema: dict[str, Any]) -> str:
    parts = []
    for key, label in (("minimum", "≥"), ("exclusiveMinimum", ">"), ("maximum", "≤"), ("exclusiveMaximum", "<")):
        if key in schema:
            parts.append(f"{label}{schema[key]}")
    for option in schema.get("anyOf", []):
        if option.get("type") != "null":
            return _limits(option)
    return " ".join(parts)


def describe(model: type[BaseModel], exclude: Optional[set[str]] = None) -> dict[str, Any]:
    """One model as plain data: tier, summary and fields."""
    exclude = exclude or set()
    schema = model.model_json_schema(mode="validation")
    defs = schema.get("$defs", {})
    if "$ref" in schema and "properties" not in schema:
        schema = defs[schema["$ref"].split("/")[-1]]  # recursive models come back as a reference
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    model_tier = _tier(model)
    fields = []
    tag = None
    for name, field in model.model_fields.items():
        key = field.alias or name
        if name in exclude:
            continue
        prop = props.get(key, {})
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if name in DISCRIMINATORS and "const" in prop:
            tag = (key, prop["const"])
            continue
        default = field.default
        if field.default_factory is not None:
            default_text = "auto"
        elif default is PydanticUndefined:
            default_text = "required" if key in required else ""
        elif field.default_factory is not None:
            default_text = "auto"
        elif isinstance(default, BaseModel):
            default_text = "auto"
        else:
            default_text = (
                repr(default)
                .replace("'", '"')
                .replace("None", "none")
                .replace("True", "true")
                .replace("False", "false")
            )
        fields.append(
            {
                "name": key,
                "type": _render_type(prop, defs),
                "limits": _limits(prop),
                "default": default_text,
                "description": field.description or "",
                "tier": extra.get("x-tier", model_tier),
                "ref": extra.get("x-ref"),
                "repair": extra.get("x-repair"),
            }
        )
    return {
        "name": model.__name__,
        "tag": {"field": tag[0], "value": tag[1]} if tag else None,
        "tier": model_tier,
        "summary": _doc(model),
        "fields": fields,
    }


def build() -> dict[str, Any]:
    """The full catalogue, grouped the way the reference presents it."""
    node_base = set(NodeBase.model_fields)
    rel_base = set(RelationBase.model_fields)
    return {
        "conventions": common.CONVENTIONS,
        "sections": [
            {
                "id": "document",
                "title": "World file",
                "intro": "The top level of a world file and the objects it holds directly.",
                "models": [
                    describe(m)
                    for m in (
                        world.World,
                        world.Brief,
                        world.Requirement,
                        world.ImageRef,
                        world.ValidationRules,
                        world.OverlapAllowance,
                        world.Prefab,
                    )
                ],
            },
            {
                "id": "shared",
                "title": "Shared building blocks",
                "intro": "Small objects used by many nodes.",
                "models": [
                    describe(m)
                    for m in (
                        common.Transform,
                        common.Support,
                        common.Semantic,
                        common.Physics,
                        common.Provenance,
                        common.Behavior,
                        common.Intent,
                        architecture.Opening,
                        architecture.RoofSpec,
                    )
                ],
            },
            {
                "id": "environment",
                "title": "Environment",
                "intro": "Sky, sun, fog and other world-wide settings.",
                "models": [
                    describe(m)
                    for m in (
                        environment.Environment,
                        environment.SkyColor,
                        environment.SkyGradient,
                        environment.SkyProcedural,
                        environment.SkyHdri,
                        environment.SkySpace,
                        environment.CelestialBody,
                        environment.PlanetRing,
                        environment.Sun,
                        environment.Ambient,
                        environment.FogLinear,
                        environment.FogExp2,
                        environment.PostEffects,
                        environment.Bloom,
                        environment.Weather,
                        environment.Wind,
                    )
                ],
            },
            {
                "id": "assets",
                "title": "Assets and materials",
                "intro": "Registries that nodes refer to by ID.",
                "models": [
                    describe(m)
                    for m in (
                        assets.AssetDef,
                        assets.License,
                        assets.Placement,
                        assets.AssetSurface,
                        assets.Material,
                        assets.TextureMaps,
                        assets.MaterialSlotOverride,
                    )
                ],
                "presets": assets.MATERIAL_PRESETS,
            },
            {
                "id": "nodes",
                "title": "Node kinds",
                "intro": "Everything that can appear in the scene tree. Every kind also has the common node fields.",
                "models": [describe(NodeBase)]
                + [describe(m, exclude=node_base) for m in NODE_KINDS]
                + [describe(m) for m in (WallOverride, NodePatch, ScatterItem)],
            },
            {
                "id": "terrain",
                "title": "Terrain",
                "intro": "Height sources, edits applied in order, and painted material layers.",
                "models": [
                    describe(m)
                    for m in (
                        terrain.HeightFlat,
                        terrain.HeightNoise,
                        terrain.HeightGrid,
                        terrain.HeightImage,
                        terrain.Circle,
                        terrain.ModFlatten,
                        terrain.ModBump,
                        terrain.ModSmooth,
                        terrain.ModCarvePath,
                        terrain.ModTerrace,
                        terrain.ModCrater,
                        terrain.ModErosion,
                        terrain.TerrainLayer,
                        terrain.LayerRule,
                    )
                ],
            },
            {
                "id": "generators",
                "title": "Generators",
                "intro": "Parametric geometry. Agents pick a generator and set numbers; they never write geometry code.",
                "groups": [
                    {"title": group, "models": [describe(m) for m in models]}
                    for group, models in GENERATOR_GROUPS.items()
                ]
                + [
                    {
                        "title": "Parts used by generators",
                        "models": [describe(m) for m in (WindowGrid, KitchenModule, SweepSupports)],
                    }
                ],
            },
            {
                "id": "relations",
                "title": "Relations",
                "intro": "Declared intent. Validators check them; hard ones are bugs when broken, soft ones lower the score.",
                "models": [describe(RelationBase)],
                "groups": [
                    {"title": group, "models": [describe(m, exclude=rel_base) for m in models]}
                    for group, models in RELATION_GROUPS.items()
                ],
            },
            {
                "id": "lowered",
                "title": "Lowered scene",
                "intro": (
                    "What lowering produces from a world file, and what both the validators and the Three.js "
                    "loader read. Everything is flattened into simple items with world-space matrices; each item "
                    "keeps the ID of the IR node it came from."
                ),
                "models": [
                    describe(lowered.LoweredScene),
                    describe(lowered.ItemBase),
                ]
                + [
                    describe(m, exclude=set(lowered.ItemBase.model_fields))
                    for m in (
                        lowered.LAssetItem,
                        lowered.LShape,
                        lowered.LSlab,
                        lowered.LHeightfield,
                        lowered.LMesh,
                        lowered.LInstances,
                        lowered.LLight,
                        lowered.LCamera,
                        lowered.LZone,
                    )
                ]
                + [describe(m) for m in (lowered.LAsset, lowered.LMaterial, lowered.LBehavior, lowered.Unsupported)],
            },
            {
                "id": "actions",
                "title": "Repair actions",
                "intro": "What an agent may change. The v1 repair model only moves, rotates and scales.",
                "groups": [
                    {"title": f"Tier {tier}", "models": [describe(m) for m in models]}
                    for tier, models in ACTIONS_BY_TIER.items()
                ],
            },
        ],
        "vocabularies": {
            "room_types": ROOM_TYPES,
            "archetypes": ARCHETYPES,
            "biomes": BIOMES,
            "style_tags": STYLE_TAGS,
            "functions": FUNCTIONS,
            "oddness_licences": ODDNESS_LICENCES,
        },
    }
