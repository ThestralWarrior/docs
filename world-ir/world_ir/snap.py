"""Snapping: set the height of terrain-supported nodes from the terrain itself.

An agent can say where something stands (x, z) and that it rests on the terrain,
but it cannot know the ground height there. This pass fills that in, the same
way code fills in asset sizes. It is a World → World step, run before lowering.
"""

from typing import Any

from .geometry import Mat, apply, identity, mul, xform_matrix
from .lower import _Lowerer
from .world import World


def snap_to_ground(world: World) -> tuple[World, list[str]]:
    """Moves every node whose support is 'terrain' so its base sits on the ground under its origin.

    Parents are snapped before children, so a child supported by terrain under a moved
    parent is measured from the parent's new position. Returns the new world and a
    list of the nodes that moved, with how far.
    """
    lowerer = _Lowerer(world)
    data = world.model_copy(deep=True)
    moved: list[str] = []

    def visit(node: Any, parent_world: Mat) -> None:
        m = mul(parent_world, xform_matrix(node.xform))
        if node.support is not None and node.support.on == "terrain":
            wx, wy, wz = apply(m, (0.0, 0.0, 0.0))
            ground = lowerer.ground_height(wx, wz)
            if ground is not None and abs(ground - wy) > 1e-4:
                delta = (ground - wy) / (parent_world[1][1] or 1.0)
                x, y, z = node.xform.pos
                node.xform.pos = (x, round(y + delta, 4), z)
                moved.append(f"{node.id}: {ground - wy:+.3f} m")
                m = mul(parent_world, xform_matrix(node.xform))
        for child in node.children:
            visit(child, m)

    for root in data.nodes:
        visit(root, identity())
    return World.model_validate(data.model_dump(by_alias=True)), moved
