import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World, apply_v1  # noqa: E402
from world_ir.lower import apply, column_major, kelvin_to_hex, lower, rot_y  # noqa: E402
from world_ir.lowered import LOWERING_VERSION, THREE_VERSION, LoweredScene  # noqa: E402


def world(name: str) -> World:
    return World.model_validate_json((ROOT / "examples" / name).read_text())


def items(scene: LoweredScene) -> dict:
    return {item.id: item for item in scene.items}


def pos(item) -> tuple:
    return tuple(round(v, 4) for v in item.matrix[12:15])


@pytest.fixture(scope="module")
def bedroom() -> LoweredScene:
    return lower(world("bedroom.json"))


def test_lowering_is_deterministic_and_committed_scenes_are_fresh():
    for name in ("bedroom", "minimal", "cabin_clearing", "scifi_colony"):
        scene = lower(world(f"{name}.json"))
        assert scene == lower(world(f"{name}.json"))
        committed = json.loads((ROOT / "viewer" / "scenes" / f"{name}.json").read_text())
        assert committed == json.loads(
            scene.model_dump_json(exclude_none=True)
        ), f"viewer/scenes/{name}.json is stale: run scripts/lower.py examples/*.json --out-dir viewer/scenes"


def test_versions_are_recorded(bedroom):
    assert bedroom.versions == {"ir": "1.0", "lowering": LOWERING_VERSION, "three": THREE_VERSION}


def test_item_ids_are_unique_and_point_back_to_real_nodes(bedroom):
    w = world("bedroom.json")
    ids = [item.id for item in bedroom.items]
    assert len(ids) == len(set(ids))
    node_ids = {node.id for node, _ in w.walk()}
    assert {item.node for item in bedroom.items} <= node_ids


def test_children_inherit_parent_transforms(bedroom):
    found = items(bedroom)
    assert pos(found["reading_lamp"]) == (3.97, 0.5, 3.11)
    assert pos(found["reading_lamp_bulb"]) == (3.97, 0.95, 3.11)


def test_asset_boxes_use_rotated_dims(bedroom):
    lo, hi = items(bedroom)["bed"].aabb
    # Yaw 270 turns the 2.137 m length along x and the 1.816 m width along z.
    assert hi[0] - lo[0] == pytest.approx(2.137, abs=1e-3)
    assert hi[2] - lo[2] == pytest.approx(1.816, abs=1e-3)
    assert lo[1] == 0 and hi[1] == pytest.approx(0.712)


def test_walls_are_cut_around_openings(bedroom):
    found = items(bedroom)
    wall0 = sorted(
        (i for i in bedroom.items if i.id.startswith("bedroom/wall0/") and i.role == "wall"), key=lambda i: i.id
    )
    assert [i.id for i in wall0] == ["bedroom/wall0/0_above", "bedroom/wall0/0_before", "bedroom/wall0/end"]
    before, above = found["bedroom/wall0/0_before"], found["bedroom/wall0/0_above"]
    assert before.size[0] == pytest.approx(0.35 + 0.12)  # from the corner overlap to the door
    assert above.size[1] == pytest.approx(2.6 - 2.1)  # lintel above a 2.1 m door
    window_wall = {i.id.split("/")[-1] for i in bedroom.items if i.id.startswith("bedroom/wall2/")}
    assert {"0_before", "0_above", "0_below", "w1", "end"} == window_wall
    assert pos(found["bedroom/wall2/w1"]) == (2.1, 0.9, 3.66)


def test_walls_sit_outside_the_outline_and_face_out(bedroom):
    found = items(bedroom)
    assert pos(found["bedroom/wall0/end"])[2] == pytest.approx(-0.06)
    assert found["bedroom/wall0/end"].facing == (0.0, -1.0)
    assert found["bedroom/wall1/end"].facing == (1.0, 0.0)


def test_door_clearance_zone_is_inside_the_room(bedroom):
    zone = items(bedroom)["bedroom/wall0/d1/clearance"]
    xs, zs = [p[0] for p in zone.polygon], [p[1] for p in zone.polygon]
    assert (min(xs), max(xs)) == (0.35, 1.25)
    assert (min(zs), max(zs)) == (0.0, 0.9)


def test_open_door_swings_into_the_room():
    data = json.loads((ROOT / "examples" / "bedroom.json").read_text())
    data["nodes"][0]["openings"][0]["open"] = 1.0
    door = items(lower(World.model_validate(data)))["bedroom/wall0/d1"]
    assert pos(door)[2] > 0.3, "fully open, the panel stands inside the room (z > 0)"


def test_repair_moves_only_the_edited_items(bedroom):
    moved = lower(apply_v1(world("bedroom.json"), [{"action": "move", "id": "desk_chair", "to": [1.0, 0, 2.6]}]))
    changed = {a.id for a, b in zip(bedroom.items, moved.items) if a.matrix != b.matrix}
    assert changed == {"desk_chair"}


def test_moving_a_parent_moves_its_children(bedroom):
    moved = lower(apply_v1(world("bedroom.json"), [{"action": "move", "id": "nightstand", "to": [3.97, 0, 2.0]}]))
    changed = {a.id for a, b in zip(bedroom.items, moved.items) if a.matrix != b.matrix}
    assert changed == {"nightstand", "reading_lamp", "reading_lamp_bulb"}


def test_presets_are_resolved(bedroom):
    floor = bedroom.materials["oak_floor"]
    assert floor.base_color == "#a87a4f" and floor.roughness == 0.5  # explicit values beat the preset
    assert bedroom.materials["_glass_clear"].transmission == 1.0


def test_unsupported_nodes_are_listed_and_their_children_still_lowered():
    data = json.loads((ROOT / "examples" / "bedroom.json").read_text())
    data["nodes"][0]["children"].append(
        {
            "kind": "audio",
            "id": "radio",
            "uri": "radio.ogg",
            "children": [{"kind": "primitive", "id": "radio_box", "shape": "box", "size": [0.3, 0.2, 0.15]}],
        }
    )
    scene = lower(World.model_validate(data))
    assert [(u.node, u.kind) for u in scene.unsupported] == [("radio", "audio")]
    assert any(item.node == "radio_box" for item in scene.items), "its child is still lowered"


def test_yaw_convention_matches_the_ir():
    x, _, z = apply(rot_y(90), (0.0, 0.0, 1.0))
    assert (round(x, 6), round(z, 6)) == (1.0, 0.0), "positive yaw turns +Z toward +X"
    assert column_major(rot_y(0))[0] == 1.0


@pytest.mark.parametrize("kelvin, expected", [(6600, "#ffffff"), (2700, "#ffa757"), (1900, "#ff8300")])
def test_kelvin_colours(kelvin, expected):
    got = kelvin_to_hex(kelvin)
    assert all(math.isclose(int(got[i : i + 2], 16), int(expected[i : i + 2], 16), abs_tol=3) for i in (1, 3, 5))
