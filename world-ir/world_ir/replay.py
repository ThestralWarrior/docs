"""Replays: a repair run recorded step by step, for the viewer to play back.

Each step applies one action, lowers the world again and stores only the scene
patch, the issue it addressed and the issues left after it. The viewer animates
the patches in order, so you watch the world heal one fix at a time.
"""

from typing import Any, Callable, Literal, Optional

from pydantic import Field

from .actions import apply_v1
from .common import IRModel, cfg
from .lower import lower
from .lowered import LoweredScene
from .patch import ScenePatch, diff_scenes
from .validate import Issue, ValidationReport, validate
from .world import World


class ReplayStep(IRModel):
    """One applied action and what it changed."""

    model_config = cfg("v1")

    actions: list[dict[str, Any]]
    issue: Optional[Issue] = Field(None, description="The issue this step set out to fix.")
    label: str = Field(description="Short caption shown at the object.")
    resolved: list[str] = Field(default_factory=list, description="Messages of the errors this step removed.")
    patch: ScenePatch
    issues: list[Issue] = Field(description="Issues after the step (errors, questions and deliberate ones).")
    score: float


class Replay(IRModel):
    """A repair run the viewer can play: the starting scene and each step after it."""

    model_config = cfg("v1")

    format: Literal["replay-1"] = "replay-1"
    world_id: str
    title: str
    policy: str = Field(description="Who repaired it, e.g. 'rule-based' or a model name.")
    start: LoweredScene
    start_issues: list[Issue]
    start_score: float
    steps: list[ReplayStep] = Field(default_factory=list)


def _keys(report: ValidationReport) -> dict[tuple, Issue]:
    return {(i.code, i.node, i.part, i.other, i.relation): i for i in report.issues if i.severity == "error"}


def _caption(issue: Issue, action: dict[str, Any]) -> str:
    what = issue.message.split(" (")[0].rstrip(".")
    if action["action"] == "move":
        verb = "moved back into place" if issue.code in ("floating", "sunk") else "moved clear"
    elif action["action"] == "rotate":
        verb = f"turned to {action['yaw']:.0f}°"
    else:
        verb = f"scaled to {action['scale']:.2f}"
    return f"{what} → {verb}"


def _shown(report: ValidationReport) -> list[Issue]:
    return [i.model_copy(update={"fix": None}) for i in report.issues if i.severity != "warning" or i.relation]


Policy = Callable[[World, ValidationReport], list[tuple[Optional[Issue], list[dict[str, Any]]]]]


def suggested_fixes(world: World, report: ValidationReport) -> list[tuple[Optional[Issue], list[dict[str, Any]]]]:
    """The rule-based policy: each fixable error's suggested action, one at a time."""
    seen, out = set(), []
    for issue in report.issues:
        if issue.severity == "error" and issue.fix and issue.fixable and issue.fix["id"] not in seen:
            seen.add(issue.fix["id"])
            out.append((issue, [issue.fix]))
    return out


def make_replay(
    world: World, title: str, policy: Policy = suggested_fixes, name: str = "rule-based", max_steps: int = 30
) -> Replay:
    """Repairs a world one action at a time and records every step.

    A step that does not improve the score is skipped and its issue is not retried.
    """
    report = validate(world)
    scene = lower(world)
    replay = Replay(
        world_id=world.id, title=title, policy=name, start=scene, start_issues=_shown(report), start_score=report.score
    )
    tried: set[tuple] = set()
    while len(replay.steps) < max_steps:
        options = [
            (i, a) for i, a in policy(world, report) if i is not None and (i.code, i.node, i.relation) not in tried
        ]
        if not options:
            break
        issue, actions = options[0]
        tried.add((issue.code, issue.node, issue.relation))
        candidate = apply_v1(world, actions)
        new_report = validate(candidate)
        if new_report.score >= report.score:
            continue
        new_scene = lower(candidate)
        before_keys, after_keys = _keys(report), _keys(new_report)
        replay.steps.append(
            ReplayStep(
                actions=actions,
                issue=issue.model_copy(update={"fix": None}),
                label=_caption(issue, actions[0]),
                resolved=[before_keys[k].message for k in before_keys if k not in after_keys],
                patch=diff_scenes(scene, new_scene),
                issues=_shown(new_report),
                score=new_report.score,
            )
        )
        world, report, scene = candidate, new_report, new_scene
        tried = {t for t in tried if any((i.code, i.node, i.relation) == t for i in report.issues)}
    return replay
