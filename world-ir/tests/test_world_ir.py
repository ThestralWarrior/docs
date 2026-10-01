import copy
import json
import pathlib
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import Action, World, apply_v1, constrained_schema  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


def load(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text())


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_validate_and_round_trip(path):
    world = World.model_validate_json(path.read_text())
    again = World.model_validate(world.model_dump(by_alias=True))
    assert again == world


def test_generated_files_are_up_to_date():
    schema = json.loads((ROOT / "schema" / "world.schema.json").read_text())
    assert schema == json.loads(
        json.dumps(World.model_json_schema(by_alias=True))
    ), "schema/world.schema.json is stale: run scripts/build_docs.py"
    actions = json.loads((ROOT / "schema" / "actions.schema.json").read_text())
    assert actions == json.loads(json.dumps(TypeAdapter(list[Action]).json_schema(by_alias=True)))


def broken(mutate) -> dict:
    data = load("bedroom.json")
    mutate(data)
    return data


def room(data: dict) -> dict:
    return data["nodes"][0]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: room(d)["children"].append(copy.deepcopy(room(d)["children"][0])), "duplicate node id 'bed'"),
        (lambda d: room(d)["children"][0].update(asset="no_such_asset"), "unknown asset 'no_such_asset'"),
        (lambda d: d["relations"][0].update(wall="bedroom:9"), "has no wall 9"),
        (lambda d: d["relations"][0].update(wall="kitchen:0"), "no room 'kitchen'"),
        (lambda d: d["relations"][3].update(b="ghost"), "unknown node 'ghost'"),
        (lambda d: d["relations"][9].update(opening="d9"), "unknown opening 'd9'"),
        (lambda d: room(d)["children"][0].update(id="Bed"), "should match pattern"),
        (lambda d: room(d)["openings"][0].update(offset=4.0), "does not fit on wall 0"),
        (lambda d: room(d)["children"][0]["xform"].update(rot=[0, 90, 0]), "at most one of yaw, rot, quat"),
        (lambda d: room(d)["children"][0].update(colour="#ffffff"), "Extra inputs are not permitted"),
        (lambda d: d["relations"].append({"rel": "levitates", "a": "bed"}), "does not match any of the expected tags"),
        (lambda d: room(d)["children"][0]["materials"][0].update(material="gold"), "unknown material 'gold'"),
        (lambda d: room(d)["children"][0].update(support={"on": "node"}), "needs a target"),
    ],
)
def test_bad_worlds_are_rejected(mutate, message):
    with pytest.raises(ValidationError) as err:
        World.model_validate(broken(mutate))
    assert message in str(err.value)


def test_scatter_needs_one_area_and_one_amount():
    data = load("cabin_clearing.json")
    forest = next(n for n in data["nodes"] if n["id"] == "forest")
    forest["count"] = 50
    with pytest.raises(ValidationError, match="exactly one of density or count"):
        World.model_validate(data)


def test_prefab_node_ids_are_local():
    world = World.model_validate(load("cabin_clearing.json"))
    with pytest.raises(KeyError):
        world.node("bench_n")  # lives inside the prefab, not the scene tree


def test_constrained_schema_lists_only_real_unlocked_nodes():
    data = load("bedroom.json")
    room(data)["children"][3]["locked"] = True  # wardrobe
    world = World.model_validate(data)
    schema = constrained_schema(world, "v1")
    move = schema["$defs"]["ActMove"]["properties"]
    assert "bed" in move["id"]["enum"]
    assert "wardrobe" not in move["id"]["enum"]
    assert move["to"]["items"] == {"type": "number"}
    assert set(schema["$defs"]) == {"ActMove", "ActRotate", "ActScale"}
    assert schema["maxItems"] == 12


def test_apply_v1_moves_rotates_and_scales():
    world = World.model_validate(load("bedroom.json"))
    fixed = apply_v1(
        world,
        [
            {"action": "move", "id": "desk_chair", "to": [0.9, 0, 2.4]},
            {"action": "rotate", "id": "bed", "yaw": 90},
            {"action": "scale", "id": "reading_lamp", "scale": 1.2},
        ],
    )
    assert fixed.node("desk_chair").xform.pos == (0.9, 0, 2.4)
    assert fixed.node("bed").xform.yaw == 90
    assert fixed.node("reading_lamp").xform.scale == 1.2
    assert world.node("desk_chair").xform.pos == (0.82, 0, 2.4), "the original world is unchanged"


def test_apply_v1_refuses_locked_nodes_and_later_actions():
    data = load("bedroom.json")
    room(data)["children"][0]["locked"] = True
    world = World.model_validate(data)
    with pytest.raises(ValueError, match="locked"):
        apply_v1(world, [{"action": "move", "id": "bed", "to": [1, 0, 1]}])
    with pytest.raises(ValueError, match="not a v1 action"):
        apply_v1(world, [{"action": "remove_node", "id": "rug"}])
