"""The bug injector: plants labelled mistakes in clean worlds, to make repair tasks.

Every bug is checked: it is kept only if the validators report a new error for
it, so every task is solvable and every label is true. Each bug carries its
oracle fix, the actions that put the world back. Decoys plant a deliberate
oddity instead (an object floating because the brief now asks for it), so a
repair model also learns what not to fix.
"""

import math
import random
from typing import Any, Optional

from pydantic import Field

from .common import IRModel, cfg
from .geometry import apply_dir, inverse
from .nodes import AssetNode, PrimitiveNode
from .validate import TYPICAL_HEIGHT, ValidationReport, _Validator
from .world import World

BUG_KINDS = ["lift", "sink", "push_wall", "collide", "block_door", "turn", "tilt", "scale"]


class Bug(IRModel):
    """One planted mistake and how to undo it."""

    model_config = cfg("v1")

    kind: str
    node: str
    note: str = Field(description="What was done, e.g. 'lifted 0.21 m'.")
    oracle: list[dict[str, Any]] = Field(description="Actions that restore the original placement.")
    codes: list[str] = Field(description="Error codes the validators report for it.")


class Task(IRModel):
    """A broken world, its labels, and nothing else: the model input is built from the world."""

    model_config = cfg("v1")

    id: str
    source: str = Field(description="ID of the clean world it was made from.")
    seed: int
    world: World
    bugs: list[Bug]
    decoys: list[str] = Field(default_factory=list, description="Nodes made deliberately odd: leave them alone.")


def _find(nodes: list[dict], node_id: str) -> Optional[dict]:
    for n in nodes:
        if n["id"] == node_id:
            return n
        hit = _find(n.get("children", []), node_id)
        if hit is not None:
            return hit
    return None


def _errors(report: ValidationReport) -> set[tuple]:
    return {(i.code, i.node, i.part, i.other, i.relation) for i in report.issues if i.severity == "error"}


def _still_found(planted: list["Bug"], report: ValidationReport, v: Any) -> bool:
    """Every bug planted so far still shows all the errors it was recorded with."""
    errors = _errors(report)
    for bug in planted:
        codes = {e[0] for e in errors if bug.node in (e[1], e[2]) or v.unit(bug.node) == e[1]}
        if not set(bug.codes) <= codes:
            return False
    return True


