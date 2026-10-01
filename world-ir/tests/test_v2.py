"""Tests for the v2 lowering: terrain, paths, prefabs, scatter, generators and snapping."""

import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.geometry import roof_mesh, rock_mesh  # noqa: E402
from world_ir.lower import _Lowerer, lower  # noqa: E402
from world_ir.snap import snap_to_ground  # noqa: E402
from world_ir.terrain_eval import Noise2D, point_in_polygon  # noqa: E402


def cabin_data() -> dict:
    return json.loads((ROOT / "examples" / "cabin_clearing.json").read_text())


@pytest.fixture(scope="module")
def cabin() -> World:
    return World.model_validate(cabin_data())


@pytest.fixture(scope="module")
def scene(cabin):
    return lower(cabin)


@pytest.fixture(scope="module")
def lowerer(cabin):
    return _Lowerer(cabin)


def items(scene, **match):
    return [i for i in scene.items if all(getattr(i, k) == v for k, v in match.items())]


# Terrain ------------------------------------------------------------------------


def test_only_later_tier_nodes_are_skipped(scene):
    assert [(u.node, u.kind) for u in scene.unsupported] == [("fire_flames", "particles")]


def test_flatten_makes_an_exactly_level_pad(lowerer):
    # The pad has a 9.5 m radius. Heights between grid vertices are interpolated, so a point is
    # exactly level once its whole grid cell is inside: one cell diagonal (1.06 m) in from the edge.
    for r in (0.0, 3.0, 6.5, 8.4):
        for a in range(0, 360, 45):
            x, z = r * math.cos(math.radians(a)), r * math.sin(math.radians(a))
            assert lowerer.ground_height(x, z) == pytest.approx(0.5, abs=1e-9)


def test_carved_trail_is_level_across_its_width(lowerer):
    trail = lowerer.path_world["trail"]
    checked = 0
    for i in range(5, len(trail) - 5, 7):
        p, q = trail[i - 1], trail[i + 1]
        if math.hypot(p[0], p[2]) < 16:  # past the pad and its blend
            continue
        tx, tz = q[0] - p[0], q[2] - p[2]
        n = math.hypot(tx, tz)
        nx, nz = -tz / n, tx / n
        left = lowerer.ground_height(trail[i][0] + nx * 0.5, trail[i][2] + nz * 0.5)
        right = lowerer.ground_height(trail[i][0] - nx * 0.5, trail[i][2] - nz * 0.5)
        assert abs(left - right) < 0.08
        checked += 1
    assert checked >= 5


def test_layers_follow_their_rules(scene, cabin):
    hf = items(scene, type="heightfield")[0]
    assert set(hf.layers) == {0, 1, 2, 3}
    campsite = [tuple(p) for p in cabin.node("campsite").area]
    for r in range(hf.rows):
        for c in range(hf.cols):
            x = -hf.size[0] / 2 + hf.size[0] * c / (hf.cols - 1)
            z = -hf.size[1] / 2 + hf.size[1] * r / (hf.rows - 1)
            if math.hypot(x, z) < 8:
                assert point_in_polygon(x, z, campsite)
                assert hf.layers[r * hf.cols + c] == 3, "dirt inside the campsite"


def test_water_plane_sits_at_the_water_level(scene):
    water = items(scene, role="water")[0]
    assert water.matrix[13] == pytest.approx(-1.62)


@pytest.mark.parametrize("algorithm", ["perlin", "simplex", "value", "worley", "ridged"])
def test_noise_is_deterministic_and_bounded(algorithm):
    a, b = Noise2D(3), Noise2D(3)
    values = [a.sample(algorithm, x * 0.37, x * 0.61) for x in range(200)]
    assert values == [b.sample(algorithm, x * 0.37, x * 0.61) for x in range(200)]
    assert all(-1.6 <= v <= 1.6 for v in values)
    assert max(values) - min(values) > 0.3


# Paths, prefabs and scatter -----------------------------------------------------


def test_path_ribbon_lies_on_the_ground(scene, lowerer):
    path = items(scene, role="path")[0]
    pts = [path.positions[i : i + 3] for i in range(0, len(path.positions), 3)]
    for x, y, z in pts[::25]:
        assert y == pytest.approx(lowerer.ground_height(x, z) + 0.04, abs=1e-3)


