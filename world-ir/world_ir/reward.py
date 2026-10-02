"""Scoring a repair: the reward for training and the numbers for the scoreboard.

The validators are the judge. A repair earns credit for each error it removes and
loses it for each error it adds, for large edits, and for undoing a deliberate
oddity or settling a question that was the user's to answer. Editing a locked
node, or any other invalid output, scores lowest, so a model learns the action
schema and the locks first.
"""

import math
from typing import Any

from pydantic import Field, TypeAdapter, ValidationError

from .actions import Action, ActMove, ActRotate, ActScale, apply_v1
from .common import IRModel, cfg
from .validate import ValidationReport, validate
from .world import World

INVALID = -1.0


class RepairScore(IRModel):
    """What a repair did, measured by the validators."""

    model_config = cfg("v1")

    reward: float = Field(description="Roughly -1 (harmful or invalid) to 1 (clean world, small edits).")
    valid: bool
    reason: str = ""
    errors_before: int = 0
    errors_after: int = 0
    fixed: int = 0
    new: int = 0
    edit_size: float = Field(0.0, description="Metres moved, plus quarter turns, plus doublings of scale.")
    undid_deliberate: int = Field(0, description="Deliberate oddities and user questions that disappeared.")
    clean: bool = False


def _errors(report: ValidationReport) -> set[tuple]:
    return {(i.code, i.node, i.part, i.other, i.relation) for i in report.issues if i.severity == "error"}


def _protected(report: ValidationReport) -> set[tuple]:
    """Deliberate oddities (waived) and open questions: things a repair must leave as they are."""
    return {(i.code, i.node, i.part) for i in report.issues if i.waived_by or i.severity == "ask"}


def score_repair(world: World, actions: Any, before: ValidationReport | None = None) -> RepairScore:
    """Applies actions to a world and scores the result. Never raises on bad model output."""
    before = before or validate(world)
    try:
        parsed = TypeAdapter(list[Action]).validate_python(actions)  # type: ignore[valid-type]
        after_world = apply_v1(world, actions)
    except (ValidationError, ValueError, KeyError, TypeError) as err:
        return RepairScore(reward=INVALID, valid=False, reason=str(err).splitlines()[0][:200])
    after = validate(after_world)
    old, new = _errors(before), _errors(after)
    fixed, added = len(old - new), len(new - old)
    undone = len(_protected(before) - _protected(after))
    edit = 0.0
    for a in parsed:
        node = world.node(a.id)
        if isinstance(a, ActMove):
            edit += math.dist(node.xform.pos, a.to)
        elif isinstance(a, ActRotate):
            edit += abs(((a.yaw - (node.xform.yaw or 0.0)) + 180) % 360 - 180) / 90
        elif isinstance(a, ActScale):
            scale = node.xform.scale if isinstance(node.xform.scale, (int, float)) else 1.0
            edit += abs(math.log2(a.scale / scale))
    total = max(1, len(old))
    reward = (fixed - 1.5 * added) / total - 0.05 * edit - 0.5 * undone + (0.2 if not new and old else 0.0)
    return RepairScore(
        reward=round(max(-1.0, min(1.2, reward)), 4),
        valid=True,
        errors_before=len(old),
        errors_after=len(new),
        fixed=fixed,
        new=added,
        edit_size=round(edit, 3),
        undid_deliberate=undone,
        clean=not new,
    )
