"""Generator lowering: parametric generators → simple shapes, meshes and instances.

Mixed into the lowerer in lower.py, which provides _shape, _mesh, _add, materials,
_wall, ground_height and the node's world matrix.
"""

import math
import random
from typing import Any

from .architecture import Opening
from .generators import (
    GenAlongPath,
    GenArray,
    GenBuilding,
    GenBush,
    GenColumn,
    GenFence,
    GenFlowerBed,
    GenGrass,
    GenKitchenRun,
    GenPlatform,
    GenRadialArray,
    GenRailing,
    GenRamp,
    GenRock,
    GenRoof,
    GenRug,
    GenShelving,
    GenStairs,
    GenTableSet,
    GenTree,
    GenWallRun,
)
from .geometry import (
    Mat,
    apply,
    box_aabb,
    bounds,
    column_major,
    identity,
    inverse,
    mul,
    roof_mesh,
    rock_mesh,
    rot_y,
    rot_z,
    scale,
    translate,
    yaw_of,
)
from .lowered import LAssetItem, LInstances, LMesh, LSlab, Unsupported
from .terrain_eval import point_in_polygon, polyline

LEAF_COLORS = {
    "conifer": {"spring": "#356b3a", "summer": "#2f5a32", "autumn": "#2f5a32", "winter": "#3c5f45"},
    "deciduous": {"spring": "#86b35a", "summer": "#4f7d32", "autumn": "#c2702e"},
    "fruit": {"spring": "#9cc46a", "summer": "#5a8a36", "autumn": "#b8862e"},
    "willow": {"spring": "#9cc260", "summer": "#7aa04a", "autumn": "#c9a33a"},
    "birch": {"spring": "#9cc260", "summer": "#6d9b3c", "autumn": "#e0b13a"},
    "palm": {"spring": "#4f8a3a", "summer": "#4f8a3a", "autumn": "#6d8a3a", "winter": "#4f8a3a"},
}


def _polygon_area(poly: list[tuple[float, float]]) -> float:
    return (
        abs(
            sum(
                poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                for i in range(len(poly))
            )
        )
        / 2
    )


def _resample(points: list[tuple[float, float, float]], spacing: float) -> list[tuple[float, float, float]]:
    """Points every `spacing` metres along a polyline, always including both ends."""
    out = [points[0]]
    carried = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.dist((a[0], a[2]), (b[0], b[2]))
        pos = spacing - carried
        while pos <= seg - 1e-9:
            t = pos / seg
            out.append(tuple(a[c] + (b[c] - a[c]) * t for c in range(3)))
            pos += spacing
        carried = (carried + seg) % spacing if seg else carried
    if math.dist(out[-1], points[-1]) > spacing * 0.3:
        out.append(points[-1])
    return out


