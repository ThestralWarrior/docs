"""Edits an agent may make to a world. The repair model outputs a list of these.

The world can describe far more than the repair model may change. Version 1 of
the repair model only moves, rotates and scales; later tiers add more.
"""

import copy
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import Field, TypeAdapter

from .assets import MaterialSlotOverride
from .common import Id, IRModel, Support, Vec3, cfg, ref
from .nodes import Node
from .relations import Relation
from .world import World


class ActMove(IRModel):
    """Puts a node at a new position, in its parent's space."""

    model_config = cfg("v1")
    action: Literal["move"] = "move"
    id: Id = Field(json_schema_extra=ref("node"))
    to: Vec3


class ActRotate(IRModel):
    """Sets a node's yaw in degrees."""

    model_config = cfg("v1")
    action: Literal["rotate"] = "rotate"
    id: Id = Field(json_schema_extra=ref("node"))
    yaw: float


class ActScale(IRModel):
    """Sets a node's uniform scale."""

    model_config = cfg("v1")
    action: Literal["scale"] = "scale"
    id: Id = Field(json_schema_extra=ref("node"))
    scale: float = Field(gt=0.05, lt=20)


class ActSetSupport(IRModel):
    """Changes what a node rests on."""

    model_config = cfg("v2")
    action: Literal["set_support"] = "set_support"
    id: Id = Field(json_schema_extra=ref("node"))
    support: Support


class ActReparent(IRModel):
    """Moves a node under another parent, keeping its place in the world."""

    model_config = cfg("v2")
    action: Literal["reparent"] = "reparent"
    id: Id = Field(json_schema_extra=ref("node"))
    parent: Optional[Id] = Field(None, description="Empty moves it to the top level.", json_schema_extra=ref("node"))


class ActSwapAsset(IRModel):
    """Replaces a node's asset, e.g. a smaller sofa that fits."""

    model_config = cfg("v2")
    action: Literal["swap_asset"] = "swap_asset"
    id: Id = Field(json_schema_extra=ref("node"))
    asset: Id = Field(json_schema_extra=ref("asset"))


class ActSetMaterial(IRModel):
    """Overrides one material slot on a node."""

    model_config = cfg("v2")
    action: Literal["set_material"] = "set_material"
    id: Id = Field(json_schema_extra=ref("node"))
    override: MaterialSlotOverride


class ActSetParam(IRModel):
    """Changes one parameter of a generator, room, terrain or other node, by dotted path."""

    model_config = cfg("v2")
    action: Literal["set_param"] = "set_param"
    id: Id = Field(json_schema_extra=ref("node"))
    path: str = Field(description="e.g. 'generator.steps', 'height', 'modifiers.0.amount'.")
    value: Union[float, int, str, bool, list[float]]


class ActAddNode(IRModel):
    """Adds a node under a parent, or at the top level."""

    model_config = cfg("v2")
    action: Literal["add_node"] = "add_node"
    parent: Optional[Id] = Field(None, json_schema_extra=ref("node"))
    node: Node


class ActRemoveNode(IRModel):
    """Removes a node and its children. Not offered to the v1 repair model."""

    model_config = cfg("v2")
    action: Literal["remove_node"] = "remove_node"
    id: Id = Field(json_schema_extra=ref("node"))


class ActAddRelation(IRModel):
    """Declares a new relation."""

    model_config = cfg("later")
    action: Literal["add_relation"] = "add_relation"
    relation: Relation


class ActRemoveRelation(IRModel):
    """Removes a relation by its ID."""

    model_config = cfg("later")
    action: Literal["remove_relation"] = "remove_relation"
    relation_id: Id


ACTIONS_BY_TIER: dict[str, list[type[IRModel]]] = {
    "v1": [ActMove, ActRotate, ActScale],
    "v2": [ActSetSupport, ActReparent, ActSwapAsset, ActSetMaterial, ActSetParam, ActAddNode, ActRemoveNode],
    "later": [ActAddRelation, ActRemoveRelation],
}
ALL_ACTIONS: list[type[IRModel]] = [cls for group in ACTIONS_BY_TIER.values() for cls in group]

Action = Annotated[Union[tuple(ALL_ACTIONS)], Field(discriminator="action")]  # type: ignore[valid-type]


def allowed_actions(tier: str) -> list[type[IRModel]]:
    """Actions available at a tier, including every earlier tier."""
    order = ["v1", "v2", "later"]
    return [cls for t in order[: order.index(tier) + 1] for cls in ACTIONS_BY_TIER[t]]


def constrained_schema(world: World, tier: str = "v1", max_actions: int = 12) -> dict[str, Any]:
    """JSON Schema for one repair answer on this world.

    Node IDs are enums of the world's unlocked nodes, so a model decoding with
    this schema (vLLM structured outputs, Fireworks JSON mode) cannot name an
    object that does not exist. Tuples become plain arrays for wider support.
    """
    classes = allowed_actions(tier)
    union = Annotated[Union[tuple(classes)], Field(discriminator="action")]  # type: ignore[valid-type]
    schema = TypeAdapter(list[union]).json_schema()  # type: ignore[valid-type]
    editable = sorted(node.id for node, _ in world.walk() if not node.locked)
    names = {cls.__name__ for cls in classes}
    for name, definition in schema.get("$defs", {}).items():
        if name in names and "id" in definition.get("properties", {}):
            definition["properties"]["id"] = {"type": "string", "enum": editable}
    schema["maxItems"] = max_actions
    return _plain_tuples(schema)


def _plain_tuples(schema: Any) -> Any:
    if isinstance(schema, dict):
        if "prefixItems" in schema:
            items = schema.pop("prefixItems")
            schema["items"] = items[0] if items else {}
        return {key: _plain_tuples(value) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_plain_tuples(item) for item in schema]
    return schema


def apply_v1(world: World, actions: list[Any]) -> World:
    """Applies v1 actions (move, rotate, scale) and returns a new, re-validated world.

    Raises ValueError for unknown or locked nodes and for actions above v1.
    """
    parsed = TypeAdapter(list[Action]).validate_python(actions)  # type: ignore[valid-type]
    data = copy.deepcopy(world)
    for act in parsed:
        if not isinstance(act, (ActMove, ActRotate, ActScale)):
            raise ValueError(f"action '{act.action}' is not a v1 action")
        node = data.node(act.id)
        if node.locked:
            raise ValueError(f"node '{act.id}' is locked")
        xform = node.xform
        if isinstance(act, ActMove):
            xform.pos = act.to
        elif isinstance(act, ActRotate):
            xform.yaw, xform.rot, xform.quat = act.yaw, None, None
        else:
            xform.scale = act.scale
    return World.model_validate(data.model_dump(by_alias=True))