def test_prefab_copies_belong_to_their_instance_and_honour_overrides(scene):
    first = {i.id: i for i in items(scene, node="picnic_1")}
    assert set(first) == {
        "picnic_1/table",
        "picnic_1/bench_n1",
        "picnic_1/bench_n2",
        "picnic_1/bench_s1",
        "picnic_1/bench_s2",
    }
    second = {i.id: i for i in items(scene, node="picnic_2")}
    assert second["picnic_2/bench_s2"].visible is False
    assert second["picnic_2/bench_s1"].visible is True


def test_scatter_respects_area_avoid_spacing_and_height(scene, cabin, lowerer):
    campsite = [tuple(p) for p in cabin.node("campsite").area]
    trail = lowerer.path_world["trail"]
    for scatter in ("forest", "undergrowth"):
        node = cabin.node(scatter)
        points = [t for i in items(scene, node=scatter) for t in i.transforms]
        assert len(points) > 100
        for x, y, z, _, _ in points:
            assert not point_in_polygon(x, z, campsite)
            assert min(math.dist((x, z), (p[0], p[2])) for p in trail) > 0.8 - 1e-6
            assert y == pytest.approx(lowerer.ground_height(x, z), abs=1e-4)  # positions are stored to 0.1 mm
            assert node.height_range[0] <= y <= node.height_range[1]
        xz = [(x, z) for x, _, z, _, _ in points]
        closest = min(math.dist(a, b) for i, a in enumerate(xz) for b in xz[i + 1 : i + 40])
        assert closest >= node.min_spacing - 1e-6


def test_scatter_keeps_clear_of_named_nodes(scene):
    deck = [i.aabb for i in scene.items if i.node in ("deck", "cabin", "porch_steps") and i.aabb]
    for x, _, z, _, _ in [t for i in items(scene, node="forest") for t in i.transforms]:
        for (x0, _, z0), (x1, _, z1) in deck:
            assert not (x0 <= x <= x1 and z0 <= z <= z1)


# Generators ----------------------------------------------------------------------


def test_building_cuts_windows_and_door_and_has_a_valid_roof(scene):
    cabin = items(scene, node="cabin")
    assert len([i for i in cabin if i.role == "window"]) == 4
    assert len([i for i in cabin if i.role == "door"]) == 1
    roof = [i for i in cabin if i.role == "roof"][0]
    n = len(roof.positions) // 3
    assert len(roof.indices) % 3 == 0 and max(roof.indices) < n


@pytest.mark.parametrize("style", ["flat", "shed", "gable", "hip", "pyramid", "mansard"])
def test_roof_meshes_are_well_formed(style):
    (positions, indices), built = roof_mesh(style, 6.0, 5.0, 35.0, 0.4)
    verts = [positions[i : i + 3] for i in range(0, len(positions), 3)]
    assert len(indices) % 3 == 0 and max(indices) < len(verts)
    for k in range(0, len(indices), 3):
        a, b, c = (verts[i] for i in indices[k : k + 3])
        ab = [b[j] - a[j] for j in range(3)]
        ac = [c[j] - a[j] for j in range(3)]
        cross = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
        assert math.hypot(*cross) > 1e-6, "no degenerate triangles"
    assert built == (style if style != "mansard" else "gable")


def test_rock_mesh_fills_the_unit_box():
    positions, indices = rock_mesh(4, 0.6)
    xs, ys, zs = positions[0::3], positions[1::3], positions[2::3]
    assert min(xs) == pytest.approx(-0.5) and max(xs) == pytest.approx(0.5)
    assert min(ys) == pytest.approx(0.0) and max(ys) == pytest.approx(1.0)
    assert min(zs) == pytest.approx(-0.5) and max(zs) == pytest.approx(0.5)
    assert max(indices) < len(xs)


def test_fence_posts_stand_on_the_terrain(scene, lowerer):
    posts = [i for i in items(scene, node="back_fence") if "/post" in i.id]
    assert len(posts) >= 6
    for post in posts:
        x, y, z = post.matrix[12:15]
        assert y == pytest.approx(lowerer.ground_height(x, z), abs=1e-4)


