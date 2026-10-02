"""A rule-based repairer: applies the validators' own suggested fixes, one round at a time.

It is the baseline the trained repair model has to beat, and the oracle that turns
planted bugs into worked examples. It only knows move, rotate and scale, and it
never touches anything locked, waived or put to the user.
"""

from typing import Any

from .actions import apply_v1
from .validate import ValidationReport, validate
from .world import World


def greedy_repair(world: World, rounds: int = 5) -> tuple[World, list[dict[str, Any]], ValidationReport]:
    """Applies suggested fixes until the world is clean, nothing changes, or the rounds run out.

    A round applies at most one fix per node. A round that makes the score worse is undone,
    and the loop stops. Returns the repaired world, the actions applied, and its final report.
    """
    report = validate(world)
    history: list[dict[str, Any]] = []
    for _ in range(rounds):
        actions, seen = [], set()
        for issue in report.issues:
            if issue.severity != "error" or not issue.fix or not issue.fixable or issue.fix["id"] in seen:
                continue
            seen.add(issue.fix["id"])
            actions.append(issue.fix)
        if not actions:
            break
        candidate = apply_v1(world, actions)
        candidate_report = validate(candidate)
        if candidate_report.score >= report.score:
            break
        world, report = candidate, candidate_report
        history += actions
    return world, history, report
