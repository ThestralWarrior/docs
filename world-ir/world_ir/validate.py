"""Validators: measure a world against its own rules, relations and intent.

They read the lowered scene, so they measure exactly what the viewer draws. Every
issue names the IR node to edit, says how far off it is, and where possible
carries a suggested fix as a repair action. The text form (``as_text``) is what
the repair model reads.

Severity:
- error: a real mistake. The repair loop must fix it.
- warning: a soft relation is broken. Lowers the score.
- ask: something odd that might be deliberate. The user decides.
- info: waived by intent, or not checked yet. Shown, never repaired.
"""

import math
from dataclasses import dataclass
from typing import Any, Literal, Optional

from pydantic import Field

from .common import IRModel, cfg
from .geometry import Mat, apply, apply_dir, inverse
from .lower import _Lowerer, lower
from .lowered import LoweredScene
from .nodes import AssetNode, GroupNode, PrefabNode, PrimitiveNode, RoomNode, TerrainNode, ZoneNode
from .terrain_eval import point_in_polygon, segment_distance
from .vocab import ODDNESS_LICENCES
from .world import World

Severity = Literal["error", "warning", "ask", "info"]
SEVERITY_WEIGHT = {"error": 1.0, "warning": 0.5, "ask": 0.0, "info": 0.0}

HOVERING = {
    "spaceship",
    "ship",
    "drone",
    "balloon",
    "airship",
    "bird",
    "cloud",
    "hologram",
    "satellite",
    "ufo",
    "planet",
    "moon",
    "star",
    "ghost",
}
"""Categories that float by nature, so support 'none' needs no intent."""

TYPICAL_HEIGHT = {
    "bed": 0.6,
    "nightstand": 0.55,
    "table lamp": 0.5,
    "lamp": 0.6,
    "desk": 0.75,
    "chair": 0.95,
    "desk chair": 1.05,
    "stool": 0.65,
    "table": 0.75,
    "picnic table": 0.75,
    "bench": 0.8,
    "sofa": 0.85,
    "armchair": 0.95,
    "bookcase": 1.8,
    "cabinet": 1.2,
    "wardrobe": 2.0,
    "box": 0.4,
    "crate": 0.5,
    "barrel": 0.9,
    "door": 2.1,
    "person": 1.75,
    "astronaut": 1.85,
    "alien": 1.7,
    "tent": 1.6,
    "pine tree": 12.0,
    "tree": 8.0,
    "bush": 1.0,
}
"""Typical heights in metres, for the scale check. Unknown categories are not checked."""

UNCHECKED_RELATIONS = {
    "mounted_on_wall",
    "inside",
    "attached_to",
    "between",
    "relative",
    "parallel",
    "perpendicular",
    "aligned",
    "around",
    "row",
    "grid",
    "symmetric",
    "rigid_group",
    "walkway",
    "reachable",
    "headroom",
    "visible_from",
    "lit",
    "style",
    "palette",
    "within_bounds",
    "plausible_scale",
    "stable",
    "in_corner",
}


class Issue(IRModel):
    """One thing the validators found."""

    model_config = cfg("v1")

    code: str = Field(description="Which check: 'floating', 'overlap', 'relation:faces', ...")
    severity: Severity
    node: str = Field(description="The IR node to edit (the prefab instance for parts of a prefab).")
    part: Optional[str] = Field(None, description="The prefab part, when the problem is inside an instance.")
    other: Optional[str] = Field(None, description="The other node involved, if any.")
    relation: Optional[str] = Field(None, description="ID or index of the relation that is broken.")
    message: str = Field(description="Plain sentence for people and for the repair model.")
    amount: Optional[float] = Field(None, description="How far off: metres, degrees or a ratio.")
    fix: Optional[dict[str, Any]] = Field(None, description="A suggested repair action, when one is obvious.")
    waived_by: Optional[str] = Field(None, description="The intent that makes this deliberate.")
    fixable: bool = Field(True, description="False when the node is locked.")


class ValidationReport(IRModel):
    """Everything the validators found in one world."""

    model_config = cfg("v1")

    world_id: str
    issues: list[Issue] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list, description="Checks and relations not measured yet.")
    score: float = Field(description="Weighted count of errors and warnings. Lower is better; 0 is clean.")

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i.severity == severity)

    @property
    def clean(self) -> bool:
        return self.count("error") == 0

    def as_text(self, hints: bool = False) -> str:
        """The issue list as the repair model reads it. hints=True adds the suggested fixes."""
        active = [i for i in self.issues if i.severity in ("error", "warning")]
        lines = [
            f"World {self.world_id}: {self.count('error')} errors, {self.count('warning')} warnings, "
            f"{self.count('ask')} questions for the user."
        ]
        for i in active:
            lock = " (locked: do not edit)" if not i.fixable else ""
            lines.append(f"- [{i.severity} {i.code}] {i.message}{lock}")
            if hints and i.fix:
                lines.append(f"  suggested: {_fix_text(i.fix)}")
        asks = [i for i in self.issues if i.severity == "ask"]
        if asks:
            lines.append("Ask the user, do not change:")
            lines += [f"- [{i.code}] {i.message}" for i in asks]
        waived = [i for i in self.issues if i.waived_by]
        if waived:
            lines.append("Deliberate, leave alone:")
            lines += [f"- [{i.code}] {i.node}: {i.waived_by}" for i in waived]
        return "\n".join(lines)


def _fix_text(fix: dict[str, Any]) -> str:
    rest = ", ".join(f"{k}={v}" for k, v in fix.items() if k not in ("action", "id"))
    return f"{fix['action']} {fix['id']} {rest}"


def validate(world: World) -> ValidationReport:
    """Lowers the world and checks it. Deterministic."""
    return _Validator(world).run()


# Geometry helpers ---------------------------------------------------------------


def _mat(column_major: tuple[float, ...]) -> Mat:
    c = column_major
    return [[c[0], c[4], c[8], c[12]], [c[1], c[5], c[9], c[13]], [c[2], c[6], c[10], c[14]], [0, 0, 0, 1]]


