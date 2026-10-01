"""Tests for what the sci-fi colony added: sweeps, domes, space skies, bloom, liquid terrain and behaviours."""

import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.geometry import dome_mesh, roof_mesh, simplify_line, sweep_mesh, circle_profile  # noqa: E402
from world_ir.lower import _Lowerer, lower  # noqa: E402
from world_ir.snap import snap_to_ground  # noqa: E402


def colony_data() -> dict:
    return json.loads((ROOT / "examples" / "scifi_colony.json").read_text())


@pytest.fixture(scope="module")
def colony() -> World:
    return World.model_validate(colony_data())


@pytest.fixture(scope="module")
def scene(colony):
    return lower(colony)


@pytest.fixture(scope="module")
def lowerer(colony):
    return _Lowerer(colony)


def items(scene, **match):
    return [i for i in scene.items if all(getattr(i, k) == v for k, v in match.items())]


def signed_volume(positions, indices):
    """Positive when every triangle winds counter-clockwise seen from outside."""
    v = [positions[i : i + 3] for i in range(0, len(positions), 3)]
    total = 0.0
    for k in range(0, len(indices), 3):
        a, b, c = (v[i] for i in indices[k : k + 3])
        total += (
            a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    return total / 6


def small_world(nodes, **extra):
    return World.model_validate(
        {
            "ir_version": "1.0",
            "id": "t",
            "materials": {"neon": {"preset": "neon_cyan"}, "lava": {"preset": "lava"}},
            "nodes": nodes,
            **extra,
        }
    )


def terrain(**extra):
    return {
        "kind": "terrain",
        "id": "ground",
        "size": [40, 40],
        "resolution": 41,
        "height": {"source": "noise", "amplitude": 3, "seed": 2},
        **extra,
    }


# Geometry -------------------------------------------------------------------------


def test_dome_is_a_closed_outward_solid_of_the_right_size():
    positions, indices = dome_mesh(4.0, 3.0, 2.5, 24, 8)
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    assert max(xs) == pytest.approx(4.0, rel=0.02) and max(zs) == pytest.approx(3.0, rel=0.02)
    assert min(ys) == 0 and max(ys) == pytest.approx(2.5)
    # Half an ellipsoid: (2/3)·π·rx·rz·h, less a little for the facets.
    assert signed_volume(positions, indices) == pytest.approx(2 / 3 * math.pi * 4 * 3 * 2.5, rel=0.04)


def test_dome_roof_is_built_not_replaced_by_a_gable():
    (positions, indices), built = roof_mesh("dome", 6.0, 6.0, 45.0, 0.2)
    assert built == "dome"
    assert max(positions[1::3]) == pytest.approx(0.25 + 3.0, rel=0.01)  # deck plus a hemisphere


def test_sweep_of_an_open_line_is_a_capped_outward_tube():
    line = [(0.0, 1.0, 0.0), (0.0, 1.0, 5.0), (3.0, 1.0, 9.0)]
    positions, indices = sweep_mesh(line, circle_profile(0.5, 16), closed=False)
    assert max(indices) < len(positions) // 3
    assert signed_volume(positions, indices) > 0
    straight, _ = sweep_mesh([(0.0, 0.0, 0.0), (0.0, 0.0, 10.0)], circle_profile(0.5, 32), closed=False)
    volume = signed_volume(straight, _)
    assert volume == pytest.approx(math.pi * 0.25 * 10, rel=0.02)


def test_simplify_line_keeps_ends_and_turns_but_drops_straight_runs():
    straight = [(0.0, 0.0, float(z)) for z in range(0, 21)]
    assert simplify_line(straight, closed=False, max_length=100) == [straight[0], straight[-1]]
    corner = [(0.0, 0.0, float(z)) for z in range(6)] + [(float(x), 0.0, 5.0) for x in range(1, 6)]
    kept = simplify_line(corner, closed=False, max_length=100)
    assert (0.0, 0.0, 5.0) in kept and kept[0] == corner[0] and kept[-1] == corner[-1]
    assert len(simplify_line(straight, closed=False, max_length=6)) > 2


# Sweeps ---------------------------------------------------------------------------


def test_sweep_follows_the_ground_and_posts_reach_it():
    sweep = {
        "kind": "generator",
        "id": "pipe",
        "generator": {
            "gen": "sweep",
            "points": [[-15, 0, 0], [15, 0, 0]],
            "smooth": False,
            "radius": 0.3,
            "elevation": 1.5,
            "conform_to_terrain": True,
            "supports": {"spacing": 5, "shape": "box", "width": 0.2},
        },
    }
    world = small_world([terrain(), sweep])
    scene = lower(world)
    check = _Lowerer(world)
    mesh = items(scene, id="pipe")[0]
    assert mesh.role == "sweep" and mesh.type == "mesh"
    posts = [i for i in scene.items if i.id.startswith("pipe/support")]
    assert len(posts) == 6
    for post in posts:
        x, y, z = post.matrix[12:15]
        assert y == pytest.approx(check.ground_height(x, z), abs=1e-4)
        top = y + post.size[1]
        assert top == pytest.approx(check.ground_height(x, z) + 1.5 - 0.3, abs=0.35)


def test_sweep_offset_moves_it_to_the_right_of_travel():
    def lowered(offset):
        node = {
            "kind": "generator",
            "id": "strip",
            "generator": {"gen": "sweep", "points": [[0, 0, 0], [0, 0, 10]], "smooth": False, "offset": offset},
        }
        return items(lower(small_world([node])), id="strip")[0]

    centre = sum(lowered(0).positions[0::3]) / len(lowered(0).positions[0::3])
    shifted = lowered(2.0).positions[0::3]
    # Travelling along +z, the right-hand side is -x.
    assert sum(shifted) / len(shifted) == pytest.approx(centre - 2.0, abs=1e-6)


def test_guide_paths_steer_sweeps_but_are_not_drawn(scene):
    assert not items(scene, id="monorail_line")
    beam = items(scene, id="monorail_beam")[0]
    ys = beam.positions[1::3]
    assert min(ys) == pytest.approx(14 - 0.35, abs=0.01) and max(ys) == pytest.approx(14 + 0.35, abs=0.01)
    pylons = [i for i in scene.items if i.id.startswith("monorail_beam/support")]
    assert len(pylons) >= 20


# Buildings, terrain, scatter -------------------------------------------------------------


def test_polygon_building_gets_a_dome_over_its_walls(scene):
    roof = items(scene, id="hub/roof")[0]
    assert roof.type == "mesh"
    xs, zs = roof.positions[0::3], roof.positions[2::3]
    reach = max(math.hypot(x, z) for x, z in zip(xs, zs))
    assert reach >= 8.0  # covers the octagon's corners
    assert not any("hub" in w for w in scene.extras.get("warnings", []))
    assert len(items(scene, node="hub", role="window")) == 7  # one per side, none beside the door


def test_terrain_liquid_uses_its_material(scene):
    lava = items(scene, id="ground/water")[0]
    assert lava.material == "lava"
    assert scene.materials["lava"].emissive is not None


def test_scatter_items_carry_material_overrides(scene):
    crystals = [i for i in scene.items if i.node == "crystals"]
    assert crystals and all(i.materials == {"crystal": "crystal_glow"} for i in crystals)
    assert sum(len(i.transforms) for i in crystals) >= 15


# Environment ------------------------------------------------------------------------


def test_space_sky_and_bloom_reach_the_loader(scene):
    sky = scene.environment.sky
    assert sky.type == "space" and len(sky.bodies) == 3 and sky.bodies[0].ring is not None
    assert scene.environment.post.bloom.threshold > 0


def test_ring_must_be_wider_than_the_body():
    with pytest.raises(Exception):
        small_world(
            [],
            environment={
                "sky": {"type": "space", "bodies": [{"azimuth_deg": 0, "elevation_deg": 10, "ring": {"inner": 0.5}}]}
            },
        )


# Behaviours --------------------------------------------------------------------------


def test_behaviours_cover_the_node_and_its_children(scene):
    ship = next(b for b in scene.behaviors if b.node == "ship")
    assert ship.preset == "bob"
    assert set(ship.items) == {"ship_hull", "thruster_l", "thruster_r", "thrust_light"}
    assert ship.origin[13] == pytest.approx(items(scene, id="ship_hull")[0].matrix[13])


def test_follow_path_points_ride_on_the_beam(scene):
    trains = [b for b in scene.behaviors if b.preset == "follow_path" and b.node.startswith("train")]
    assert len(trains) == 3
    for b in trains:
        assert b.closed and all(p[1] == pytest.approx(14.35) for p in b.path)
    rover = next(b for b in scene.behaviors if b.node == "rover")
    assert not rover.closed


def test_follow_path_on_a_terrain_path_rides_the_ground():
    path = {"kind": "path", "id": "trail", "points": [[-10, 0, -10], [10, 0, 10]]}
    cart = {
        "kind": "primitive",
        "id": "cart",
        "shape": "box",
        "size": [1, 1, 1],
        "behaviors": [{"preset": "follow_path", "path": "trail", "params": {"height_m": 0.5}}],
    }
    world = small_world([terrain(), path, cart])
    scene = lower(world)
    check = _Lowerer(world)
    b = scene.behaviors[0]
    for x, y, z in b.path:
        assert y == pytest.approx(check.ground_height(x, z) + 0.5, abs=2e-3)


def test_behaviours_not_played_yet_are_reported():
    door = {
        "kind": "primitive",
        "id": "door",
        "shape": "box",
        "size": [1, 2, 0.1],
        "behaviors": [{"preset": "open_on_approach"}],
    }
    scene = lower(small_world([door]))
    assert scene.behaviors == []
    assert any("open_on_approach" in w for w in scene.extras["warnings"])


def test_animated_items_keep_their_rest_pose(scene, colony):
    train = items(scene, id="train_1")[0]
    assert train.matrix[12:15] == pytest.approx(colony.node("train_1").xform.pos)


# The example ---------------------------------------------------------------------------


def test_colony_lowers_everything(scene):
    assert scene.unsupported == []
    assert scene.extras == {}


def test_hovering_ship_is_not_snapped(colony, lowerer):
    x, y, z = (lowerer.node_world["ship"][r][3] for r in range(3))
    assert y - lowerer.ground_height(x, z) > 5


def test_the_committed_colony_is_already_snapped(colony):
    _, moved = snap_to_ground(colony)
    assert moved == [], "run scripts/snap.py examples/scifi_colony.json"
