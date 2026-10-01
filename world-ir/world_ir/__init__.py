"""World IR: the single source of truth for a generated 3D world.

Import ``World`` to load and validate a world file::

    from world_ir import World
    world = World.model_validate_json(open("examples/bedroom.json").read())
"""

from .actions import ACTIONS_BY_TIER, Action, apply_v1, constrained_schema
from .assets import MATERIAL_PRESETS, AssetDef, Material
from .generators import GENERATOR_GROUPS, Generator
from .nodes import NODE_KINDS, Node
from .relations import RELATION_GROUPS, Relation
from .vocab import ARCHETYPES, BIOMES, FUNCTIONS, ODDNESS_LICENCES, ROOM_TYPES, STYLE_TAGS
from .world import IR_VERSION, World, intent_changes

__all__ = [
    "ACTIONS_BY_TIER",
    "ARCHETYPES",
    "Action",
    "AssetDef",
    "BIOMES",
    "FUNCTIONS",
    "GENERATOR_GROUPS",
    "Generator",
    "IR_VERSION",
    "MATERIAL_PRESETS",
    "Material",
    "NODE_KINDS",
    "Node",
    "ODDNESS_LICENCES",
    "RELATION_GROUPS",
    "ROOM_TYPES",
    "Relation",
    "STYLE_TAGS",
    "World",
    "apply_v1",
    "constrained_schema",
    "intent_changes",
]