def _area(poly: list[tuple[float, float]]) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def _ccw(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    return poly if signed > 0 else poly[::-1]


def _clip(subject: list[tuple[float, float]], clipper: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Intersection of two convex polygons (Sutherland-Hodgman)."""
    out = _ccw(subject)
    clipper = _ccw(clipper)
    for a, b in zip(clipper, clipper[1:] + clipper[:1]):
        inside = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-12  # noqa: E731
        src, out = out, []
        for i, p in enumerate(src):
            q = src[i - 1]
            if inside(p):
                if not inside(q):
                    out.append(_cross(q, p, a, b))
                out.append(p)
            elif inside(q):
                out.append(_cross(q, p, a, b))
        if not out:
            return []
    return out


def _cross(p, q, a, b) -> tuple[float, float]:
    x1, y1, x2, y2 = p[0], p[1], q[0], q[1]
    x3, y3, x4, y4 = a[0], a[1], b[0], b[1]
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        return q
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _poly_distance(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> float:
    """Edge-to-edge distance between two polygons; 0 when they touch or overlap."""
    if any(point_in_polygon(x, z, b) for x, z in a) or any(point_in_polygon(x, z, a) for x, z in b):
        return 0.0
    best = math.inf
    for p in a:
        for q0, q1 in zip(b, b[1:] + b[:1]):
            best = min(best, segment_distance(p[0], p[1], q0, q1)[0])
    for p in b:
        for q0, q1 in zip(a, a[1:] + a[:1]):
            best = min(best, segment_distance(p[0], p[1], q0, q1)[0])
    return best


def _bbox(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs, zs = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(zs), max(xs), max(zs)


def _escape(item_box, zone_box, margin: float = 0.02) -> tuple[float, float]:
    """Smallest x or z shift that takes one box out of another."""
    ix0, iz0, ix1, iz1 = item_box
    zx0, zz0, zx1, zz1 = zone_box
    options = [
        (zx1 - ix0 + margin, 0.0),
        (-(ix1 - zx0 + margin), 0.0),
        (0.0, zz1 - iz0 + margin),
        (0.0, -(iz1 - zz0 + margin)),
    ]
    return min(options, key=lambda d: abs(d[0]) + abs(d[1]))


def _angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    la, lb = math.hypot(*a), math.hypot(*b)
    if la < 1e-9 or lb < 1e-9:
        return 0.0
    c = max(-1.0, min(1.0, (a[0] * b[0] + a[1] * b[1]) / (la * lb)))
    return math.degrees(math.acos(c))


def _round(v: tuple[float, ...]) -> list[float]:
    return [round(x, 3) for x in v]


# The validator -------------------------------------------------------------------


@dataclass
class Solid:
    """An object the per-object checks look at: one asset or primitive item."""

    item: Any
    node: Any
    owner: str
    m: Mat
    size: tuple[float, float, float]
    base: float
    top: float
    footprint: list[tuple[float, float]]
    tilt: float
    front: tuple[float, float]
    center: tuple[float, float]
    category: Optional[str]

    @property
    def id(self) -> str:
        return self.item.id

    @property
    def height(self) -> float:
        return self.top - self.base


class _Validator:
    def __init__(self, world: World):
        self.world = world
        self.rules = world.rules
        self.lw = _Lowerer(world)
        self.scene: LoweredScene = lower(world, self.lw)
        self.nodes: dict[str, Any] = {}
        self.parent: dict[str, Optional[str]] = {}

        def visit(node: Any, parent: Optional[Any]) -> None:
            self.nodes[node.id] = node
            self.parent[node.id] = parent.id if parent is not None else None
            for copy in self.lw.prefab_copies.get(node.id, []):
                visit(copy, node)
            for child in node.children:
                visit(child, node)

        for root in world.nodes:
            visit(root, None)
        self.editable = {node.id for node, _ in world.walk()}
        self.issues: list[Issue] = []
        self.unchecked: set[str] = set()
        self.solids = [s for s in (self._solid(i) for i in self.scene.items) if s is not None]
        self.by_node: dict[str, list[Solid]] = {}
        for s in self.solids:
            self.by_node.setdefault(s.node.id, []).append(s)
            if s.owner != s.node.id:
                self.by_node.setdefault(s.owner, []).append(s)
        self.items_by_node: dict[str, list[Any]] = {}
        for item in self.scene.items:
            self.items_by_node.setdefault(item.node, []).append(item)
            self.items_by_node.setdefault(item.id, []).append(item)
        self.rooms = {nid: self._room(n) for nid, n in self.nodes.items() if isinstance(n, RoomNode)}
        self.has_terrain = any(isinstance(n, TerrainNode) for n in self.nodes.values())
        brief = world.brief
        given = set(brief.mood + brief.style + [brief.archetype or ""]) if brief else set()
        self.odd_brief = bool({g.lower() for g in given} & set(ODDNESS_LICENCES))

    # Setup ------------------------------------------------------------------------

    def _solid(self, item: Any) -> Optional[Solid]:
        if item.type == "asset":
            size = tuple(self.scene.assets[item.asset].dims)
        elif item.type == "shape" and item.role == "primitive":
            size = tuple(item.size)
        else:
            return None
        node = self.nodes.get(item.id)
        if node is None or not item.visible:
            return None
        m = _mat(item.matrix)
        w, h, d = size
        corners = [apply(m, (x, y, z)) for x in (-w / 2, w / 2) for y in (0.0, h) for z in (-d / 2, d / 2)]
        up = apply_dir(m, (0.0, 1.0, 0.0))
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, up[1] / (math.sqrt(sum(c * c for c in up)) or 1)))))
        if tilt < 5:
            bottom = [
                apply(m, (x, 0.0, z)) for x, z in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))
            ]
            footprint = [(p[0], p[2]) for p in bottom]
        else:
            xs, zs = [c[0] for c in corners], [c[2] for c in corners]
            footprint = [(min(xs), min(zs)), (max(xs), min(zs)), (max(xs), max(zs)), (min(xs), max(zs))]
        f = apply_dir(m, (0.0, 0.0, 1.0))
        cx = sum(p[0] for p in footprint) / 4
        cz = sum(p[1] for p in footprint) / 4
        return Solid(
            item=item,
            node=node,
            owner=self.unit(item.node),
            m=m,
            size=size,
            base=min(c[1] for c in corners),
            top=max(c[1] for c in corners),
            footprint=footprint,
            tilt=tilt,
            front=(f[0], f[2]),
            center=(cx, cz),
            category=self._category(node),
        )

    def _category(self, node: Any) -> Optional[str]:
        if node.semantic is not None:
            return node.semantic.category.lower()
        if isinstance(node, AssetNode):
            return self.world.assets[node.asset].category.lower()
        return None

    def _room(self, node: RoomNode) -> dict[str, Any]:
        m = self.lw.node_world[node.id]
        outline = [(p[0], p[2]) for p in (apply(m, (x, 0.0, z)) for x, z in node.outline)]
        floor = apply(m, (0.0, 0.0, 0.0))[1]
        return {"outline": _ccw(outline), "floor": floor, "ceiling": floor + node.height, "node": node}

    def unit(self, node_id: str) -> str:
        """The node a repair should move: the outermost group or prefab instance holding this one."""
        current = node_id
        while True:
            parent = self.parent.get(current)
            if parent is None or not isinstance(self.nodes[parent], (GroupNode, PrefabNode)):
                return current
            current = parent

    def room_of(self, node_id: str) -> Optional[str]:
        current = self.parent.get(node_id)
        while current is not None:
            if current in self.rooms:
                return current
            current = self.parent.get(current)
        return None

    def ancestors(self, node_id: str) -> list[str]:
        out, current = [], self.parent.get(node_id)
        while current is not None:
            out.append(current)
            current = self.parent.get(current)
        return out

    def waiver(self, node_id: str, check: str) -> Optional[Any]:
        current: Optional[str] = node_id
        while current is not None:
            for intent in self.nodes[current].intent:
                if check in intent.allows:
                    return intent
            current = self.parent.get(current)
        return None

    def asked_for(self, node_id: str) -> bool:
        """The node carries intent from the user's own words, so a hard break may be on purpose."""
        current: Optional[str] = node_id
        while current is not None:
            if any(i.source in ("prompt", "user", "image_brief") for i in self.nodes[current].intent):
                return True
            current = self.parent.get(current)
        return False

    def locked(self, node_id: str) -> bool:
        return any(self.nodes[n].locked for n in [node_id] + self.ancestors(node_id) if n in self.nodes)

    def inherited_tilt(self, solid: Solid) -> bool:
        for ancestor in self.ancestors(solid.node.id):
            for other in self.by_node.get(ancestor, []):
                if other.node.id == ancestor and other.tilt > self.rules.upright_tolerance_deg:
                    return True
        return False

    def deliberately_off(self, solid: Solid, gap: float) -> bool:
        """A support relation yields to intent: an object asked to float is not 'off the table' by mistake."""
        w = self.waiver(solid.node.id, "floating" if gap > 0 else "sunk")
        return w is not None and abs(gap) >= self.rules.deliberate_min_offset

    def protected(self, solid: Solid) -> bool:
        """Locked, or carrying a deliberate choice: suggested fixes move something else when they can."""
        ids = [solid.node.id, solid.owner] + self.ancestors(solid.node.id)
        return self.locked(solid.owner) or any(self.nodes[n].intent for n in ids if n in self.nodes)

    def tolerance(self, base: float, solid: Solid) -> float:
        return max(base, self.rules.relative_tolerance * solid.height)

    # Emitting ---------------------------------------------------------------------

    def emit(
        self,
        code: str,
        severity: Severity,
        solid: Optional[Solid],
        message: str,
        node: Optional[str] = None,
        amount: Optional[float] = None,
        other: Optional[str] = None,
        relation: Optional[str] = None,
        fix: Optional[dict[str, Any]] = None,
        waived_by: Optional[Any] = None,
    ) -> None:
        if node is not None:
            subject = node
        elif fix is not None:
            subject = fix["id"]
        elif solid is not None:
            subject = solid.node.id if solid.node.id in self.editable else solid.owner
        else:
            subject = ""
        part = solid.id if solid is not None and solid.id != subject else None
        if waived_by is not None:
            severity = "info"
            how = f'"{waived_by.quote}"' if waived_by.quote else f"licence '{waived_by.licence}': {waived_by.note}"
            waived_text = f"{waived_by.source}: {how}"
        else:
            waived_text = None
        self.issues.append(
            Issue(
                code=code,
                severity=severity,
                node=subject,
                part=part,
                other=other,
                relation=relation,
                message=message,
                amount=None if amount is None else round(amount, 3),
                fix=fix if severity in ("error", "warning") else None,
                waived_by=waived_text,
                fixable=not self.locked(subject) if subject in self.nodes else True,
            )
        )

    def target(self, solid: Solid, whole: bool) -> Optional[str]:
        """The node a fix edits: the whole group or prefab instance for collisions, else the object itself."""
        if whole or solid.node.id not in self.editable:
            return solid.owner if solid.owner in self.editable else None
        return solid.node.id

    def rotate_fix(self, solid: Solid, world_yaw: float) -> Optional[dict[str, Any]]:
        node_id = self.target(solid, whole=False)
        if node_id is None:
            return None
        parent = self.parent.get(node_id)
        parent_yaw = 0.0
        if parent:
            f = apply_dir(self.lw.node_world[parent], (0.0, 0.0, 1.0))
            parent_yaw = math.degrees(math.atan2(f[0], f[2]))
        yaw = (world_yaw - parent_yaw + 180) % 360 - 180
        return {"action": "rotate", "id": node_id, "yaw": round(yaw, 1)}

    def move_fix(self, solid: Solid, dx: float, dy: float, dz: float, whole: bool = False) -> Optional[dict[str, Any]]:
        node_id = self.target(solid, whole)
        if node_id is None:
            return None
        node = self.nodes[node_id]
        parent = self.parent.get(node.id)
        pm = self.lw.node_world[parent] if parent else None
        local = apply_dir(inverse(pm), (dx, dy, dz)) if pm else (dx, dy, dz)
        x, y, z = node.xform.pos
        return {"action": "move", "id": node.id, "to": _round((x + local[0], y + local[1], z + local[2]))}

    def name(self, solid: Solid) -> str:
        return solid.id

    # Running ----------------------------------------------------------------------

    def run(self) -> ValidationReport:
        checks = set(self.rules.checks)
        if "floating" in checks or "sunk" in checks:
            for s in self.solids:
                self.check_support(s, checks)
        if "upright" in checks:
            for s in self.solids:
                self.check_upright(s)
        if "overlap" in checks:
            self.check_overlaps()
        if "inside_wall" in checks:
            for s in self.solids:
                self.check_walls(s)
        if "out_of_bounds" in checks:
            for s in self.solids:
                self.check_bounds(s)
        if "door_clearance" in checks:
            self.check_clearance()
        if "scale" in checks:
            for s in self.solids:
                self.check_scale(s)
        if "relations" in checks or "facing" in checks:
            self.check_relations(checks)
        if "requirements" in checks:
            self.check_requirements()
        if "reachability" in checks:
            self.unchecked.add("check:reachability")
        score = sum(
            SEVERITY_WEIGHT[i.severity] * (self._relation_weight(i.relation) if i.severity == "warning" else 1.0)
            for i in self.issues
        )
        order = {"error": 0, "warning": 1, "ask": 2, "info": 3}
        self.issues.sort(key=lambda i: (order[i.severity], i.code, i.node, i.other or ""))
        return ValidationReport(
            world_id=self.world.id, issues=self.issues, unchecked=sorted(self.unchecked), score=round(score, 3)
        )

    def _relation_weight(self, ref: Optional[str]) -> float:
        if ref is None:
            return 1.0
        for index, rel in enumerate(self.world.relations):
            if rel.id == ref or f"relations[{index}]" == ref:
                return rel.weight
        return 1.0

    # Support: floating and sinking ---------------------------------------------------

    def support_of(self, solid: Solid) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """(kind, target node, surface) that holds this object up, declared or inferred."""
        node = solid.node
        if node.support is not None:
            return node.support.on, node.support.target, node.support.surface
        parent = self.parent.get(node.id)
        parent_node = self.nodes.get(parent) if parent else None
        if isinstance(parent_node, (AssetNode, PrimitiveNode)):
            return "node", parent, None
        if isinstance(parent_node, RoomNode):
            return "floor", None, None
        if parent_node is None and self.has_terrain:
            return "terrain", None, None
        return None, None, None

    def surface_height(self, target: str, surface: Optional[str]) -> Optional[float]:
        solids = self.by_node.get(target, [])
        if surface and len(solids) == 1:
            s = solids[0]
            asset = self.world.assets.get(getattr(s.node, "asset", ""), None)
            if asset is not None:
                for surf in asset.surfaces:
                    if surf.name == surface:
                        scale_y = math.sqrt(sum(s.m[r][1] ** 2 for r in range(3)))
                        return s.base + surf.height * scale_y
        items = [i for i in self.items_by_node.get(target, []) if getattr(i, "aabb", None)]
        if solids:
            return max(s.top for s in solids)
        if items:
            return max(i.aabb[1][1] for i in items)
        return None

    def check_support(self, s: Solid, checks: set[str]) -> None:
        kind, target, surface = self.support_of(s)
        label = s.id
        if kind is None or kind == "wall":
            return
        if kind == "none":
            if s.category in HOVERING or self.waiver(s.node.id, "floating"):
                return
            severity = "ask" if self.odd_brief else "error"
            self.emit(
                "floating",
                severity,
                s,
                f"{label} is marked as unsupported (support 'none'), but nothing says why it floats. "
                f"Give it a support, or an intent that allows floating.",
            )
            return
        if kind == "ceiling":
            room = self.room_of(s.node.id)
            if room is None:
                return
            gap = self.rooms[room]["ceiling"] - s.top
            where, measured, expected = "the ceiling", s.top, self.rooms[room]["ceiling"]
            dy = gap
        else:
            if kind == "floor":
                room = self.room_of(s.node.id)
                if room is not None:
                    expected = self.rooms[room]["floor"]
                    where = f"the floor of {room}"
                else:
                    ground = self.lw.ground_height(*s.center) if self.has_terrain else None
                    expected = ground if ground is not None else 0.0
                    where = "the ground"
            elif kind == "terrain":
                expected = self.lw.ground_height(*s.center)
                if expected is None:
                    return  # off the terrain: out_of_bounds reports it
                where = "the ground"
            else:  # node
                expected = self.surface_height(target, surface) if target else None
                if expected is None:
                    return
                where = f"{target}.{surface}" if surface else f"the top of {target}"
            gap = s.base - expected
            measured = s.base
            dy = -gap
        float_tol = self.tolerance(self.rules.float_tolerance, s)
        sink_tol = self.tolerance(self.rules.sink_tolerance, s)
        if gap > float_tol and "floating" in checks:
            code, verb = "floating", f"floats {gap:.2f} m away from {where}"
        elif gap < -sink_tol and "sunk" in checks:
            code, verb = "sunk", f"sinks {-gap:.2f} m into {where}"
        else:
            return
        message = f"{label} {verb} (at y {measured:.2f}, should be {expected:.2f})."
        w = self.waiver(s.node.id, code)
        if w is not None and abs(gap) >= self.rules.deliberate_min_offset:
            self.emit(code, "info", s, message, amount=abs(gap), waived_by=w)
            return
        if w is not None:
            message += f" It may {code.replace('sunk', 'sink').replace('floating', 'float')}, but {abs(gap):.2f} m looks like a near miss, not a choice."
        self.emit(code, "error", s, message, amount=abs(gap), fix=self.move_fix(s, 0.0, dy, 0.0))

    # Upright -------------------------------------------------------------------------

    def check_upright(self, s: Solid) -> None:
        if s.item.type != "asset" or s.tilt <= self.rules.upright_tolerance_deg:
            return
        if s.node.xform.rot is None and s.node.xform.quat is None and self.inherited_tilt(s):
            return  # tilted only because what it stands on is tilted: that object's issue covers it
        message = f"{s.id} is tilted {s.tilt:.0f}° from upright."
        w = self.waiver(s.node.id, "upright")
        if w is not None and s.tilt >= self.rules.deliberate_min_tilt_deg:
            self.emit("upright", "info", s, message, amount=s.tilt, waived_by=w)
            return
        if w is not None:
            message += f" Tilting is allowed here, but {s.tilt:.0f}° looks like a mistake, not a choice."
        fix = self.rotate_fix(s, math.degrees(math.atan2(s.front[0], s.front[1])))
        self.emit("upright", "error", s, message, amount=s.tilt, fix=fix)

    # Overlap -------------------------------------------------------------------------

    def assembled(self, a: Solid, b: Solid) -> bool:
        """Parts of one object, or one resting on the other: not an overlap problem."""
        anc_a, anc_b = self.ancestors(a.node.id), self.ancestors(b.node.id)
        if a.node.id in anc_b or b.node.id in anc_a:
            return True
        common = next((n for n in anc_a if n in anc_b), None)
        return common is not None and isinstance(self.nodes[common], (GroupNode, PrefabNode))

    def allowed_fraction(self, a: Solid, b: Solid) -> float:
        best = self.rules.overlap_fraction
        cats = (a.category or "", b.category or "")
        for allow in self.rules.allowed_overlaps:
            for x, y in (cats, cats[::-1]):
                if allow.a in x.split() + [x] and allow.b in y.split() + [y]:
                    best = max(best, allow.max_fraction)
        ids_a, ids_b = {a.node.id, a.owner}, {b.node.id, b.owner}
        for rel in self.world.relations:
            if rel.rel == "allow_overlap" and (
                (rel.a in ids_a and rel.b in ids_b) or (rel.a in ids_b and rel.b in ids_a)
            ):
                best = max(best, rel.max_fraction)
        return best

    def overlap(self, a: Solid, b: Solid) -> tuple[float, float]:
        """(share of the smaller footprint, vertical overlap in metres)."""
        vertical = min(a.top, b.top) - max(a.base, b.base)
        if vertical <= 0:
            return 0.0, vertical
        inter = _clip(a.footprint, b.footprint)
        if len(inter) < 3:
            return 0.0, vertical
        smaller = min(_area(a.footprint), _area(b.footprint)) or 1e-9
        return _area(inter) / smaller, vertical

    def check_overlaps(self) -> None:
        solids = [s for s in self.solids if s.height > self.rules.floor_covering_max_height]
        for i, a in enumerate(solids):
            for b in solids[i + 1 :]:
                if a.owner == b.owner and a.owner != a.node.id and b.owner != b.node.id:
                    continue  # two parts of the same prefab instance
                if self.assembled(a, b):
                    continue
                share, vertical = self.overlap(a, b)
                if share <= self.allowed_fraction(a, b) or vertical <= self.rules.overlap_height:
                    continue
                mover, still = (a, b) if _area(a.footprint) <= _area(b.footprint) else (b, a)
                if self.protected(mover) and not self.protected(still):
                    mover, still = still, mover
                dx, dz = _escape(_bbox(mover.footprint), _bbox(still.footprint))
                message = (
                    f"{a.id} and {b.id} overlap: {share:.0%} of the smaller footprint, "
                    f"{vertical:.2f} m deep vertically."
                )
                w = self.waiver(a.node.id, "overlap") or self.waiver(b.node.id, "overlap")
                self.emit(
                    "overlap",
                    "error",
                    mover,
                    message,
                    amount=share,
                    other=still.owner,
                    fix=self.move_fix(mover, dx, 0.0, dz, whole=True),
                    waived_by=w,
                )

    # Walls and bounds -------------------------------------------------------------------

    def check_walls(self, s: Solid) -> None:
        room = self.room_of(s.node.id)
        if room is None:
            return
        outline = self.rooms[room]["outline"]
        worst, push = 0.0, (0.0, 0.0)
        for x, z in s.footprint:
            if point_in_polygon(x, z, outline):
                continue
            best = (math.inf, None, None)
            for a, b in zip(outline, outline[1:] + outline[:1]):
                d, _ = segment_distance(x, z, a, b)
                if d < best[0]:
                    best = (d, a, b)
            d, a, b = best
            if d > worst:
                ex, ez = b[0] - a[0], b[1] - a[1]
                n = math.hypot(ex, ez) or 1.0
                worst, push = d, (-ez / n, ex / n)  # inward normal of a counter-clockwise outline
        if worst <= self.rules.wall_tolerance:
            return
        step = worst + 0.01
        message = f"{s.id} pokes {worst:.2f} m through a wall of {room}."
        fix = self.move_fix(s, push[0] * step, 0.0, push[1] * step, whole=True)
        self.emit("inside_wall", "error", s, message, amount=worst, other=room, fix=fix, waived_by=None)

    def check_bounds(self, s: Solid) -> None:
        if not self.has_terrain or self.room_of(s.node.id) is not None:
            return
        if self.lw.ground_height(*s.center) is None:
            self.emit("out_of_bounds", "error", s, f"{s.id} stands outside the terrain, where there is no ground.")

    # Clearance zones ----------------------------------------------------------------------

    def check_clearance(self) -> None:
        zones = [
            i
            for i in self.scene.items
            if i.type == "zone" and (i.role == "door_clearance" or i.purpose in ("clearance", "walkway"))
        ]
        for zone in zones:
            poly = [tuple(p) for p in zone.polygon]
            what = zone.label or zone.id
            for s in self.solids:
                if s.height <= self.rules.floor_covering_max_height:
                    continue
                if min(s.top, zone.y_range[1]) - max(s.base, zone.y_range[0]) <= 0:
                    continue
                inter = _clip(s.footprint, poly)
                if len(inter) < 3 or _area(inter) < 0.01:
                    continue
                message = f"{s.id} blocks the {what} ({_area(inter):.2f} m² inside it)."
                waived = self.waiver(s.node.id, "door_clearance")
                if waived is None and self.asked_for(s.node.id):
                    self.emit("door_clearance", "ask", s, message + " It may be on purpose: ask the user.")
                    continue
                dx, dz = _escape(_bbox(s.footprint), _bbox(poly))
                self.emit(
                    "door_clearance",
                    "error",
                    s,
                    message,
                    amount=_area(inter),
                    other=zone.node,
                    fix=self.move_fix(s, dx, 0.0, dz, whole=True),
                    waived_by=waived,
                )

    # Scale -----------------------------------------------------------------------------

    def check_scale(self, s: Solid) -> None:
        if s.item.type != "asset" or s.category is None or s.tilt > 5:
            return
        typical = TYPICAL_HEIGHT.get(s.category) or TYPICAL_HEIGHT.get(s.category.split()[-1])
        if typical is None:
            return
        ratio = s.height / typical
        lo, hi = self.rules.scale_ratio
        if lo <= ratio <= hi:
            return
        message = f"{s.id} is {s.height:.2f} m tall, {ratio:.1f}× a typical {s.category} ({typical} m)."
        scale = s.node.xform.scale if isinstance(s.node.xform.scale, (int, float)) else 1.0
        node_id = self.target(s, whole=False)
        fix = {"action": "scale", "id": node_id, "scale": round(scale / ratio, 3)} if node_id == s.node.id else None
        self.emit("scale", "error", s, message, amount=ratio, fix=fix, waived_by=self.waiver(s.node.id, "scale"))

    # Relations ---------------------------------------------------------------------------

    def footprint_of(self, node_id: str) -> Optional[list[tuple[float, float]]]:
        if node_id in self.rooms:
            return self.rooms[node_id]["outline"]
        node = self.nodes.get(node_id)
        if isinstance(node, ZoneNode):
            return _ccw(list(self.lw.zone_world[node_id]))
        solids = self.by_node.get(node_id)
        if solids:
            pts = [p for s in solids for p in s.footprint]
            x0, z0, x1, z1 = _bbox(pts)
            return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
        items = [i for i in self.items_by_node.get(node_id, []) if getattr(i, "aabb", None)]
        if items:
            x0 = min(i.aabb[0][0] for i in items)
            z0 = min(i.aabb[0][2] for i in items)
            x1 = max(i.aabb[1][0] for i in items)
            z1 = max(i.aabb[1][2] for i in items)
            return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
        if node_id in self.lw.node_world:
            p = apply(self.lw.node_world[node_id], (0.0, 0.0, 0.0))
            return [(p[0], p[2])] * 3
        return None

    def main_solid(self, node_id: str) -> Optional[Solid]:
        solids = self.by_node.get(node_id)
        return solids[0] if solids else None

    def check_relations(self, checks: set[str]) -> None:
        facing = {"faces", "faces_away", "faces_direction"}
        for index, rel in enumerate(self.world.relations):
            ref = rel.id or f"relations[{index}]"
            kind = rel.rel
            if kind in facing and "facing" not in checks:
                continue
            if kind not in facing and "relations" not in checks:
                continue
            if kind in UNCHECKED_RELATIONS:
                self.unchecked.add(f"relation:{kind}")
                continue
            handler = getattr(self, f"rel_{kind}", None)
            if handler is None:
                if kind != "allow_overlap":
                    self.unchecked.add(f"relation:{kind}")
                continue
            problem = handler(rel)
            if problem:
                message, node, amount, fix = problem
                self.emit(
                    f"relation:{kind}",
                    "error" if rel.hard else "warning",
                    None,
                    message + (f" ({rel.note})" if rel.note else ""),
                    node=node,
                    amount=amount,
                    relation=ref,
                    fix=fix,
                )

    def distance(self, a: str, b: str) -> Optional[float]:
        fa, fb = self.footprint_of(a), self.footprint_of(b)
        if fa is None or fb is None:
            return None
        return _poly_distance(fa, fb)

    def rel_on(self, rel: Any):
        s = self.main_solid(rel.a)
        top = self.surface_height(rel.b, rel.surface)
        if s is None or top is None:
            return None
        gap = s.base - top
        fb = self.footprint_of(rel.b)
        outside = fb is not None and not point_in_polygon(s.center[0], s.center[1], _ccw(fb))
        if abs(gap) <= self.tolerance(self.rules.float_tolerance, s) and not outside:
            return None
        if not outside and self.deliberately_off(s, gap):
            return None
        where = f"{rel.b}.{rel.surface}" if rel.surface else rel.b
        msg = f"{rel.a} should rest on {where}" + (
            " but is not over it." if outside else f" but is {gap:+.2f} m off it."
        )
        return msg, rel.a, abs(gap), self.move_fix(s, 0.0, -gap, 0.0) if not outside else None

    def rel_on_floor(self, rel: Any):
        s = self.main_solid(rel.a)
        if s is None:
            return None
        room = self.room_of(s.node.id)
        floor = self.rooms[room]["floor"] if room else (self.lw.ground_height(*s.center) or 0.0)
        gap = s.base - floor
        if abs(gap) <= self.tolerance(self.rules.float_tolerance, s) or self.deliberately_off(s, gap):
            return None
        return (
            f"{rel.a} should stand on the floor but is {gap:+.2f} m off it.",
            rel.a,
            abs(gap),
            self.move_fix(s, 0.0, -gap, 0.0),
        )

    def rel_near(self, rel: Any):
        d = self.distance(rel.a, rel.b)
        if d is None or d <= rel.max:
            return None
        return f"{rel.a} should be within {rel.max} m of {rel.b}, but is {d:.2f} m away.", rel.a, d - rel.max, None

    def rel_adjacent(self, rel: Any):
        d = self.distance(rel.a, rel.b)
        if d is None or d <= rel.max_gap:
            return None
        return f"{rel.a} should sit next to {rel.b} (gap ≤ {rel.max_gap} m), but the gap is {d:.2f} m.", rel.a, d, None

    def rel_far_from(self, rel: Any):
        d = self.distance(rel.a, rel.b)
        if d is None or d >= rel.min:
            return None
        return f"{rel.a} should be at least {rel.min} m from {rel.b}, but is {d:.2f} m away.", rel.a, rel.min - d, None

    def rel_distance(self, rel: Any):
        if rel.measure == "center":
            fa, fb = self.footprint_of(rel.a), self.footprint_of(rel.b)
            if fa is None or fb is None:
                return None
            ca = (sum(p[0] for p in fa) / len(fa), sum(p[1] for p in fa) / len(fa))
            cb = (sum(p[0] for p in fb) / len(fb), sum(p[1] for p in fb) / len(fb))
            d = math.dist(ca, cb)
        else:
            d = self.distance(rel.a, rel.b)
        if d is None or rel.range[0] <= d <= rel.range[1]:
            return None
        return f"{rel.a} should be {rel.range[0]}–{rel.range[1]} m from {rel.b}, but is {d:.2f} m.", rel.a, d, None

    def rel_inside_region(self, rel: Any):
        s, region = self.main_solid(rel.a), self.footprint_of(rel.region)
        if s is None or region is None:
            return None
        outside = [p for p in s.footprint if not point_in_polygon(p[0], p[1], _ccw(region))]
        if not outside:
            return None
        return (
            f"{rel.a} should lie inside {rel.region}, but {len(outside)} of its corners are outside.",
            rel.a,
            None,
            None,
        )

    def wall_segments(self, room: str, edge: Optional[int]):
        outline = self.rooms[room]["outline"]
        node_outline = self.rooms[room]["node"].outline
        segs = list(zip(outline, outline[1:] + outline[:1]))
        if edge is None:
            return segs
        # The stored outline may have been reversed to run counter-clockwise; find the edge by its points.
        m = self.lw.node_world[room]
        a = apply(m, (node_outline[edge][0], 0.0, node_outline[edge][1]))
        b = apply(
            m, (node_outline[(edge + 1) % len(node_outline)][0], 0.0, node_outline[(edge + 1) % len(node_outline)][1])
        )
        return [((a[0], a[2]), (b[0], b[2]))]

    def rel_against_wall(self, rel: Any):
        s = self.main_solid(rel.a)
        if s is None:
            return None
        if rel.wall:
            room, edge = rel.wall.split(":")
            edge = int(edge)
        else:
            room, edge = self.room_of(s.node.id), None
        if room not in self.rooms:
            return None
        w, _, d = s.size
        local = {
            "back": [(-w / 2, -d / 2), (w / 2, -d / 2)],
            "front": [(-w / 2, d / 2), (w / 2, d / 2)],
            "left": [(w / 2, -d / 2), (w / 2, d / 2)],
            "right": [(-w / 2, -d / 2), (-w / 2, d / 2)],
        }[rel.side]
        points = [apply(s.m, (x, 0.0, z)) for x, z in local]
        best = math.inf
        for a, b in self.wall_segments(room, edge):
            best = min(best, max(segment_distance(p[0], p[2], a, b)[0] for p in points))
        if best <= rel.max_gap + self.rules.wall_tolerance:
            return None
        where = rel.wall or f"a wall of {room}"
        return f"{rel.a}'s {rel.side} should be against {where}, but is {best:.2f} m from it.", rel.a, best, None

    def rel_faces(self, rel: Any, away: bool = False):
        s, fb = self.main_solid(rel.a), self.footprint_of(rel.b)
        if s is None or fb is None:
            return None
        cb = (sum(p[0] for p in fb) / len(fb), sum(p[1] for p in fb) / len(fb))
        to = (cb[0] - s.center[0], cb[1] - s.center[1])
        if away:
            to = (-to[0], -to[1])
        off = _angle(s.front, to)
        if off <= rel.tolerance_deg:
            return None
        fix = self.rotate_fix(s, math.degrees(math.atan2(to[0], to[1])))
        verb = "face away from" if away else "face"
        return f"{rel.a} should {verb} {rel.b}, but is turned {off:.0f}° off.", rel.a, off, fix

    def rel_faces_away(self, rel: Any):
        return self.rel_faces(rel, away=True)

    def rel_faces_direction(self, rel: Any):
        s = self.main_solid(rel.a)
        if s is None or rel.yaw is None:
            if rel.away_from_wall:
                self.unchecked.add("relation:faces_direction(away_from_wall)")
            return None
        want = (math.sin(math.radians(rel.yaw)), math.cos(math.radians(rel.yaw)))
        off = _angle(s.front, want)
        if off <= rel.tolerance_deg:
            return None
        return (
            f"{rel.a} should face yaw {rel.yaw:.0f}°, but is {off:.0f}° off.",
            rel.a,
            off,
            self.rotate_fix(s, rel.yaw),
        )

    def rel_upright(self, rel: Any):
        s = self.main_solid(rel.a)
        if s is None or s.tilt <= rel.tolerance_deg:
            return None
        return f"{rel.a} should be upright, but is tilted {s.tilt:.0f}°.", rel.a, s.tilt, None

    def rel_centered(self, rel: Any):
        fa, fw = self.footprint_of(rel.a), self.footprint_of(rel.within)
        if fa is None or fw is None:
            return None
        ca = (sum(p[0] for p in fa) / len(fa), sum(p[1] for p in fa) / len(fa))
        bx0, bz0, bx1, bz1 = _bbox(fw)
        cw = ((bx0 + bx1) / 2, (bz0 + bz1) / 2)
        off = max(abs(ca[0] - cw[0]) if "x" in rel.axes else 0.0, abs(ca[1] - cw[1]) if "z" in rel.axes else 0.0)
        if off <= rel.tolerance:
            return None
        return f"{rel.a} should be centred in {rel.within}, but is {off:.2f} m off centre.", rel.a, off, None

    def rel_hangs_from_ceiling(self, rel: Any):
        s = self.main_solid(rel.a)
        room = self.room_of(rel.a) if s else None
        if s is None or room is None:
            return None
        drop = self.rooms[room]["ceiling"] - s.top
        lo, hi = rel.drop or (0.0, self.rules.float_tolerance)
        if lo - 1e-6 <= drop <= hi + 1e-6:
            return None
        return f"{rel.a} should hang {lo}–{hi} m below the ceiling, but hangs {drop:.2f} m below it.", rel.a, drop, None

    def blockers(self, poly: list[tuple[float, float]], y_range: tuple[float, float], skip: set[str]) -> list[Solid]:
        out = []
        for s in self.solids:
            if s.owner in skip or s.node.id in skip or s.height <= self.rules.floor_covering_max_height:
                continue
            if min(s.top, y_range[1]) - max(s.base, y_range[0]) <= 0:
                continue
            inter = _clip(s.footprint, poly)
            if len(inter) >= 3 and _area(inter) >= 0.01:
                out.append(s)
        return out

    def rel_clear(self, rel: Any):
        if rel.zone:
            zone = next((i for i in self.items_by_node.get(rel.zone, []) if i.type == "zone"), None)
            if zone is None:
                return None
            hits = self.blockers([tuple(p) for p in zone.polygon], tuple(zone.y_range), set())
            if not hits:
                return None
            names = ", ".join(sorted({h.owner for h in hits}))
            return (
                f"{rel.zone} should stay clear, but {names} {'is' if len(hits) == 1 else 'are'} in it.",
                hits[0].owner,
                len(hits),
                None,
            )
        if rel.around:
            s = self.main_solid(rel.around)
            if s is None:
                return None
            w, _, d = s.size
            band = []
            for side in rel.sides:
                corners = {
                    "front": ((-w / 2, d / 2), (w / 2, d / 2 + rel.margin)),
                    "back": ((-w / 2, -d / 2 - rel.margin), (w / 2, -d / 2)),
                    "left": ((w / 2, -d / 2), (w / 2 + rel.margin, d / 2)),
                    "right": ((-w / 2 - rel.margin, -d / 2), (-w / 2, d / 2)),
                }[side]
                (x0, z0), (x1, z1) = corners
                pts = [apply(s.m, (x, 0.0, z)) for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))]
                band.append([(p[0], p[2]) for p in pts])
            hits = [h for poly in band for h in self.blockers(poly, (s.base, s.base + 2.0), {s.owner, s.node.id})]
            if not hits:
                return None
            names = ", ".join(sorted({h.owner for h in hits}))
            return (
                f"The space around {rel.around} ({', '.join(rel.sides)}) should stay clear, but {names} is in it.",
                hits[0].owner,
                len(hits),
                None,
            )
        return None

    def rel_not_blocking(self, rel: Any):
        zone = next(
            (i for i in self.scene.items if i.role == "door_clearance" and i.id.endswith(f"/{rel.opening}/clearance")),
            None,
        )
        if zone is None:
            return None
        hits = [
            h
            for h in self.blockers([tuple(p) for p in zone.polygon], tuple(zone.y_range), set())
            if h.owner == rel.a or h.node.id == rel.a
        ]
        if not hits:
            return None
        return f"{rel.a} should not block {rel.opening}, but stands in its clearance zone.", rel.a, None, None

    def rel_no_overlap(self, rel: Any):
        mine = self.by_node.get(rel.a, [])
        others = self.by_node.get(rel.b, []) if rel.b else [s for s in self.solids if s.owner != rel.a]
        for a in mine:
            for b in others:
                share, vertical = self.overlap(a, b)
                if share > self.rules.overlap_fraction and vertical > self.rules.overlap_height:
                    return f"{rel.a} must not overlap {b.owner}, but does ({share:.0%}).", rel.a, share, None
        # Generated geometry (sweeps, roofs, terrain-following meshes): test its real vertices.
        if rel.b and (not mine or not others):
            if self._mesh_hits(rel.a, rel.b) or self._mesh_hits(rel.b, rel.a):
                return f"{rel.a} must not overlap {rel.b}, but they intersect.", rel.a, None, None
        return None

    def _points(self, node_id: str) -> list[tuple[float, float, float]]:
        """Mesh vertices and box corners of everything lowered from a node."""
        out = []
        for item in self.items_by_node.get(node_id, []):
            m = _mat(item.matrix)
            if item.type == "mesh":
                out += [apply(m, tuple(item.positions[i : i + 3])) for i in range(0, len(item.positions), 3)]
            elif item.type in ("shape", "asset"):
                w, h, d = item.size if item.type == "shape" else self.scene.assets[item.asset].dims
                out += [apply(m, (x, y, z)) for x in (-w / 2, w / 2) for y in (0.0, h) for z in (-d / 2, d / 2)]
        return out

    def _boxes(self, node_id: str) -> list[tuple[tuple[float, ...], tuple[float, ...]]]:
        """World boxes of a node's solid parts. Meshes are left out: a curved beam's box would cover the whole loop."""
        return [i.aabb for i in self.items_by_node.get(node_id, []) if getattr(i, "aabb", None) and i.type != "mesh"]

    def _mesh_hits(self, a: str, b: str) -> bool:
        """True when a vertex of a's geometry lies inside one of b's solid parts."""
        e = 0.01
        boxes = self._boxes(b)
        return any(all(lo[k] + e < p[k] < hi[k] - e for k in range(3)) for p in self._points(a) for lo, hi in boxes)

    def rel_max_slope(self, rel: Any):
        poly = self.footprint_of(rel.region)
        if poly is None or not self.has_terrain:
            return None
        x0, z0, x1, z1 = _bbox(poly)
        worst = 0.0
        for i in range(9):
            for j in range(9):
                x, z = x0 + (x1 - x0) * i / 8, z0 + (z1 - z0) * j / 8
                if point_in_polygon(x, z, _ccw(poly)):
                    worst = max(worst, self.lw.slope_at(x, z))
        if worst <= rel.max_deg:
            return None
        return (
            f"The ground in {rel.region} slopes up to {worst:.0f}°, more than {rel.max_deg:.0f}°.",
            rel.region,
            worst,
            None,
        )

    # Counting -----------------------------------------------------------------------------

    def counted(self, category: str, region: Optional[str]) -> int:
        category = category.lower()
        poly = _ccw(self.footprint_of(region)) if region and self.footprint_of(region) else None
        if region and poly is None:
            # A region named by room type, e.g. 'bedroom'.
            room = next((r for r in self.rooms.values() if r["node"].room_type == region), None)
            poly = room["outline"] if room else None

        def matches(cat: Optional[str]) -> bool:
            return bool(cat) and (cat == category or category in cat.split() or cat.endswith(" " + category))

        n = 0
        for node_id, node in self.nodes.items():
            if not matches(self._category(node)):
                continue
            if node_id in self.by_node:
                x, z = self.by_node[node_id][0].center
            else:
                x, _, z = apply(self.lw.node_world[node_id], (0.0, 0.0, 0.0))
            if poly is None or point_in_polygon(x, z, poly):
                n += 1
        for item in self.scene.items:
            if item.type == "instances" and item.asset and matches(self.world.assets[item.asset].category.lower()):
                n += sum(1 for t in item.transforms if poly is None or point_in_polygon(t[0], t[2], poly))
        return n

    def rel_count(self, rel: Any):
        n = self.counted(rel.category, rel.region)
        if n < rel.min or (rel.max is not None and n > rel.max):
            want = f"{rel.min}–{rel.max}" if rel.max is not None else f"at least {rel.min}"
            return (
                f"Expected {want} {rel.category} {'in ' + rel.region if rel.region else ''}, found {n}.".replace(
                    "  ", " "
                ),
                rel.region or self.world.id,
                n,
                None,
            )
        return None

    def rel_requires(self, rel: Any):
        if self.counted(rel.category, rel.region) == 0:
            return (
                f"The world needs a {rel.category}{' in ' + rel.region if rel.region else ''}, and has none.",
                rel.region or self.world.id,
                0,
                None,
            )
        return None

    def rel_forbids(self, rel: Any):
        n = self.counted(rel.category, rel.region)
        if n:
            return f"The world must not contain {rel.category}, but has {n}.", rel.region or self.world.id, n, None
        return None

    def check_requirements(self) -> None:
        brief = self.world.brief
        if brief is None:
            return
        for req in brief.required:
            n = self.counted(req.category, None)
            if n < req.min or (req.max is not None and n > req.max):
                self.emit(
                    "requirement",
                    "error" if req.source in ("prompt", "user") else "warning",
                    None,
                    f"The brief asks for {req.min}{'–' + str(req.max) if req.max is not None else '+'} {req.category}; found {n}.",
                    node=self.world.id,
                    amount=n,
                )