class Injector:
    """Plants bugs in one clean world. Deterministic for a given seed."""

    def __init__(self, world: World, seed: int = 0):
        self.clean = world
        self.rng = random.Random(seed)
        self.seed = seed

    # Helpers ------------------------------------------------------------------------

    def candidates(self, v: _Validator, taken: set[str]) -> list[Any]:
        out = []
        for node, _ in v.world.walk():
            if not isinstance(node, (AssetNode, PrimitiveNode)) or node.id in taken or v.locked(node.id):
                continue
            if node.intent or not v.by_node.get(node.id) or node.xform.quat is not None or node.xform.rot is not None:
                continue
            out.append(node)
        return out

    def local(self, v: _Validator, node_id: str, dx: float, dy: float, dz: float) -> tuple[float, float, float]:
        parent = v.parent.get(node_id)
        if parent is None:
            return dx, dy, dz
        return apply_dir(inverse(v.lw.node_world[parent]), (dx, dy, dz))

    @staticmethod
    def oracle(node: Any, kind: str) -> list[dict[str, Any]]:
        """The action that undoes a bug: the node's original position, yaw or scale."""
        x = node.xform
        if kind in ("turn", "tilt"):
            return [{"action": "rotate", "id": node.id, "yaw": round(x.yaw or 0.0, 2)}]
        if kind == "scale":
            return [{"action": "scale", "id": node.id, "scale": x.scale}]
        return [{"action": "move", "id": node.id, "to": [round(c, 4) for c in x.pos]}]

    # Bug kinds ------------------------------------------------------------------------

    def plant(self, kind: str, v: _Validator, node: Any, data: dict) -> Optional[str]:
        """Changes one node in the world data. Returns a note, or None if this kind does not apply."""
        rng = self.rng
        target = _find(data["nodes"], node.id)
        xf = target.setdefault("xform", {})
        pos = list(xf.get("pos", [0.0, 0.0, 0.0]))
        solid = v.by_node[node.id][0]
        support = v.support_of(solid)[0]

        def shift(dx: float, dy: float, dz: float) -> None:
            lx, ly, lz = self.local(v, node.id, dx, dy, dz)
            xf["pos"] = [round(pos[0] + lx, 4), round(pos[1] + ly, 4), round(pos[2] + lz, 4)]

        if kind in ("lift", "sink"):
            if support not in ("floor", "node", "terrain"):
                return None
            amount = rng.uniform(0.06, 0.4) if kind == "lift" else rng.uniform(0.05, 0.25)
            shift(0.0, amount if kind == "lift" else -amount, 0.0)
            return f"{'lifted' if kind == 'lift' else 'sunk'} {amount:.2f} m"
        if kind == "push_wall":
            room = v.room_of(node.id)
            if room is None:
                return None
            outline = v.rooms[room]["outline"]
            i = rng.randrange(len(outline))
            a, b = outline[i], outline[(i + 1) % len(outline)]
            ex, ez = b[0] - a[0], b[1] - a[1]
            n = math.hypot(ex, ez) or 1.0
            inward = (-ez / n, ex / n)
            gap = min((p[0] - a[0]) * inward[0] + (p[1] - a[1]) * inward[1] for p in solid.footprint)
            depth = rng.uniform(0.12, 0.35)
            shift(-inward[0] * (gap + depth), 0.0, -inward[1] * (gap + depth))
            return f"pushed {depth:.2f} m into wall {i} of {room}"
        if kind == "collide":
            others = [
                s
                for s in v.solids
                if s.owner != solid.owner
                and not v.assembled(s, solid)
                and math.dist(s.center, solid.center) < 8
                and min(s.top, solid.top) - max(s.base, solid.base) > 0.1
                and s.height > 0.1
            ]
            if not others:
                return None
            other = rng.choice(others)
            jitter = 0.25 * min(_half(other), _half(solid))
            tx = other.center[0] + rng.uniform(-jitter, jitter)
            tz = other.center[1] + rng.uniform(-jitter, jitter)
            shift(tx - solid.center[0], 0.0, tz - solid.center[1])
            return f"moved into {other.owner}"
        if kind == "block_door":
            zones = [i for i in v.scene.items if i.type == "zone" and i.role == "door_clearance"]
            room = v.room_of(node.id)
            zones = [z for z in zones if room and z.node == room]
            if not zones:
                return None
            zone = rng.choice(zones)
            cx = sum(p[0] for p in zone.polygon) / len(zone.polygon)
            cz = sum(p[1] for p in zone.polygon) / len(zone.polygon)
            shift(cx - solid.center[0], 0.0, cz - solid.center[1])
            return f"moved in front of {zone.id.split('/')[-2]}"
        if kind == "turn":
            facing = {r.a for r in v.world.relations if r.rel in ("faces", "faces_away", "against_wall")}
            if node.id not in facing or node.xform.rot is not None:
                return None
            delta = rng.choice([-1, 1]) * rng.uniform(45, 135)
            xf["yaw"] = round((node.xform.yaw or 0.0) + delta, 1)
            return f"turned {delta:+.0f}°"
        if kind == "tilt":
            if not isinstance(node, AssetNode) or node.xform.rot is not None:
                return None
            tx = rng.choice([-1, 1]) * rng.uniform(15, 40)
            tz = rng.choice([-1, 1]) * rng.uniform(0, 25)
            xf["rot"] = [round(tx, 1), node.xform.yaw or 0.0, round(tz, 1)]
            xf.pop("yaw", None)
            return f"tilted {tx:.0f}°/{tz:.0f}°"
        if kind == "scale":
            category = solid.category or ""
            known = category in TYPICAL_HEIGHT or category.split()[-1:] and category.split()[-1] in TYPICAL_HEIGHT
            if not known or not isinstance(node.xform.scale, (int, float)):
                return None
            factor = rng.uniform(2.3, 3.5) if rng.random() < 0.5 else rng.uniform(0.25, 0.42)
            xf["scale"] = round(node.xform.scale * factor, 3)
            return f"scaled ×{factor:.2f}"
        raise ValueError(f"unknown bug kind '{kind}'")

    def decoy(self, v: _Validator, node: Any, data: dict) -> Optional[str]:
        """Makes an object float on purpose: the brief asks for it, and an intent quotes that."""
        solid = v.by_node[node.id][0]
        if v.support_of(solid)[0] not in ("floor", "node", "terrain") or not solid.category:
            return None
        target = _find(data["nodes"], node.id)
        xf = target.setdefault("xform", {})
        pos = list(xf.get("pos", [0.0, 0.0, 0.0]))
        lift = self.rng.uniform(0.6, 1.0)
        _, ly, _ = self.local(v, node.id, 0.0, lift, 0.0)
        xf["pos"] = [pos[0], round(pos[1] + ly, 4), pos[2]]
        phrase = f"the {solid.category} floats in mid-air"
        brief = data.get("brief") or {"prompt": ""}
        brief["prompt"] = (brief.get("prompt", "").rstrip() + f" Make it so {phrase}.").strip()
        data["brief"] = brief
        target.setdefault("intent", []).append({"allows": ["floating"], "source": "prompt", "quote": phrase})
        return f"floats {lift:.2f} m on purpose"

    # Making tasks ----------------------------------------------------------------------

    def make(self, bugs: int = 2, decoys: int = 0, kinds: Optional[list[str]] = None, tries: int = 40) -> Task:
        kinds = kinds or BUG_KINDS
        world = self.clean
        report = _Validator(world).run()
        planted: list[Bug] = []
        decoy_nodes: list[str] = []
        taken: set[str] = set()
        for _ in range(tries):
            if len(planted) >= bugs and len(decoy_nodes) >= decoys:
                break
            v = _Validator(world)
            pool = self.candidates(v, taken)
            if not pool:
                break
            node = self.rng.choice(pool)
            data = world.model_dump(by_alias=True)
            make_decoy = len(decoy_nodes) < decoys and (len(planted) >= bugs or self.rng.random() < 0.4)
            if make_decoy:
                note = self.decoy(v, node, data)
            else:
                kind = self.rng.choice(kinds)
                note = self.plant(kind, v, node, data)
            if note is None:
                continue
            try:
                candidate = World.model_validate(data)
            except ValueError:
                continue
            new_report = _Validator(candidate).run()
            new = _errors(new_report) - _errors(report)
            mine = [e for e in new if node.id in (e[1], e[2]) or v.unit(node.id) == e[1]]
            if not _still_found(planted, new_report, v):
                continue  # this change would hide a bug planted earlier
            if make_decoy:
                if new:  # a decoy must not break anything else
                    continue
                decoy_nodes.append(node.id)
            else:
                if not mine:
                    continue  # not detectable: try something else
                planted.append(
                    Bug(
                        kind=kind,
                        node=node.id,
                        note=note,
                        oracle=self.oracle(node, kind),
                        codes=sorted({e[0] for e in mine}),
                    )
                )
            taken.add(node.id)
            world, report = candidate, new_report
        return Task(
            id=f"{self.clean.id}-{self.seed}",
            source=self.clean.id,
            seed=self.seed,
            world=world,
            bugs=planted,
            decoys=decoy_nodes,
        )


def _half(solid: Any) -> float:
    x0 = min(p[0] for p in solid.footprint)
    x1 = max(p[0] for p in solid.footprint)
    return (x1 - x0) / 2


def make_tasks(
    world: World, count: int, seed: int = 0, bugs: tuple[int, int] = (1, 3), decoy_share: float = 0.3
) -> list[Task]:
    """Many tasks from one clean world: 1–3 bugs each, and a decoy in about a third of them."""
    rng = random.Random(seed)
    tasks = []
    for k in range(count):
        n = rng.randint(*bugs)
        decoys = 1 if rng.random() < decoy_share else 0
        task = Injector(world, seed=seed * 100003 + k).make(bugs=n, decoys=decoys)
        if task.bugs:
            tasks.append(task)
    return tasks
