"""Bug injector, repair text and reward: the training data and how repairs are scored."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.inject import Injector, make_tasks  # noqa: E402
from world_ir.repair_text import INSTRUCTIONS, repair_prompt  # noqa: E402
from world_ir.reward import score_repair  # noqa: E402
from world_ir.validate import validate  # noqa: E402


def world(name: str) -> World:
    return World.model_validate_json((ROOT / "examples" / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def tasks():
    return make_tasks(world("bedroom"), 25, seed=5) + make_tasks(world("horror_room"), 15, seed=5)


def test_tasks_are_deterministic():
    a = Injector(world("bedroom"), seed=11).make(bugs=2, decoys=1)
    b = Injector(world("bedroom"), seed=11).make(bugs=2, decoys=1)
    assert a == b


def test_every_planted_bug_is_detected(tasks):
    for task in tasks:
        report = validate(task.world)
        for bug in task.bugs:
            found = {i.code for i in report.issues if i.severity == "error"}
            assert set(bug.codes) <= found, (task.id, bug)


def test_bugs_vary(tasks):
    kinds = {bug.kind for task in tasks for bug in task.bugs}
    assert {"lift", "sink", "push_wall", "collide", "turn", "tilt", "scale"} <= kinds


def test_oracle_fixes_restore_a_clean_world(tasks):
    for task in tasks:
        score = score_repair(task.world, [a for b in task.bugs for a in b.oracle])
        assert score.valid and score.new == 0 and score.fixed == score.errors_before > 0, task.id


def test_decoys_are_deliberate_and_backed_by_the_brief(tasks):
    decoyed = [t for t in tasks if t.decoys]
    assert decoyed
    for task in decoyed:
        report = validate(task.world)
        for node_id in task.decoys:
            waived = [i for i in report.issues if i.node == node_id and i.waived_by]
            assert waived and "floats in mid-air" in task.world.brief.prompt


def test_injected_worlds_do_not_reveal_their_bugs(tasks):
    text = repair_prompt(tasks[0].world)
    assert text.startswith(INSTRUCTIONS) and "ISSUES" in text
    assert "injector" not in text and "suggested:" not in text
    assert "reading_lamp | table lamp | nightstand | [0, 0.5, 0]" in repair_prompt(world("bedroom"))


# Reward ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def task():
    return Injector(world("bedroom"), seed=3).make(bugs=3, decoys=1)


def test_reward_orders_good_and_bad_repairs(task):
    oracle = score_repair(task.world, [a for b in task.bugs for a in b.oracle])
    nothing = score_repair(task.world, [])
    assert oracle.reward > 1.0 > nothing.reward == 0.0


def test_invalid_output_scores_lowest(task):
    assert score_repair(task.world, [{"action": "move", "id": "no_such_node", "to": [0, 0, 0]}]).reward == -1
    assert score_repair(task.world, [{"action": "teleport", "id": "bed"}]).reward == -1
    assert score_repair(task.world, "not even a list").reward == -1


def test_making_things_worse_is_negative(task):
    score = score_repair(task.world, [{"action": "move", "id": "bed", "to": [0.5, 0.0, 0.5]}])
    assert score.new > 0 and score.reward < 0


def test_undoing_a_deliberate_oddity_costs(task):
    decoy = task.decoys[0]
    pos = list(task.world.node(decoy).xform.pos)
    pos[1] = 0.0
    oracle = [a for b in task.bugs for a in b.oracle]
    honest = score_repair(task.world, oracle)
    meddling = score_repair(task.world, oracle + [{"action": "move", "id": decoy, "to": pos}])
    assert meddling.undid_deliberate == 1 and meddling.reward < honest.reward


def test_locked_nodes_cannot_be_edited():
    d = json.loads((ROOT / "examples" / "bedroom.json").read_text())
    d["nodes"][0]["children"][0]["locked"] = True
    locked = World.model_validate(d)
    node = d["nodes"][0]["children"][0]["id"]
    assert score_repair(locked, [{"action": "rotate", "id": node, "yaw": 0}]).reward == -1


def test_outdoor_worlds_make_tasks_too():
    tasks = make_tasks(world("cabin_clearing"), 2, seed=2)
    assert tasks and all(t.bugs for t in tasks)
