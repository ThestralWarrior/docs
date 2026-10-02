"""Validators: they find planted bugs, respect intent, and their suggested fixes work."""

import copy
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.baseline import greedy_repair  # noqa: E402
from world_ir.validate import validate  # noqa: E402

EXAMPLES = ["minimal", "bedroom", "horror_room", "cabin_clearing", "scifi_colony"]


def data(name: str) -> dict:
    return json.loads((ROOT / "examples" / f"{name}.json").read_text())


def find(nodes: list, node_id: str) -> dict:
    for n in nodes:
        if n["id"] == node_id:
            return n
        found = find(n.get("children", []), node_id)
        if found:
            return found
    return None


def codes(report, severity="error") -> list[str]:
    return sorted(i.code for i in report.issues if i.severity == severity)


@pytest.mark.parametrize("name", EXAMPLES)
def test_examples_have_no_errors(name):
    report = validate(World.model_validate(data(name)))
    assert report.clean and report.score == 0, report.as_text()


def test_validation_is_deterministic():
    world = World.model_validate(data("bedroom"))
    assert validate(world) == validate(world)


# Planted bugs ----------------------------------------------------------------------


@pytest.fixture
def broken_bedroom() -> World:
    d = data("bedroom")
    find(d["nodes"], "reading_lamp")["xform"]["pos"][1] += 0.15  # floats above the nightstand
    find(d["nodes"], "desk")["xform"]["pos"][0] -= 0.25  # into the wall
    find(d["nodes"], "desk_chair")["xform"]["yaw"] += 70  # turned away from the desk
    find(d["nodes"], "bed")["xform"]["pos"][1] -= 0.1  # sunk into the floor
    find(d["nodes"], "cabinet")["xform"]["pos"][0] += 0.9  # through the side wall
    return World.model_validate(d)


def test_planted_bugs_are_found(broken_bedroom):
    found = codes(validate(broken_bedroom))
    for code in ("floating", "sunk", "inside_wall", "relation:faces", "relation:on", "relation:against_wall"):
        assert code in found


def test_messages_say_how_far_off(broken_bedroom):
    report = validate(broken_bedroom)
    lamp = next(i for i in report.issues if i.code == "floating")
    assert lamp.node == "reading_lamp" and lamp.amount == pytest.approx(0.15, abs=1e-3)
    assert "0.15 m" in lamp.message and "nightstand.top" in lamp.message


def test_suggested_fixes_repair_the_world(broken_bedroom):
    repaired, actions, report = greedy_repair(broken_bedroom)
    assert report.clean and report.score == 0
    assert {a["action"] for a in actions} == {"move", "rotate"}


def test_collisions_move_the_whole_group_but_turns_move_the_object(broken_bedroom):
    report = validate(broken_bedroom)
    wall = next(i for i in report.issues if i.code == "inside_wall" and i.part == "desk")
    assert wall.node == "work_corner" and wall.fix["id"] == "work_corner"
    turn = next(i for i in report.issues if i.code == "relation:faces")
    assert turn.fix["action"] == "rotate" and turn.fix["id"] == "desk_chair"


def test_hint_text_is_optional(broken_bedroom):
    report = validate(broken_bedroom)
    assert "suggested:" not in report.as_text()
    assert "suggested: move reading_lamp" in report.as_text(hints=True)


# Specific checks -------------------------------------------------------------------


def two_crates(**second) -> dict:
    d = data("minimal")
    crate = copy.deepcopy(d["nodes"][0]["children"][0])
    crate.update(id="crate_2", **second)
    d["nodes"][0]["children"].append(crate)
    return d


def test_overlap_is_found_and_separated():
    world = World.model_validate(two_crates(xform={"pos": [1.6, 0, 1.55], "yaw": 0}))
    report = validate(world)
    assert codes(report) == ["overlap"]
    assert greedy_repair(world)[2].clean


def test_declared_overlap_is_allowed():
    d = two_crates(xform={"pos": [1.6, 0, 1.55], "yaw": 0})
    d["relations"] = [{"rel": "allow_overlap", "a": "crate_1", "b": "crate_2", "max_fraction": 1.0}]
    assert validate(World.model_validate(d)).clean


def test_stacked_crates_do_not_overlap():
    world = World.model_validate(
        two_crates(xform={"pos": [1.5, 0.45, 1.5]}, support={"on": "node", "target": "crate_1"})
    )
    assert validate(world).clean


def test_scale_is_checked_and_fixed():
    d = data("bedroom")
    find(d["nodes"], "nightstand")["xform"]["scale"] = 3.0
    world = World.model_validate(d)
    report = validate(world)
    issue = next(i for i in report.issues if i.code == "scale")
    assert issue.fix == {"action": "scale", "id": "nightstand", "scale": pytest.approx(issue.fix["scale"])}
    assert issue.fix["scale"] < 1.6


