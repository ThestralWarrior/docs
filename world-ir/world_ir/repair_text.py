"""The text a repair model reads: the task, the world in compact form, and the issue list.

Positions are given in each object's parent space, the same space the repair actions
use. Provenance is left out, so a model never sees which objects a bug injector touched.
"""

from typing import Any, Optional

from .nodes import AssetNode, GeneratorNode, GroupNode, PrefabNode, PrimitiveNode, RoomNode
from .validate import ValidationReport, _Validator
from .world import World

INSTRUCTIONS = (
    "Fix the errors with as few and as small edits as possible. "
    "Do not edit locked objects, and leave deliberate oddities and questions for the user alone. "
    'Answer with a JSON list of actions, e.g. [{"action": "move", "id": "lamp", "to": [x, y, z]}]. '
    "Allowed actions: move (to a position in the parent's space), rotate (yaw in degrees), scale (uniform)."
)


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) >= 0.005 else "0"


def world_text(world: World, validator: Optional[_Validator] = None) -> str:
    """One line per room and per object, in the space repair actions use."""
    v = validator or _Validator(world)
    lines = [f"WORLD {world.id}"]
    for room_id, room in v.rooms.items():
        node = room["node"]
        outline = ", ".join(f"[{_num(x)}, {_num(z)}]" for x, z in node.outline)
        doors = [f"{o.type} {o.id} on wall {o.wall}" for o in node.openings]
        lines.append(
            f"room {room_id}: floor y {_num(room['floor'])}, ceiling y {_num(room['ceiling'])}, "
            f"outline [{outline}]" + (f", {', '.join(doors)}" if doors else "")
        )
    lines.append("objects: id | category | parent | pos | yaw | size w×h×d | rests on | notes")
    for node, parent in world.walk():
        if isinstance(node, (AssetNode, PrimitiveNode)):
            lines.append(_object_line(world, v, node, parent))
        elif isinstance(node, (GroupNode, PrefabNode, GeneratorNode)) and not isinstance(node, RoomNode):
            kind = node.generator.gen if isinstance(node, GeneratorNode) else node.kind
            pos = ", ".join(_num(c) for c in node.xform.pos)
            notes = " | locked" if node.locked else ""
            lines.append(f"{node.id} | ({kind}) | {parent.id if parent else '-'} | [{pos}] | {_yaw(node)} | | |{notes}")
    return "\n".join(lines)


def _yaw(node: Any) -> str:
    if node.xform.rot is not None:
        return "tilted " + "/".join(_num(c) for c in node.xform.rot)
    return _num(node.xform.yaw or 0.0)


def _object_line(world: World, v: _Validator, node: Any, parent: Any) -> str:
    if isinstance(node, AssetNode):
        w, h, d = world.assets[node.asset].dims
    else:
        w, h, d = node.size
    s = node.xform.scale
    sx, sy, sz = (s, s, s) if isinstance(s, (int, float)) else s
    size = f"{_num(w * sx)}×{_num(h * sy)}×{_num(d * sz)}"
    solids = v.by_node.get(node.id, [])
    kind, target, surface = v.support_of(solids[0]) if solids else (None, None, None)
    rests = {None: "", "node": f"{target}.{surface}" if surface else str(target)}.get(kind, kind)
    notes = []
    if node.locked:
        notes.append("locked")
    if node.intent:
        notes.append("deliberate: " + ", ".join(c for i in node.intent for c in i.allows))
    pos = ", ".join(_num(c) for c in node.xform.pos)
    category = v._category(node) or ""
    return f"{node.id} | {category} | {parent.id if parent else '-'} | [{pos}] | {_yaw(node)} | {size} | {rests} | {'; '.join(notes)}"


def repair_prompt(world: World, report: Optional[ValidationReport] = None, hints: bool = False) -> str:
    """The full input for one repair step."""
    v = _Validator(world)
    report = report or v.run()
    return f"{INSTRUCTIONS}\n\n{world_text(world, v)}\n\nISSUES\n{report.as_text(hints=hints)}"