class GeneratorMixin:
    """Lowering for every generator. Each method emits items for one generator node."""

    def _generator(self, node: Any, m: Mat, parent: Mat, visible: bool) -> None:
        g = node.generator
        handlers = {
            GenRug: self._rug,
            GenWallRun: self._wall_run,
            GenArray: self._array,
            GenPlatform: self._platform,
            GenBuilding: self._building,
            GenRoof: self._roof_gen,
            GenColumn: self._column,
            GenRamp: self._ramp,
            GenRailing: self._railing,
            GenFence: self._fence,
            GenTree: self._tree,
            GenRock: self._rock,
            GenBush: self._bush,
            GenGrass: self._grass,
            GenFlowerBed: self._flower_bed,
            GenAlongPath: self._along_path,
            GenRadialArray: self._radial_array,
            GenTableSet: self._table_set,
            GenShelving: self._shelving,
            GenKitchenRun: self._kitchen_run,
        }
        if isinstance(g, GenStairs):
            if g.shape == "straight":
                self._stairs(node, g, m, visible)
                return
            handler = None
        else:
            handler = handlers.get(type(g))
        if handler is None:
            detail = f" ({g.shape})" if isinstance(g, GenStairs) else ""
            self.unsupported.append(
                Unsupported(node=node.id, kind=f"generator:{g.gen}{detail}", reason="not lowered in this version")
            )
            return
        handler(node, g, m, visible)

    # Helpers

    def _box(
        self, node: Any, name: str, role: str, size: tuple, m: Mat, material: str, visible: bool, **extra: Any
    ) -> None:
        self._shape(f"{node.id}/{name}", self._ref(node.id), role, "box", size, m, material, visible, **extra)

    def _mesh(
        self,
        node: Any,
        name: str,
        role: str,
        mesh: tuple,
        m: Mat,
        material: str,
        visible: bool,
        flat: bool = True,
        double_sided: bool = False,
    ) -> None:
        positions, indices = mesh
        corners = [apply(m, tuple(positions[i : i + 3])) for i in range(0, len(positions), 3)]
        self._add(
            LMesh(
                id=f"{node.id}/{name}" if name else node.id,
                node=self._ref(node.id),
                role=role,
                matrix=column_major(m),
                visible=visible,
                aabb=bounds(corners),
                positions=positions,
                indices=indices,
                material=material,
                flat=flat,
                double_sided=double_sided,
            )
        )

    def _instances(
        self, node: Any, name: str, role: str, transforms: list, visible: bool, m: Mat, box: tuple, **base: Any
    ) -> None:
        """Many copies; box is one copy's [w, h, d] at scale 1, used for the bounding box."""
        if not transforms:
            return
        corners = []
        for x, y, z, _, s in transforms:
            r = max(box[0], box[2]) * s / 2
            corners += [apply(m, (x - r, y, z - r)), apply(m, (x + r, y + box[1] * s, z + r))]
        self._add(
            LInstances(
                id=f"{node.id}/{name}",
                node=self._ref(node.id),
                role=role,
                matrix=column_major(m),
                visible=visible,
                aabb=bounds(corners),
                transforms=[tuple(round(v, 4) for v in t) for t in transforms],
                **base,
            )
        )

    def _local_ground(self, m: Mat, x: float, z: float, fallback: float = 0.0) -> float:
        """Ground height under local (x, z), returned in the same local space."""
        wx, wy, wz = apply(m, (x, 0.0, z))
        ground = self.ground_height(wx, wz)
        if ground is None:
            return fallback
        return apply(inverse(m), (wx, ground, wz))[1]

    def _segment(
        self,
        node: Any,
        name: str,
        role: str,
        a: tuple,
        b: tuple,
        thickness: float,
        height: float,
        m: Mat,
        material: str,
        visible: bool,
    ) -> None:
        """A box from point a to point b (centres of its bottom edge), following any slope between them."""
        dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        flat_len = math.hypot(dx, dz)
        if flat_len < 1e-6:
            return
        length = math.hypot(flat_len, dy)
        pitch = math.degrees(math.atan2(dy, flat_len))
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
        sm = mul(m, mul(translate(*mid), mul(rot_y(yaw_of(dx, dz)), rot_z(pitch))))
        self._box(node, name, role, (length, height, thickness), sm, material, visible)

    # Version 1 generators

    def _rug(self, node: Any, g: GenRug, m: Mat, visible: bool) -> None:
        width, depth = g.size
        shape = "cylinder" if g.shape in ("round", "oval") else "box"
        if g.shape == "round":
            depth = width
        ref = self._ref(node.id)
        if g.pattern == "bordered" and len(g.colors) > 1:
            self._shape(
                f"{node.id}/border",
                ref,
                "rug",
                shape,
                (width, g.thickness, depth),
                m,
                self._color(g.colors[1], 1.0),
                visible,
            )
            inset = min(0.12, width / 6, depth / 6)
            size = (width - 2 * inset, g.thickness + 0.002, depth - 2 * inset)
            self._shape(f"{node.id}/field", ref, "rug", shape, size, m, self._color(g.colors[0], 1.0), visible)
        else:
            self._shape(
                node.id, ref, "rug", shape, (width, g.thickness, depth), m, self._color(g.colors[0], 1.0), visible
            )

    def _stairs(self, node: Any, g: GenStairs, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "wood_oak")
        for i in range(g.steps):
            sm = mul(m, translate(0.0, 0.0, g.run * (i + 0.5)))
            self._box(node, f"step{i}", "step", (g.width, g.rise * (i + 1), g.run), sm, material, visible)
        sides = {"left": [1.0], "right": [-1.0], "both": [1.0, -1.0], "none": []}[g.railing]
        rail = self._preset("metal_black")
        for side in sides:
            x = side * (g.width / 2 - 0.04)
            name = "left" if side > 0 else "right"
            a = (x, g.rise + 0.9, g.run * 0.5)
            b = (x, g.rise * g.steps + 0.9, g.run * (g.steps - 0.5))
            self._segment(node, f"rail_{name}", "railing", a, b, 0.05, 0.05, m, rail, visible)
            for j, (z, step) in enumerate(((a[2], 1), (b[2], g.steps))):
                self._box(
                    node,
                    f"post_{name}{j}",
                    "railing",
                    (0.05, 0.9, 0.05),
                    mul(m, translate(x, g.rise * step, z)),
                    rail,
                    visible,
                )

    def _wall_run(self, node: Any, g: GenWallRun, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "plaster_white")
        points = list(g.points) + ([g.points[0]] if g.closed else [])
        for i in range(len(points) - 1):
            openings = [o for o in g.openings if o.wall == i]
            self._wall(
                node.id,
                f"{node.id}/seg{i}",
                m,
                points[i],
                points[i + 1],
                g.height,
                g.thickness,
                material,
                openings,
                1.0,
                "centred",
                visible,
                self.world.rules.door_clearance,
                cutaway=False,
            )

    def _array(self, node: Any, g: GenArray, m: Mat, visible: bool) -> None:
        rng = random.Random(g.seed)
        dims = self.world.assets[g.asset].dims
        transforms = []
        for ix in range(g.count[0]):
            for iy in range(g.count[1]):
                for iz in range(g.count[2]):
                    jitter = [rng.uniform(-j, j) if j else 0.0 for j in g.jitter]
                    yaw = rng.uniform(-g.yaw_jitter_deg, g.yaw_jitter_deg) if g.yaw_jitter_deg else 0.0
                    transforms.append(
                        (
                            ix * g.spacing[0] + jitter[0],
                            iy * g.spacing[1] + jitter[1],
                            iz * g.spacing[2] + jitter[2],
                            yaw,
                            1.0,
                        )
                    )
        self._instances(node, "copies", "asset", transforms, visible, m, dims, asset=g.asset)

    # Architecture

    def _platform(self, node: Any, g: GenPlatform, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "wood_pine")
        deck = min(0.15, g.height) if g.height > 0 else 0.1
        if isinstance(g.footprint, tuple) and len(g.footprint) == 2 and not isinstance(g.footprint[0], (tuple, list)):
            w, d = g.footprint
            corners = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
            if g.legs == "solid":
                self._box(node, "base", "platform", (w, g.height, d), m, material, visible)
            else:
                self._box(
                    node, "deck", "platform", (w, deck, d), mul(m, translate(0, g.height - deck, 0)), material, visible
                )
        else:
            corners = [tuple(p) for p in g.footprint]
            top = mul(m, translate(0, g.height, 0))
            self._add(
                LSlab(
                    id=f"{node.id}/deck", node=self._ref(node.id), role="platform", matrix=column_major(top), visible=visible,
                    aabb=bounds([apply(top, (x, y, z)) for x, z in corners for y in (0.0, -deck)]),
                    polygon=corners, thickness=deck, material=material,
                )
            )  # fmt: skip
        leg = g.height - deck
        if g.legs == "posts" and leg > 0.01:
            inset = 0.12
            ring = corners + [corners[0]]
            k = 0
            for a, b in zip(ring, ring[1:]):
                length = math.dist(a, b)
                n = max(1, math.ceil(length / 2.4))
                for i in range(n):
                    t = i / n
                    x, z = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                    cx = x - math.copysign(inset, x) if abs(x) > inset else x
                    cz = z - math.copysign(inset, z) if abs(z) > inset else z
                    self._box(
                        node, f"post{k}", "platform", (0.14, leg, 0.14), mul(m, translate(cx, 0, cz)), material, visible
                    )
                    k += 1
        if g.railing:
            ring = corners + [corners[0]]
            for i, (a, b) in enumerate(zip(ring, ring[1:])):
                self._segment(
                    node,
                    f"rail{i}",
                    "railing",
                    (a[0], g.height + 0.95, a[1]),
                    (b[0], g.height + 0.95, b[1]),
                    0.06,
                    0.06,
                    m,
                    material,
                    visible,
                )
                length = math.dist(a, b)
                n = max(1, math.ceil(length / 1.2))
                for j in range(n):
                    t = j / n
                    x, z = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
                    self._box(
                        node,
                        f"baluster{i}_{j}",
                        "railing",
                        (0.06, 0.95, 0.06),
                        mul(m, translate(x, g.height, z)),
                        material,
                        visible,
                    )

    def _building(self, node: Any, g: GenBuilding, m: Mat, visible: bool) -> None:
        walls = self._material(g.wall_material, "plaster_white")
        trim = self._material(g.trim_material, "wood_walnut")
        thickness = 0.2
        polygon = not (
            isinstance(g.footprint, tuple) and len(g.footprint) == 2 and not isinstance(g.footprint[0], (tuple, list))
        )
        if polygon:
            outline = [tuple(p) for p in g.footprint]
        else:
            w, d = g.footprint
            outline = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
        lengths = [math.dist(outline[i], outline[(i + 1) % len(outline)]) for i in range(len(outline))]
        longest = max(lengths)
        total = g.floors * g.floor_height
        area = sum(
            outline[i][0] * outline[(i + 1) % len(outline)][1] - outline[(i + 1) % len(outline)][0] * outline[i][1]
            for i in range(len(outline))
        )
        door = g.entrance
        for floor in range(g.floors):
            fm = mul(m, translate(0, floor * g.floor_height, 0))
            for edge in range(len(outline)):
                a, b = outline[edge], outline[(edge + 1) % len(outline)]
                count = g.windows.per_floor if lengths[edge] >= longest - 1e-6 else g.windows.per_floor // 2
                openings = []
                blocked = []
                if floor == 0 and door is not None and door.wall == edge:
                    openings.append(door)
                    blocked.append((door.offset - door.width / 2 - 0.4, door.offset + door.width / 2 + 0.4))
                for i in range(count):
                    offset = lengths[edge] * (i + 1) / (count + 1)
                    lo, hi = offset - g.windows.width / 2, offset + g.windows.width / 2
                    if lo < 0.3 or hi > lengths[edge] - 0.3 or any(lo < bh and hi > bl for bl, bh in blocked):
                        continue
                    if g.windows.sill + g.windows.height > g.floor_height - 0.1:
                        continue
                    openings.append(
                        Opening(
                            id=f"win{floor}_{edge}_{i}",
                            type="window",
                            wall=edge,
                            offset=offset,
                            width=g.windows.width,
                            height=g.windows.height,
                            sill=g.windows.sill,
                        )
                    )
                self._wall(
                    node.id,
                    f"{node.id}/f{floor}/wall{edge}",
                    fm,
                    a,
                    b,
                    g.floor_height,
                    thickness,
                    walls,
                    openings,
                    1.0 if area > 0 else -1.0,
                    "inset",
                    visible,
                    self.world.rules.door_clearance,
                    cutaway=False,
                    clear_outside=True,
                )
        floor_material = self._preset("floor_parquet")
        self._add(
            LSlab(
                id=f"{node.id}/ground_floor", node=self._ref(node.id), role="floor", matrix=column_major(mul(m, translate(0, 0.12, 0))), visible=visible,
                aabb=bounds([apply(m, (x, y, z)) for x, z in outline for y in (0.0, 0.12)]), polygon=outline, thickness=0.12, material=floor_material,
            )
        )  # fmt: skip
        for i, (x, z) in enumerate(outline):
            self._box(node, f"trim{i}", "trim", (0.24, total, 0.24), mul(m, translate(x, 0, z)), trim, visible)
        roof_material = (
            self._material(g.roof.material, "wood_walnut") if g.roof.material else self._color("#4a3a33", 0.9)
        )
        if polygon:
            self.warnings.append(f"building '{node.id}': polygon footprints get a flat roof")
            top = mul(m, translate(0, total + 0.2, 0))
            self._add(
                LSlab(
                    id=f"{node.id}/roof", node=self._ref(node.id), role="roof", matrix=column_major(top), visible=visible,
                    aabb=bounds([apply(top, (x, y, z)) for x, z in outline for y in (0.0, -0.2)]), polygon=outline, thickness=0.2, material=roof_material,
                )
            )  # fmt: skip
            return
        self._roof(node, "roof", g.roof, w, d, mul(m, translate(0, total, 0)), roof_material, visible)

    def _roof(self, node: Any, name: str, spec: Any, w: float, d: float, m: Mat, material: str, visible: bool) -> None:
        along_x = spec.ridge_axis == "x" or (spec.ridge_axis == "auto" and w > d)
        if along_x:
            m = mul(m, rot_y(90))
            w, d = d, w
        mesh, built = roof_mesh(spec.style, w, d, spec.pitch_deg, spec.overhang)
        if built != spec.style:
            self.warnings.append(f"'{node.id}': {spec.style} roofs are drawn as {built}")
        self._mesh(node, name, "roof", mesh, m, material, visible, flat=True, double_sided=True)

    def _roof_gen(self, node: Any, g: GenRoof, m: Mat, visible: bool) -> None:
        material = self._material(g.roof.material, "wood_walnut") if g.roof.material else self._color("#4a3a33", 0.9)
        if not (
            isinstance(g.footprint, tuple) and len(g.footprint) == 2 and not isinstance(g.footprint[0], (tuple, list))
        ):
            self.warnings.append(f"roof '{node.id}': polygon footprints are not built yet")
            return
        self._roof(node, "", g.roof, g.footprint[0], g.footprint[1], m, material, visible)

    def _column(self, node: Any, g: GenColumn, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "stone_grey")
        ref = self._ref(node.id)
        shape, segments = (
            ("box", None) if g.profile == "square" else ("cylinder", 8 if g.profile == "octagonal" else 20)
        )
        foot = 0.12 if g.base else 0.0
        head = 0.12 if g.capital != "none" else 0.0
        if foot:
            self._shape(
                f"{node.id}/base", ref, "column", "box", (g.width * 1.4, foot, g.width * 1.4), m, material, visible
            )
        self._shape(
            f"{node.id}/shaft",
            ref,
            "column",
            shape,
            (g.width, g.height - foot - head, g.width),
            mul(m, translate(0, foot, 0)),
            material,
            visible,
            segments=segments,
        )
        if head:
            self._shape(
                f"{node.id}/capital",
                ref,
                "column",
                "box",
                (g.width * 1.35, head, g.width * 1.35),
                mul(m, translate(0, g.height - head, 0)),
                material,
                visible,
            )

    def _ramp(self, node: Any, g: GenRamp, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "concrete")
        self._shape(node.id, self._ref(node.id), "ramp", "wedge", (g.width, g.rise, g.length), m, material, visible)

    def _railing(self, node: Any, g: GenRailing, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "metal_black")
        points = [tuple(p) for p in g.points]
        for i, (a, b) in enumerate(zip(points, points[1:])):
            self._segment(
                node,
                f"rail{i}",
                "railing",
                (a[0], a[1] + g.height, a[2]),
                (b[0], b[1] + g.height, b[2]),
                0.05,
                0.05,
                m,
                material,
                visible,
            )
            for j, p in enumerate(_resample([a, b], 1.2)[:-1]):
                self._box(
                    node, f"post{i}_{j}", "railing", (0.04, g.height, 0.04), mul(m, translate(*p)), material, visible
                )
        self._box(
            node, "post_end", "railing", (0.04, g.height, 0.04), mul(m, translate(*points[-1])), material, visible
        )

    def _fence(self, node: Any, g: GenFence, m: Mat, visible: bool) -> None:
        if g.path:
            inv = inverse(m)
            line = [apply(inv, p) for p in self.path_world.get(g.path, [])]
        elif g.points:
            line = [(x, 0.0, z) for x, z in g.points]
        else:
            self.warnings.append(f"fence '{node.id}' has no points or path")
            return
        if len(line) < 2:
            return
        posts = _resample(line, g.post_spacing)
        posts = [(x, self._local_ground(m, x, z, 0.0), z) for x, _, z in posts]
        # Distance of each post along the fence, to leave gaps for gates.
        along = [0.0]
        for a, b in zip(posts, posts[1:]):
            along.append(along[-1] + math.dist((a[0], a[2]), (b[0], b[2])))
        gaps = [(s - g.gate_width / 2, s + g.gate_width / 2) for s in g.gates]
        in_gap = lambda s: any(lo <= s <= hi for lo, hi in gaps)  # noqa: E731
        style = g.style
        material = self._material(
            g.material,
            {"stone_wall": "stone_grey", "hedge": "foliage", "iron": "metal_black", "wire": "metal_steel"}.get(
                style, "wood_pine"
            ),
        )
        if style in ("stone_wall", "hedge"):
            thick = 0.45 if style == "stone_wall" else 0.7
            for i, (a, b) in enumerate(zip(posts, posts[1:])):
                if in_gap((along[i] + along[i + 1]) / 2):
                    continue
                self._segment(node, f"wall{i}", "fence", a, b, thick, g.height, m, material, visible)
            return
        for i, p in enumerate(posts):
            if not in_gap(along[i]):
                self._box(node, f"post{i}", "fence", (0.1, g.height, 0.1), mul(m, translate(*p)), material, visible)
        heights = {
            "rail": (0.35, 0.8),
            "picket": (0.25, 0.75),
            "panel": (0.0,),
            "iron": (0.15, 0.9),
            "wire": (0.3, 0.6, 0.9),
        }.get(style, (0.35, 0.8))
        pickets = []
        for i, (a, b) in enumerate(zip(posts, posts[1:])):
            if in_gap((along[i] + along[i + 1]) / 2):
                continue
            if style == "panel":
                self._segment(node, f"panel{i}", "fence", a, b, 0.04, g.height * 0.95, m, material, visible)
                continue
            rail_t = 0.03 if style in ("iron", "wire") else 0.05
            for k, frac in enumerate(heights):
                ya, yb = a[1] + g.height * frac, b[1] + g.height * frac
                self._segment(
                    node,
                    f"rail{i}_{k}",
                    "fence",
                    (a[0], ya, a[2]),
                    (b[0], yb, b[2]),
                    rail_t,
                    rail_t * 1.6,
                    m,
                    material,
                    visible,
                )
            if style in ("picket", "iron"):
                step = 0.13 if style == "picket" else 0.15
                n = max(1, int(math.dist((a[0], a[2]), (b[0], b[2])) / step))
                for j in range(1, n):
                    t = j / n
                    pickets.append(
                        (
                            a[0] + (b[0] - a[0]) * t,
                            a[1] + (b[1] - a[1]) * t,
                            a[2] + (b[2] - a[2]) * t,
                            yaw_of(b[0] - a[0], b[2] - a[2]),
                            1.0,
                        )
                    )
        if pickets:
            size = (0.07, g.height * 0.95, 0.02) if style == "picket" else (0.02, g.height, 0.02)
            self._instances(
                node, "pickets", "fence", pickets, visible, m, size, shape="box", size=size, material=material
            )

    # Nature

    def _tree(self, node: Any, g: GenTree, m: Mat, visible: bool) -> None:
        rng = random.Random(g.seed)
        ref = self._ref(node.id)
        low = g.style == "low_poly"
        seg = 6 if low else 14
        species = g.species
        leaves = g.leaf_color or LEAF_COLORS.get(species, LEAF_COLORS["deciduous"]).get(g.season)
        if species in ("deciduous", "fruit", "willow", "birch") and g.season == "winter" and not g.leaf_color:
            species, leaves = "dead", None
        bark = self._color("#e8e4da" if species == "birch" else ("#6b5a48" if species == "dead" else "#5a4632"), 1.0)
        leaf = self._color(leaves, 0.9) if leaves else None
        h, cr, tr = g.height, g.crown_radius, g.trunk_radius

        def part(name: str, shape: str, size: tuple, pm: Mat, material: str) -> None:
            self._shape(f"{node.id}/{name}", ref, "tree", shape, size, pm, material, visible, segments=seg, flat=low)

        if species == "conifer":
            part("trunk", "cylinder", (tr * 2, h * 0.35, tr * 2), m, bark)
            for i in range(3):
                r = cr * (1 - 0.27 * i)
                part(
                    f"tier{i}",
                    "cone",
                    (2 * r, h * 0.42 * (1 - 0.12 * i), 2 * r),
                    mul(m, translate(0, h * (0.22 + 0.23 * i), 0)),
                    leaf,
                )
        elif species == "palm":
            lean = rng.uniform(4, 10)
            for i in range(4):
                pm = mul(m, mul(translate(math.sin(math.radians(lean)) * h * 0.25 * i, h * 0.25 * i, 0), rot_z(-lean)))
                part(f"trunk{i}", "cylinder", (tr * 2 * (1 - 0.12 * i), h * 0.26, tr * 2 * (1 - 0.12 * i)), pm, bark)
            top = (math.sin(math.radians(lean)) * h, h * 0.98, 0)
            for i in range(7):
                ang = i * 360 / 7 + rng.uniform(-10, 10)
                fm = mul(m, mul(translate(*top), mul(rot_y(ang), mul(rot_z(-25), translate(cr / 2, 0, 0)))))
                part(f"frond{i}", "box", (cr, 0.05, cr * 0.28), fm, leaf)
        elif species == "dead":
            part("trunk", "cylinder", (tr * 2, h * 0.75, tr * 2), m, bark)
            for i in range(3):
                ang = rng.uniform(0, 360)
                bm = mul(m, mul(translate(0, h * (0.4 + 0.15 * i), 0), mul(rot_y(ang), rot_z(-50))))
                part(f"branch{i}", "cylinder", (tr, h * 0.3, tr), bm, bark)
        else:
            trunk_h = h * (0.6 if species == "birch" else 0.5)
            part("trunk", "cylinder", (tr * 2, trunk_h, tr * 2), m, bark)
            blobs = 4 if species == "willow" else rng.randint(3, 5)
            centre_y = trunk_h + cr * (0.25 if species == "willow" else 0.55)
            for i in range(blobs):
                r = cr * rng.uniform(0.55, 0.8)
                ang = i * 360 / blobs + rng.uniform(-25, 25)
                off = cr * rng.uniform(0.25, 0.45)
                cx, cz = math.sin(math.radians(ang)) * off, math.cos(math.radians(ang)) * off
                cy = centre_y + rng.uniform(-0.2, 0.25) * cr - r
                squash = 0.7 if species == "willow" else 0.85
                part(
                    f"crown{i}",
                    "sphere",
                    (2 * r, 2 * r * squash, 2 * r),
                    mul(m, translate(cx, max(trunk_h * 0.6, cy), cz)),
                    leaf,
                )
            if species == "fruit":
                fruit = self._color("#c0392b", 0.6)
                for i in range(6):
                    ang = rng.uniform(0, 360)
                    fx, fz = math.sin(math.radians(ang)) * cr * 0.85, math.cos(math.radians(ang)) * cr * 0.85
                    part(
                        f"fruit{i}",
                        "sphere",
                        (0.14, 0.14, 0.14),
                        mul(m, translate(fx, centre_y + rng.uniform(-0.3, 0.3), fz)),
                        fruit,
                    )

    def _rock(self, node: Any, g: GenRock, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "rock")
        sm = mul(m, mul(translate(0, -g.embed * g.size[1], 0), scale(*g.size)))
        self._mesh(node, "", "rock", rock_mesh(g.seed, g.roughness), sm, material, visible, flat=True)

    def _bush(self, node: Any, g: GenBush, m: Mat, visible: bool) -> None:
        rng = random.Random(g.seed)
        ref = self._ref(node.id)
        leaf = self._color("#4a7a35", 0.95)
        w, h, d = g.size
        blobs = 3 + int(g.density * 3)
        for i in range(blobs):
            r = min(w, d) * rng.uniform(0.3, 0.45)
            x, z = rng.uniform(-w / 2 + r, w / 2 - r), rng.uniform(-d / 2 + r, d / 2 - r)
            self._shape(
                f"{node.id}/blob{i}",
                ref,
                "bush",
                "sphere",
                (2 * r, min(h, 2 * r), 2 * r),
                mul(m, translate(x, 0, z)),
                leaf,
                visible,
                segments=6,
                flat=True,
            )
        if g.flowers:
            flower = self._color(g.flowers, 0.7)
            spots = [
                (rng.uniform(-w / 2.5, w / 2.5), h * rng.uniform(0.7, 0.95), rng.uniform(-d / 2.5, d / 2.5), 0.0, 1.0)
                for _ in range(8)
            ]
            self._instances(
                node,
                "flowers",
                "bush",
                spots,
                visible,
                m,
                (0.09, 0.09, 0.09),
                shape="sphere",
                size=(0.09, 0.09, 0.09),
                material=flower,
                segments=5,
                flat=True,
            )

    def _scatter_points(
        self, poly: list[tuple[float, float]], count: int, rng: random.Random
    ) -> list[tuple[float, float]]:
        xs, zs = [p[0] for p in poly], [p[1] for p in poly]
        out = []
        for _ in range(count * 4):
            if len(out) >= count:
                break
            x, z = rng.uniform(min(xs), max(xs)), rng.uniform(min(zs), max(zs))
            if point_in_polygon(x, z, poly):
                out.append((x, z))
        return out

    def _grass(self, node: Any, g: GenGrass, m: Mat, visible: bool) -> None:
        rng = random.Random(g.seed)
        poly = [tuple(p) for p in g.area]
        count = min(4000, int(g.density * _polygon_area(poly)))
        tufts = [
            (x, self._local_ground(m, x, z), z, rng.uniform(0, 360), rng.uniform(0.7, 1.3))
            for x, z in self._scatter_points(poly, count, rng)
        ]
        size = (0.09, g.height, 0.09)
        self._instances(
            node,
            "tufts",
            "grass",
            tufts,
            visible,
            m,
            size,
            shape="cone",
            size=size,
            material=self._color(g.color, 1.0),
            segments=3,
            flat=True,
        )
        if count == 4000:
            self.warnings.append(f"grass '{node.id}': capped at 4000 tufts")

    def _flower_bed(self, node: Any, g: GenFlowerBed, m: Mat, visible: bool) -> None:
        rng = random.Random(g.seed)
        poly = [tuple(p) for p in g.area]
        count = min(3000, int(g.density * _polygon_area(poly)))
        points = [(x, self._local_ground(m, x, z), z) for x, z in self._scatter_points(poly, count, rng)]
        stems = [(x, y, z, 0.0, rng.uniform(0.8, 1.2)) for x, y, z in points]
        stem_size = (0.02, 0.28, 0.02)
        self._instances(
            node,
            "stems",
            "flowers",
            stems,
            visible,
            m,
            stem_size,
            shape="cylinder",
            size=stem_size,
            material=self._color("#4f7d32", 1.0),
            segments=3,
            flat=True,
        )
        for k, colour in enumerate(g.palette):
            heads = [(x, y + 0.26 * s, z, 0.0, s) for i, (x, y, z, _, s) in enumerate(stems) if i % len(g.palette) == k]
            self._instances(
                node,
                f"heads{k}",
                "flowers",
                heads,
                visible,
                m,
                (0.11, 0.08, 0.11),
                shape="sphere",
                size=(0.11, 0.08, 0.11),
                material=self._color(colour, 0.7),
                segments=5,
                flat=True,
            )
        if g.border:
            material = self._preset({"stone": "stone_grey", "wood": "wood_pine", "brick": "brick_red"}[g.border])
            ring = _resample([(x, 0, z) for x, z in poly + [poly[0]]], 0.32)
            stones = [
                (x, self._local_ground(m, x, z), z, rng.uniform(-8, 8), rng.uniform(0.85, 1.15)) for x, _, z in ring
            ]
            self._instances(
                node,
                "border",
                "flowers",
                stones,
                visible,
                m,
                (0.28, 0.14, 0.2),
                shape="box",
                size=(0.28, 0.14, 0.2),
                material=material,
            )

    # Urban and arrangements

    def _along_path(self, node: Any, g: GenAlongPath, m: Mat, visible: bool) -> None:
        line = self.path_world.get(g.path, [])
        if len(line) < 2:
            return
        total = sum(math.dist((a[0], a[2]), (b[0], b[2])) for a, b in zip(line, line[1:]))
        stations = _resample(line, g.spacing)
        transforms = []
        travelled = 0.0
        for i, p in enumerate(stations):
            if i:
                travelled += math.dist((stations[i - 1][0], stations[i - 1][2]), (p[0], p[2]))
            if travelled < g.start or travelled > total + 1e-6:
                continue
            q = stations[min(i + 1, len(stations) - 1)] if i + 1 < len(stations) else p
            prev = stations[i - 1] if i else p
            tx, tz = q[0] - prev[0], q[2] - prev[2]
            n = math.hypot(tx, tz) or 1.0
            tx, tz = tx / n, tz / n
            for side in ((-1.0, 1.0) if g.both_sides else (1.0,)):
                off = g.offset * side if g.both_sides else g.offset
                x, z = p[0] + tz * off, p[2] - tx * off  # offset to the right of travel for positive values
                y = self.ground_height(x, z)
                y = p[1] if y is None else y
                if g.face == "forward":
                    yaw = math.degrees(math.atan2(tx, tz))
                else:
                    to_path = math.degrees(math.atan2(p[0] - x, p[2] - z)) if off else math.degrees(math.atan2(tx, tz))
                    yaw = to_path if g.face == "path" else to_path + 180
                transforms.append((x, y, z, yaw, 1.0))
        dims = self.world.assets[g.asset].dims
        self._instances(node, "copies", "asset", transforms, visible, identity(), dims, asset=g.asset)

    def _radial_array(self, node: Any, g: GenRadialArray, m: Mat, visible: bool) -> None:
        full = abs(g.sweep_deg - 360) < 1e-6
        steps = g.count if full else max(1, g.count - 1)
        transforms = []
        for i in range(g.count):
            ang = g.start_deg + g.sweep_deg * i / steps
            x, z = g.radius * math.sin(math.radians(ang)), g.radius * math.cos(math.radians(ang))
            yaw = {"center": ang + 180, "outward": ang, "tangent": ang + 90, "fixed": 0.0}[g.face]
            transforms.append((x, 0.0, z, yaw, 1.0))
        self._instances(node, "copies", "asset", transforms, visible, m, self.world.assets[g.asset].dims, asset=g.asset)

    def _table_set(self, node: Any, g: GenTableSet, m: Mat, visible: bool) -> None:
        tw, _, td = self.world.assets[g.table].dims
        cw, _, cd = self.world.assets[g.chair].dims
        ref = self._ref(node.id)
        self._add(
            LAssetItem(
                id=f"{node.id}/table",
                node=ref,
                role="asset",
                matrix=column_major(m),
                visible=visible,
                aabb=box_aabb(m, self.world.assets[g.table].dims),
                asset=g.table,
            )
        )
        n = g.chairs
        if g.layout == "ends":
            sides = {"+x": min(n, 1), "-x": min(max(n - 1, 0), 1), "+z": 0, "-z": 0}
        elif g.layout == "one_side":
            sides = {"+z": n, "-z": 0, "+x": 0, "-x": 0}
        elif g.layout == "two_sides":
            sides = {"+z": (n + 1) // 2, "-z": n // 2, "+x": 0, "-x": 0}
        else:
            ends = 2 if n >= 6 else (1 if n == 5 else 0)
            sides = {
                "+x": 1 if ends >= 1 else 0,
                "-x": 1 if ends == 2 else 0,
                "+z": (n - ends + 1) // 2,
                "-z": (n - ends) // 2,
            }
        k = 0
        offset_z = td / 2 + g.chair_gap + cd / 2
        offset_x = tw / 2 + g.chair_gap + cd / 2
        for side, count in sides.items():
            for i in range(count):
                if side in ("+z", "-z"):
                    x = -tw / 2 + tw * (i + 0.5) / count
                    z = offset_z if side == "+z" else -offset_z
                    yaw = 180.0 if side == "+z" else 0.0
                else:
                    x = offset_x if side == "+x" else -offset_x
                    z = -td / 2 + td * (i + 0.5) / count
                    yaw = 270.0 if side == "+x" else 90.0
                cm = mul(m, mul(translate(x, 0, z), rot_y(yaw)))
                self._add(
                    LAssetItem(
                        id=f"{node.id}/chair{k}",
                        node=ref,
                        role="asset",
                        matrix=column_major(cm),
                        visible=visible,
                        aabb=box_aabb(cm, self.world.assets[g.chair].dims),
                        asset=g.chair,
                    )
                )
                k += 1

    def _shelving(self, node: Any, g: GenShelving, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "wood_oak")
        t = 0.025
        for side, x in (("left", -g.width / 2 + t / 2), ("right", g.width / 2 - t / 2)):
            self._box(node, side, "shelving", (t, g.height, g.depth), mul(m, translate(x, 0, 0)), material, visible)
        for i in range(g.shelves + 1):
            y = (g.height - t) * i / g.shelves
            self._box(
                node,
                f"shelf{i}",
                "shelving",
                (g.width - 2 * t, t, g.depth),
                mul(m, translate(0, y, 0)),
                material,
                visible,
            )
        if g.back_panel:
            self._box(
                node,
                "back",
                "shelving",
                (g.width, g.height, 0.01),
                mul(m, translate(0, 0, -g.depth / 2 + 0.005)),
                material,
                visible,
            )

    def _kitchen_run(self, node: Any, g: GenKitchenRun, m: Mat, visible: bool) -> None:
        cabinet = self._material(g.cabinet_material, "wood_oak")
        counter = self._material(g.counter_material, "stone_grey")
        dark = self._preset("metal_black")
        steel = self._preset("metal_steel")
        total = sum(mod.width for mod in g.modules)
        x = -total / 2
        for i, mod in enumerate(g.modules):
            cx = x + mod.width / 2
            at = lambda y=0.0, z=0.0: mul(m, translate(cx, y, z))  # noqa: E731
            tall = mod.type in ("fridge", "tall")
            if mod.type == "gap":
                x += mod.width
                continue
            body_h = 2.0 if tall else g.counter_height - 0.04
            body = steel if mod.type == "fridge" else cabinet
            self._box(node, f"unit{i}", "kitchen", (mod.width - 0.004, body_h, g.depth), at(), body, visible)
            if not tall:
                self._box(
                    node,
                    f"counter{i}",
                    "kitchen",
                    (mod.width, 0.04, g.depth + 0.02),
                    at(g.counter_height - 0.04, 0.01),
                    counter,
                    visible,
                )
                if g.upper_cabinets and mod.type != "stove":
                    self._box(
                        node,
                        f"upper{i}",
                        "kitchen",
                        (mod.width - 0.004, 0.7, 0.35),
                        at(1.45, -g.depth / 2 + 0.175),
                        cabinet,
                        visible,
                    )
            if mod.type == "sink":
                self._box(
                    node,
                    f"sink{i}",
                    "kitchen",
                    (mod.width * 0.6, 0.012, g.depth * 0.55),
                    at(g.counter_height, 0.02),
                    steel,
                    visible,
                )
            elif mod.type == "stove":
                self._box(
                    node,
                    f"hob{i}",
                    "kitchen",
                    (mod.width * 0.9, 0.012, g.depth * 0.8),
                    at(g.counter_height, 0.0),
                    dark,
                    visible,
                )
            elif mod.type in ("oven", "dishwasher"):
                self._box(
                    node,
                    f"door{i}",
                    "kitchen",
                    (mod.width * 0.9, g.counter_height * 0.6, 0.01),
                    at(0.1, g.depth / 2 + 0.005),
                    dark,
                    visible,
                )
            x += mod.width