def test_door_clearance_is_hard_and_offers_a_way_out():
    d = data("bedroom")
    find(d["nodes"], "cabinet")["xform"]["pos"] = [0.8, 0, 0.5]
    report = validate(World.model_validate(d))
    issue = next(i for i in report.issues if i.code == "door_clearance")
    assert issue.severity == "error" and issue.fix["action"] == "move"


def test_unexplained_floating_needs_a_reason():
    d = data("minimal")
    crate = d["nodes"][0]["children"][0]
    crate["support"] = {"on": "none"}
    crate["xform"]["pos"][1] = 1.2
    assert codes(validate(World.model_validate(d))) == ["floating"]
    crate["semantic"] = {"category": "drone"}
    assert validate(World.model_validate(d)).clean


def test_unsupported_object_is_a_question_in_an_odd_brief():
    d = data("minimal")
    d["brief"] = {"prompt": "A surreal storage room.", "mood": ["surreal"]}
    crate = d["nodes"][0]["children"][0]
    crate["support"] = {"on": "none"}
    report = validate(World.model_validate(d))
    assert codes(report, "ask") == ["floating"] and report.clean


def test_locked_nodes_are_reported_but_not_repaired():
    d = data("minimal")
    crate = d["nodes"][0]["children"][0]
    crate["xform"]["pos"][1] = 0.3
    crate["locked"] = True
    world = World.model_validate(d)
    issue = validate(world).issues[0]
    assert issue.code == "floating" and not issue.fixable
    _, actions, _ = greedy_repair(world)
    assert actions == []


def test_out_of_bounds_on_terrain():
    d = data("cabin_clearing")
    find(d["nodes"], "tent")["xform"]["pos"] = [80, 0.5, 0]
    assert "out_of_bounds" in codes(validate(World.model_validate(d)))


def test_prefab_parts_point_at_their_instance():
    d = data("cabin_clearing")
    find(d["nodes"], "picnic_2")["xform"]["pos"] = [-6.0, 0.5, -1.2]  # on top of the tent
    report = validate(World.model_validate(d))
    overlaps = [i for i in report.issues if i.code == "overlap"]
    assert overlaps and all(i.node in ("picnic_2", "tent") for i in overlaps)
    assert any(i.part and i.part.startswith("picnic_2/") for i in overlaps if i.node == "picnic_2") or any(
        i.other == "picnic_2" for i in overlaps
    )


def test_hidden_parts_are_ignored():
    report = validate(World.model_validate(data("cabin_clearing")))
    assert not any("bench_s2" in (i.part or "") for i in report.issues)


def test_curved_beam_is_not_judged_by_its_bounding_box():
    report = validate(World.model_validate(data("scifi_colony")))
    assert not any(i.relation == "r_monorail_hub" for i in report.issues)


def test_requirements_count_generated_objects():
    d = data("cabin_clearing")
    d.setdefault("rules", {})["checks"] = ["requirements"]
    assert validate(World.model_validate(d)).clean


# Intent ----------------------------------------------------------------------------


def test_deliberate_oddities_are_left_alone():
    report = validate(World.model_validate(data("horror_room")))
    waived = {i.node for i in report.issues if i.waived_by}
    assert {"ceiling_chair", "nightstand", "fallen_box", "floating_lamp"} <= waived
    assert all(i.fix is None for i in report.issues if i.waived_by)
    assert "Deliberate, leave alone" in report.as_text()


def test_hard_checks_on_requested_oddities_become_questions():
    report = validate(World.model_validate(data("horror_room")))
    assert [(i.code, i.node) for i in report.issues if i.severity == "ask"] == [("door_clearance", "bed")]


def test_near_misses_are_still_mistakes():
    d = data("horror_room")
    find(d["nodes"], "floating_lamp")["xform"]["rot"] = [8, 0, -5]
    report = validate(World.model_validate(d))
    issue = next(i for i in report.issues if i.code == "upright" and i.node == "floating_lamp")
    assert issue.severity == "error" and "near miss" not in issue.message and "mistake" in issue.message


def test_without_intent_the_ceiling_chair_is_a_bug():
    d = data("horror_room")
    find(d["nodes"], "ceiling_chair")["intent"] = []
    report = validate(World.model_validate(d))
    assert any(i.code == "upright" and i.node == "ceiling_chair" and i.severity == "error" for i in report.issues)


def test_ceiling_support_is_still_checked():
    d = data("horror_room")
    find(d["nodes"], "ceiling_chair")["xform"]["pos"][1] = 2.3  # hangs 30 cm low
    report = validate(World.model_validate(d))
    issue = next(i for i in report.issues if i.node == "ceiling_chair" and i.severity == "error")
    assert issue.code == "floating" and "ceiling" in issue.message
    assert issue.fix["to"][1] == pytest.approx(2.6, abs=0.01)  # moved up to the ceiling, not down to the floor
