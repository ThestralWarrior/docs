"""Scene patches and replays: what the viewer animates instead of reloading."""

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from world_ir import World  # noqa: E402
from world_ir.actions import apply_v1  # noqa: E402
from world_ir.lower import lower  # noqa: E402
from world_ir.patch import apply_patch, diff_scenes  # noqa: E402
from world_ir.replay import Replay, make_replay  # noqa: E402

from replay import demo_worlds  # noqa: E402


def world(name: str) -> World:
    return World.model_validate_json((ROOT / "examples" / f"{name}.json").read_text())


def as_dict(scene) -> dict:
    data = json.loads(scene.model_dump_json(exclude_none=True))
    data["items"] = sorted(data["items"], key=lambda i: i["id"])
    return data


def test_a_small_edit_makes_a_small_patch():
    before = world("bedroom")
    after = apply_v1(before, [{"action": "move", "id": "bed", "to": [3.0, 0.0, 1.9]}])
    patch = diff_scenes(lower(before), lower(after))
    assert not patch.added and not patch.removed and not patch.reload
    assert {i.id for i in patch.changed} <= {"bed", "hero_cam"} and "bed" in {i.id for i in patch.changed}


def test_patches_rebuild_the_scene_exactly():
    before = world("cabin_clearing")
    after = apply_v1(
        before, [{"action": "scale", "id": "tent", "scale": 1.5}, {"action": "rotate", "id": "fire_bench", "yaw": 10}]
    )
    a, b = lower(before), lower(after)
    assert as_dict(apply_patch(a, diff_scenes(a, b))) == as_dict(b)


def test_added_and_removed_items_are_listed():
    a = lower(world("bedroom"))
    b = a.model_copy(update={"items": a.items[1:] + [a.items[0].model_copy(update={"id": "bedroom/floor_copy"})]})
    patch = diff_scenes(a, b)
    assert patch.removed == [a.items[0].id] and [i.id for i in patch.added] == ["bedroom/floor_copy"]


def test_environment_changes_ask_for_a_reload():
    a = lower(world("bedroom"))
    env = a.environment.model_copy(update={"exposure": 2.0})
    assert diff_scenes(a, a.model_copy(update={"environment": env})).reload


@pytest.fixture(scope="module")
def demos():
    return {name: make_replay(w, title) for name, (title, _, w) in demo_worlds().items()}


def test_demo_replays_heal_their_worlds(demos):
    for name, replay in demos.items():
        assert replay.steps, name
        scores = [replay.start_score] + [s.score for s in replay.steps]
        assert scores == sorted(scores, reverse=True) and scores[-1] == 0, name


def test_replay_steps_are_one_action_each_and_small(demos):
    for replay in demos.values():
        for step in replay.steps:
            assert len(step.actions) == 1 and step.resolved
            assert len(step.patch.changed) + len(step.patch.added) + len(step.patch.removed) <= 4


def test_horror_replay_keeps_the_horror(demos):
    replay = demos["horror_repair"]
    touched = {a["id"] for s in replay.steps for a in s.actions}
    assert touched == {"ceiling_chair", "fallen_box"}
    final = replay.steps[-1].issues
    assert {i.node for i in final if i.waived_by} >= {"ceiling_chair", "floating_lamp", "nightstand", "fallen_box"}
    assert [i.node for i in final if i.severity == "ask"] == ["bed"]


def test_committed_replays_are_fresh(demos):
    for name, replay in demos.items():
        committed = Replay.model_validate_json((ROOT / "viewer" / "replays" / f"{name}.json").read_text())
        assert json.loads(committed.model_dump_json(exclude_none=True)) == json.loads(
            replay.model_dump_json(exclude_none=True)
        ), f"run scripts/replay.py --demo ({name} is stale)"