def test_every_generator_kind_in_v2_lowers():
    data = json.loads((ROOT / "examples" / "bedroom.json").read_text())
    room = data["nodes"][0]
    extra = [
        {
            "kind": "generator",
            "id": "shelves",
            "xform": {"pos": [1.5, 0, 3.3]},
            "generator": {"gen": "shelving", "shelves": 4},
        },
        {
            "kind": "generator",
            "id": "kitchen",
            "xform": {"pos": [2.0, 0, 0.5]},
            "generator": {
                "gen": "kitchen_run",
                "modules": [
                    {"type": "fridge"},
                    {"type": "sink"},
                    {"type": "stove"},
                    {"type": "dishwasher"},
                    {"type": "gap", "width": 0.3},
                    {"type": "base"},
                ],
            },
        },
        {
            "kind": "generator",
            "id": "dining",
            "xform": {"pos": [2.0, 0, 2.0]},
            "generator": {"gen": "table_set", "table": "desk", "chair": "chair_desk", "chairs": 6},
        },
        {
            "kind": "generator",
            "id": "ring",
            "generator": {"gen": "radial_array", "asset": "lamp_table", "count": 5, "radius": 1.2},
        },
        {"kind": "generator", "id": "ramp", "generator": {"gen": "ramp", "length": 2.0, "rise": 0.3}},
        {"kind": "generator", "id": "pillar", "generator": {"gen": "column", "profile": "octagonal", "height": 2.4}},
        {
            "kind": "generator",
            "id": "handrail",
            "generator": {"gen": "railing", "points": [[0, 0, 0], [1, 0.2, 0], [2, 0.2, 1]]},
        },
        {"kind": "generator", "id": "shrub", "generator": {"gen": "bush", "flowers": "#d9465f"}},
        {
            "kind": "generator",
            "id": "lawn",
            "generator": {"gen": "grass", "area": [[0, 0], [1, 0], [1, 1], [0, 1]], "density": 20},
        },
        {
            "kind": "generator",
            "id": "canopy",
            "generator": {"gen": "roof", "footprint": [3, 2], "roof": {"style": "hip"}},
        },
        {"kind": "generator", "id": "palm", "generator": {"gen": "tree", "species": "palm", "height": 5}},
        {"kind": "generator", "id": "pine", "generator": {"gen": "tree", "species": "conifer", "height": 6}},
        {"kind": "generator", "id": "bare", "generator": {"gen": "tree", "species": "deciduous", "season": "winter"}},
        {
            "kind": "generator",
            "id": "hedge",
            "generator": {"gen": "fence", "points": [[0, 0], [3, 0]], "style": "hedge"},
        },
        {
            "kind": "generator",
            "id": "pickets",
            "generator": {"gen": "fence", "points": [[0, 1], [3, 1]], "style": "picket", "gates": [1.5]},
        },
    ]
    room["children"] += extra
    scene = lower(World.model_validate(data))
    assert scene.unsupported == []
    lowered_nodes = {i.node for i in scene.items}
    assert {e["id"] for e in extra} <= lowered_nodes
    dining = [i for i in scene.items if i.node == "dining"]
    assert len(dining) == 7, "a table and six chairs"


# Snapping -----------------------------------------------------------------------


def test_snapping_puts_terrain_supported_nodes_on_the_ground():
    data = cabin_data()
    for node in data["nodes"]:
        if node["id"] in ("picnic_2", "landmark_rock"):
            node["xform"]["pos"][1] = 5.0  # lift them into the air
    snapped, moved = snap_to_ground(World.model_validate(data))
    assert {line.split(":")[0] for line in moved} == {"picnic_2", "landmark_rock"}
    check = _Lowerer(snapped)
    for node_id in ("picnic_2", "landmark_rock", "fire", "visitor_start"):
        x, y, z = (check.node_world[node_id][r][3] for r in range(3))
        assert y == pytest.approx(check.ground_height(x, z), abs=1e-3)


def test_the_committed_example_is_already_snapped(cabin):
    _, moved = snap_to_ground(cabin)
    assert moved == [], "run scripts/snap.py examples/cabin_clearing.json"
