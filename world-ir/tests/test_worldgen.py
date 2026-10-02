"""The clean-world generator: varied furnished rooms and flats that pass every check."""

import collections
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.furniture import KIT_SCALE, library  # noqa: E402
from world_ir.inject import make_tasks  # noqa: E402
from world_ir.layout import Layout, footprint  # noqa: E402
from world_ir.nodes import AssetNode, RoomNode  # noqa: E402
from world_ir.reward import score_repair  # noqa: E402
from world_ir.validate import TYPICAL_HEIGHT, validate  # noqa: E402
from world_ir.worldgen import ROOM_KINDS, SUITES, THEMES, generate, generate_many, generate_suite  # noqa: E402


def clean(world: World) -> bool:
    report = validate(world)
    return report.count("error") == report.count("warning") == report.count("ask") == 0


@pytest.mark.parametrize("kind", ROOM_KINDS)
def test_every_room_type_comes_out_clean(kind):
    for seed in (1, 2, 3):
        result = generate(seed, kind)
        assert result.world.nodes[0].room_type == kind
        assert clean(result.world), (kind, seed, validate(result.world).as_text())
        assert result.dropped == []  # the layout satisfied its own relations without help


def test_same_seed_same_world():
    a = generate(41, "kitchen").world.model_dump_json()
    b = generate(41, "kitchen").world.model_dump_json()
    assert a == b
    assert a != generate(42, "kitchen").world.model_dump_json()


@pytest.mark.parametrize("rooms", SUITES)
def test_flats_are_clean_and_connected(rooms):
    result = generate_suite(5, list(rooms))
    world = result.world
    assert clean(world), validate(world).as_text()
    assert [n.room_type for n in world.nodes] == list(rooms)
    ids = {n.id for n in world.nodes}
    links = [(n.id, o.connects_to) for n in world.nodes for o in n.openings if o.connects_to]
    assert len(links) == 2 * (len(rooms) - 1) and all(b in ids for _, b in links)
    # Rooms never overlap: their world-space boxes are at least a wall apart.
    boxes = []
    for n in world.nodes:
        c, s = math.cos(math.radians(n.xform.yaw or 0)), math.sin(math.radians(n.xform.yaw or 0))
        pts = [(n.xform.pos[0] + c * x + s * z, n.xform.pos[2] - s * x + c * z) for x, z in n.outline]
        boxes.append((min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] + 1e-6 or b[2] <= a[0] + 1e-6 or a[3] <= b[1] + 1e-6 or b[3] <= a[1] + 1e-6


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_themes_make_deliberate_oddities_that_validate(theme):
    world = generate(9, "living_room", theme=theme).world
    odd = [node for node, _ in world.walk() if node.intent]
    assert odd and theme in world.brief.mood
    report = validate(world)
    waived = [i for i in report.issues if i.waived_by]
    assert waived and clean(world)
    for node in odd:
        for intent in node.intent:
            assert intent.licence == theme if intent.source == "builder" else intent.quote in world.brief.prompt


@pytest.fixture(scope="module")
def batch():
    return generate_many(70, seed=500)


def test_a_batch_is_varied(batch):
    rooms, models, relations, prompts = set(), set(), set(), set()
    objects = []
    for r in batch:
        w = r.world
        prompts.add(w.brief.prompt)
        relations |= {rel.rel for rel in w.relations}
        n = 0
        for node, _ in w.walk():
            if isinstance(node, RoomNode):
                rooms.add(node.room_type)
            if isinstance(node, AssetNode):
                models.add(w.assets[node.asset].extras["model"])
                n += 1
        objects.append(n)
    assert len(rooms) == len(ROOM_KINDS)
    assert len(models) >= 80
    assert len(relations) >= 12
    assert len(prompts) >= 66
    assert min(objects) >= 4 and max(objects) >= 25
    assert any(len(r.world.nodes) > 1 for r in batch)


def test_bugs_planted_in_generated_worlds_are_found_and_undone(batch):
    kinds = collections.Counter()
    for i, r in enumerate(batch[:12]):
        for task in make_tasks(r.world, 2, seed=900 + i):
            report = validate(task.world)
            found = {x.code for x in report.issues if x.severity == "error"}
            for bug in task.bugs:
                kinds[bug.kind] += 1
                assert set(bug.codes) <= found
            score = score_repair(task.world, [a for b in task.bugs for a in b.oracle])
            assert score.valid and score.new == 0 and score.fixed == score.errors_before, task.id
    assert len(kinds) >= 5


def test_the_furniture_library_is_complete_and_to_scale():
    pieces = library()
    assert len(pieces) >= 110
    for p in pieces.values():
        assert (ROOT / "viewer" / p.asset().uri).exists(), p.name
        assert all(d > 0 for d in p.dims)
        typical = TYPICAL_HEIGHT.get(p.category) or TYPICAL_HEIGHT.get(p.category.split()[-1])
        if typical:
            assert 0.5 <= p.h / typical <= 2.0, (p.name, p.h, typical)
        if p.top is not None:
            assert 0 < p.top <= p.h + 1e-6
    assert pieces["bedDouble"].asset().extras["kit_scale"] == KIT_SCALE


def test_layout_places_backs_against_walls():
    import random

    L = Layout(random.Random(1), "room", [(0, 0), (4, 0), (4, 3), (0, 3)], 2.6, [])
    assert [w.yaw for w in L.walls] == [0.0, 270.0, 180.0, 90.0]
    bed = L.against_wall(library()["bedDouble"], walls=[L.walls[1]], where="middle")
    assert bed is not None and bed.yaw == 270.0
    assert max(x for x, _ in bed.poly) == pytest.approx(3.99, abs=0.005)
    # Nothing else may overlap it now.
    assert L.free_at(library()["desk"], bed.x, bed.z, 0.0) is None
    assert footprint(0, 0, 2, 1, 90)[0] == pytest.approx((-0.5, 1.0))
