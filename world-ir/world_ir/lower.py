"""Lowering: World IR → LoweredScene.

This is the Python half of the compiler. It runs everything that needs real
geometry (rooms, generators, presets, nested transforms) and emits simple
items with world-space matrices. The validators and the Three.js loader both
read the result, so they always agree on what is where.

Version 1 lowers: room (floor, walls cut around openings, ceiling, door
panels, window glass, door clearance zones), asset, primitive, group, light,
camera, zone, and the generators rug, stairs (straight), wall_run and array.
Anything else is listed in ``LoweredScene.unsupported`` and skipped; its
children are still lowered.
"""

import math
from typing import Any, Optional

from .assets import MATERIAL_PRESETS, Material
from .common import Transform
from .generators import GenArray, GenRug, GenStairs, GenWallRun
from .lowered import (
    LOWERING_VERSION,
    THREE_VERSION,
    LAsset,
    LAssetItem,
    LCamera,
    LLight,
    LMaterial,
    LoweredScene,
    LShape,
    LSlab,
    LZone,
    Unsupported,
)
from .nodes import (
    AssetNode,
    CameraNode,
    GeneratorNode,
    GroupNode,
    LightNode,
    PrimitiveNode,
    RoomNode,
    ZoneNode,
)
from .world import World

Mat = list[list[float]]

FLOOR_THICKNESS = 0.1
CEILING_THICKNESS = 0.1
DOOR_PANEL_THICKNESS = 0.04
GLASS_THICKNESS = 0.02


# Matrix helpers (row-major 4×4) -----------------------------------------------


def identity() -> Mat:
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def mul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def translate(x: float, y: float, z: float) -> Mat:
    m = identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def scale(sx: float, sy: float, sz: float) -> Mat:
    m = identity()
    m[0][0], m[1][1], m[2][2] = sx, sy, sz
    return m


def rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


def rot_y(deg: float) -> Mat:
    """Positive angles turn +Z toward +X, matching the IR's yaw."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]


def rot_z(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def from_quat(x: float, y: float, z: float, w: float) -> Mat:
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1],
    ]


def xform_matrix(t: Transform) -> Mat:
    """T · R · S. Euler 'rot' uses Three.js XYZ order, which is Rx · Ry · Rz."""
    if t.yaw is not None:
        r = rot_y(t.yaw)
    elif t.rot is not None:
        r = mul(mul(rot_x(t.rot[0]), rot_y(t.rot[1])), rot_z(t.rot[2]))
    elif t.quat is not None:
        r = from_quat(*t.quat)
    else:
        r = identity()
    s = t.scale if isinstance(t.scale, tuple) else (t.scale, t.scale, t.scale)
    return mul(mul(translate(*t.pos), r), scale(*s))


def apply(m: Mat, p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def apply_dir(m: Mat, v: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = v
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z,
        m[1][0] * x + m[1][1] * y + m[1][2] * z,
        m[2][0] * x + m[2][1] * y + m[2][2] * z,
    )


def column_major(m: Mat) -> tuple[float, ...]:
    return tuple(round(m[r][c], 6) + 0.0 for c in range(4) for r in range(4))


def box_aabb(m: Mat, size: tuple[float, float, float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """World AABB of a box whose origin is the centre of its bottom face."""
    w, h, d = size
    corners = [apply(m, (x, y, z)) for x in (-w / 2, w / 2) for y in (0.0, h) for z in (-d / 2, d / 2)]
    return _bounds(corners)


def _bounds(points: list[tuple[float, float, float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    lo = tuple(round(min(p[i] for p in points), 4) for i in range(3))
    hi = tuple(round(max(p[i] for p in points), 4) for i in range(3))
    return lo, hi


def yaw_of(dx: float, dz: float) -> float:
    """Yaw that turns local +X onto the direction (dx, dz)."""
    return math.degrees(math.atan2(-dz, dx))


# Colour -----------------------------------------------------------------------


def kelvin_to_hex(kelvin: float) -> str:
    """Approximate colour of a black body (Tanner Helland's fit)."""
    t = kelvin / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return "#" + "".join(f"{int(max(0, min(255, v))):02x}" for v in (r, g, b))


# Lowering ---------------------------------------------------------------------


class _Lowerer:
    def __init__(self, world: World):
        self.world = world
        self.items: list[Any] = []
        self.unsupported: list[Unsupported] = []
        self.warnings: list[str] = []
        self.materials: dict[str, LMaterial] = {}
        for name, material in world.materials.items():
            self.materials[name] = self._resolve(material, name)
        self.node_world: dict[str, Mat] = {}
        for root in world.nodes:
            self._index(root, identity())

    # Setup

    def _index(self, node: Any, parent: Mat) -> None:
        m = mul(parent, xform_matrix(node.xform))
        self.node_world[node.id] = m
        for child in node.children:
            self._index(child, m)

    def _resolve(self, material: Material, name: str) -> LMaterial:
        values = {field: getattr(material, field) for field in LMaterial.model_fields}
        if material.preset:
            if material.preset in MATERIAL_PRESETS:
                values.update(MATERIAL_PRESETS[material.preset])
            else:
                self.warnings.append(f"material '{name}': unknown preset '{material.preset}'")
        values.update({f: getattr(material, f) for f in material.model_fields_set if f in LMaterial.model_fields})
        return LMaterial(**values)

    def _preset(self, preset: str) -> str:
        key = f"_{preset}"
        if key not in self.materials:
            self.materials[key] = LMaterial(
                **{**Material().model_dump(include=set(LMaterial.model_fields)), **MATERIAL_PRESETS[preset]}
            )
        return key

    def _color(self, hex_color: str, roughness: float = 0.85) -> str:
        key = f"_c_{hex_color.lstrip('#').lower()}"
        if key not in self.materials:
            self.materials[key] = LMaterial(base_color=hex_color, metallic=0.0, roughness=roughness)
        return key

    def _material(self, ref: Optional[str], fallback_preset: str) -> str:
        return ref if ref else self._preset(fallback_preset)

    def _add(self, item: Any) -> None:
        self.items.append(item)

    def _point_target(self, target: Any, parent: Mat) -> Optional[tuple[float, float, float]]:
        """A target given as coordinates (in the parent's space) or as a node ID."""
        if target is None:
            return None
        if isinstance(target, str):
            node = self.world.node(target)
            height = 0.0
            if isinstance(node, AssetNode):
                height = self.world.assets[node.asset].dims[1] / 2
            return tuple(round(v, 4) for v in apply(self.node_world[target], (0.0, height, 0.0)))
        return tuple(round(v, 4) for v in apply(parent, tuple(target)))

    # Walking

    def lower_node(self, node: Any, parent: Mat, visible: bool) -> None:
        m = self.node_world[node.id]
        visible = visible and node.visible
        handler = {
            GroupNode: None,
            AssetNode: self._asset,
            PrimitiveNode: self._primitive,
            RoomNode: self._room,
            LightNode: self._light,
            CameraNode: self._camera,
            ZoneNode: self._zone,
            GeneratorNode: self._generator,
        }
        kind = type(node)
        if kind in handler:
            if handler[kind] is not None:
                handler[kind](node, m, parent, visible)
        else:
            self.unsupported.append(Unsupported(node=node.id, kind=node.kind, reason="not lowered in this version"))
        for child in node.children:
            self.lower_node(child, m, visible)

    # Node kinds

    def _asset(self, node: AssetNode, m: Mat, parent: Mat, visible: bool) -> None:
        dims = self.world.assets[node.asset].dims
        self._add(
            LAssetItem(
                id=node.id,
                node=node.id,
                role="asset",
                matrix=column_major(m),
                visible=visible,
                aabb=box_aabb(m, dims),
                asset=node.asset,
                materials={o.slot: o.material for o in node.materials},
            )
        )

    def _primitive(self, node: PrimitiveNode, m: Mat, parent: Mat, visible: bool) -> None:
        material = node.material or (self._color(node.color) if node.color else self._preset("plastic_white"))
        self._shape(node.id, node.id, "primitive", node.shape, node.size, m, material, visible)

    def _shape(
        self,
        item_id: str,
        node_id: str,
        role: str,
        shape: str,
        size: tuple[float, float, float],
        m: Mat,
        material: str,
        visible: bool,
        facing: Optional[tuple[float, float]] = None,
    ) -> None:
        self._add(
            LShape(
                id=item_id,
                node=node_id,
                role=role,
                matrix=column_major(m),
                visible=visible,
                aabb=box_aabb(m, size),
                shape=shape,
                size=tuple(round(v, 5) for v in size),
                material=material,
                facing=facing,
            )
        )

    def _light(self, node: LightNode, m: Mat, parent: Mat, visible: bool) -> None:
        target = self._point_target(node.target, parent)
        if target is None and node.type in ("spot", "directional"):
            target = tuple(round(v, 4) for v in apply(m, (0.0, -1.0, 0.0)))
        self._add(
            LLight(
                id=node.id,
                node=node.id,
                role="light",
                matrix=column_major(m),
                visible=visible,
                light=node.type,
                color=node.color or kelvin_to_hex(node.kelvin),
                intensity=node.intensity,
                range=node.range,
                angle_deg=node.angle_deg,
                penumbra=node.penumbra,
                size=node.size,
                cast_shadows=node.cast_shadows,
                target=target,
            )
        )

    def _camera(self, node: CameraNode, m: Mat, parent: Mat, visible: bool) -> None:
        self._add(
            LCamera(
                id=node.id,
                node=node.id,
                role="camera",
                matrix=column_major(m),
                visible=visible,
                purpose=node.purpose,
                projection=node.projection,
                fov_deg=node.fov_deg,
                near=node.near,
                far=node.far,
                ortho_height=node.ortho_height,
                look_at=self._point_target(node.look_at, parent),
            )
        )

    def _zone(self, node: ZoneNode, m: Mat, parent: Mat, visible: bool) -> None:
        polygon = [(round(p[0], 4), round(p[2], 4)) for p in (apply(m, (x, 0.0, z)) for x, z in node.area)]
        base = apply(m, (0.0, 0.0, 0.0))[1]
        self._add(
            LZone(
                id=node.id,
                node=node.id,
                role="zone",
                matrix=column_major(m),
                visible=visible,
                purpose=node.purpose,
                polygon=polygon,
                y_range=(round(base + node.y_range[0], 4), round(base + node.y_range[1], 4)),
                label=node.label,
            )
        )

    # Rooms and walls

    def _room(self, node: RoomNode, m: Mat, parent: Mat, visible: bool) -> None:
        outline = [tuple(p) for p in node.outline]
        floor = node.floor_material or self._preset("floor_parquet")
        wall_default = node.wall_material or self._preset("plaster_white")
        ceiling = node.ceiling_material or wall_default
        self._add(
            LSlab(
                id=f"{node.id}/floor",
                node=node.id,
                role="floor",
                matrix=column_major(m),
                visible=visible,
                aabb=_bounds([apply(m, (x, y, z)) for x, z in outline for y in (0.0, -FLOOR_THICKNESS)]),
                polygon=outline,
                thickness=FLOOR_THICKNESS,
                material=floor,
            )
        )
        if node.has_ceiling:
            top = node.height + CEILING_THICKNESS
            cm = mul(m, translate(0.0, top, 0.0))
            self._add(
                LSlab(
                    id=f"{node.id}/ceiling",
                    node=node.id,
                    role="ceiling",
                    matrix=column_major(cm),
                    visible=visible,
                    aabb=_bounds([apply(cm, (x, y, z)) for x, z in outline for y in (0.0, -CEILING_THICKNESS)]),
                    polygon=outline,
                    thickness=CEILING_THICKNESS,
                    material=ceiling,
                )
            )
        area = sum(
            outline[i][0] * outline[(i + 1) % len(outline)][1] - outline[(i + 1) % len(outline)][0] * outline[i][1]
            for i in range(len(outline))
        )
        overrides = {w.edge: w for w in node.walls}
        for edge in range(len(outline)):
            override = overrides.get(edge)
            if override and override.hidden:
                continue
            a, b = outline[edge], outline[(edge + 1) % len(outline)]
            openings = [o for o in node.openings if o.wall == edge]
            self._wall(
                node_id=node.id,
                prefix=f"{node.id}/wall{edge}",
                m=m,
                a=a,
                b=b,
                height=(override.height if override and override.height else node.height),
                thickness=node.wall_thickness,
                material=(override.material if override and override.material else wall_default),
                openings=openings,
                outward_sign=1.0 if area > 0 else -1.0,
                centred=False,
                visible=visible,
                clearance=self.world.rules.door_clearance,
            )

    def _wall(
        self,
        node_id: str,
        prefix: str,
        m: Mat,
        a: tuple[float, float],
        b: tuple[float, float],
        height: float,
        thickness: float,
        material: str,
        openings: list[Any],
        outward_sign: float,
        centred: bool,
        visible: bool,
        clearance: float,
    ) -> None:
        """One straight wall from a to b, split into boxes around its openings.

        Room walls sit outside the outline so the outline stays the interior face.
        They run past both corners by their thickness, which closes convex corners.
        """
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length < 1e-6:
            return
        dx, dz = dx / length, dz / length
        nx, nz = (dz * outward_sign, -dx * outward_sign)
        offset = 0.0 if centred else thickness / 2
        yaw = yaw_of(dx, dz)
        world_n = apply_dir(m, (nx, 0.0, nz))
        facing = (round(world_n[0], 4), round(world_n[2], 4))

        def piece(name: str, u0: float, u1: float, y0: float, y1: float) -> None:
            if u1 - u0 < 1e-4 or y1 - y0 < 1e-4:
                return
            u = (u0 + u1) / 2
            cx, cz = a[0] + dx * u + nx * offset, a[1] + dz * u + nz * offset
            pm = mul(m, mul(translate(cx, y0, cz), rot_y(yaw)))
            self._shape(
                f"{prefix}/{name}", node_id, "wall", "box", (u1 - u0, y1 - y0, thickness), pm, material, visible, facing
            )

        extend = 0.0 if centred else thickness
        cursor = -extend
        for i, o in enumerate(sorted(openings, key=lambda o: o.offset)):
            o0, o1 = o.offset - o.width / 2, o.offset + o.width / 2
            top = min(o.sill + o.height, height)
            piece(f"{i}_before", cursor, o0, 0.0, height)
            piece(f"{i}_above", o0, o1, top, height)
            piece(f"{i}_below", o0, o1, 0.0, o.sill)
            cursor = max(cursor, o1)
            self._opening(node_id, prefix, m, o, a, (dx, dz), (nx, nz), offset, yaw, visible, clearance, facing)
        piece("end", cursor, length + extend, 0.0, height)

    def _opening(
        self,
        node_id: str,
        prefix: str,
        m: Mat,
        o: Any,
        a: tuple[float, float],
        d: tuple[float, float],
        n: tuple[float, float],
        offset: float,
        yaw: float,
        visible: bool,
        clearance: float,
        facing: tuple[float, float],
    ) -> None:
        dx, dz = d
        nx, nz = n
        mid = lambda u: (a[0] + dx * u + nx * offset, a[1] + dz * u + nz * offset)  # noqa: E731
        if o.type == "window":
            cx, cz = mid(o.offset)
            pm = mul(m, mul(translate(cx, o.sill, cz), rot_y(yaw)))
            size = (o.width, o.height, GLASS_THICKNESS)
            glass = self._preset("glass_clear")
            self._shape(f"{prefix}/{o.id}", node_id, "window", "box", size, pm, glass, visible, facing)
            return
        if o.type != "door":
            return
        # Hinge side is judged from inside the room, looking out through the door (along +n).
        left = (nz, -nx)
        larger_u_is_left = dx * left[0] + dz * left[1] > 0
        hinge_at_larger_u = larger_u_is_left == (o.hinge == "left")
        hinge_u = o.offset + o.width / 2 if hinge_at_larger_u else o.offset - o.width / 2
        toward_free = -1.0 if hinge_at_larger_u else 1.0
        angle = 90.0 * o.open
        if o.swing in ("in", "out") and angle > 0:
            into = (-nx, -nz) if o.swing == "in" else (nx, nz)
            options = []
            for sign in (1.0, -1.0):
                r = rot_y(sign * angle)
                v = apply_dir(r, (dx * toward_free, 0.0, dz * toward_free))
                options.append((v[0] * into[0] + v[2] * into[1], v))
            panel_dir = max(options)[1]
        else:
            panel_dir = (dx * toward_free, 0.0, dz * toward_free)
        hx, hz = mid(hinge_u)
        width = o.width - 0.02
        cx, cz = hx + panel_dir[0] * width / 2, hz + panel_dir[2] * width / 2
        pm = mul(m, mul(translate(cx, o.sill, cz), rot_y(yaw_of(panel_dir[0], panel_dir[2]))))
        material = self._preset("wood_oak")
        size = (width, o.height - 0.01, DOOR_PANEL_THICKNESS)
        self._shape(f"{prefix}/{o.id}", node_id, "door", "box", size, pm, material, visible, facing)
        # The free zone in front of the door, on the room side.
        depth = o.clearance or clearance
        p0 = (a[0] + dx * (o.offset - o.width / 2), a[1] + dz * (o.offset - o.width / 2))
        p1 = (a[0] + dx * (o.offset + o.width / 2), a[1] + dz * (o.offset + o.width / 2))
        corners = [p0, p1, (p1[0] - nx * depth, p1[1] - nz * depth), (p0[0] - nx * depth, p0[1] - nz * depth)]
        polygon = [(round(p[0], 4), round(p[2], 4)) for p in (apply(m, (x, 0.0, z)) for x, z in corners)]
        base = apply(m, (0.0, 0.0, 0.0))[1]
        self._add(
            LZone(
                id=f"{prefix}/{o.id}/clearance",
                node=node_id,
                role="door_clearance",
                matrix=column_major(m),
                visible=visible,
                purpose="clearance",
                polygon=polygon,
                y_range=(round(base, 4), round(base + 2.0, 4)),
                label=f"clearance of {o.id}",
            )
        )

    # Generators

    def _generator(self, node: GeneratorNode, m: Mat, parent: Mat, visible: bool) -> None:
        g = node.generator
        if isinstance(g, GenRug):
            self._rug(node.id, g, m, visible)
        elif isinstance(g, GenStairs) and g.shape == "straight":
            self._stairs(node.id, g, m, visible)
        elif isinstance(g, GenWallRun):
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
                    True,
                    visible,
                    self.world.rules.door_clearance,
                )
        elif isinstance(g, GenArray):
            dims = self.world.assets[g.asset].dims
            for ix in range(g.count[0]):
                for iy in range(g.count[1]):
                    for iz in range(g.count[2]):
                        im = mul(m, translate(ix * g.spacing[0], iy * g.spacing[1], iz * g.spacing[2]))
                        self._add(
                            LAssetItem(
                                id=f"{node.id}/{ix}_{iy}_{iz}",
                                node=node.id,
                                role="asset",
                                matrix=column_major(im),
                                visible=visible,
                                aabb=box_aabb(im, dims),
                                asset=g.asset,
                            )
                        )
        else:
            detail = f" ({g.shape})" if isinstance(g, GenStairs) else ""
            self.unsupported.append(
                Unsupported(node=node.id, kind=f"generator:{g.gen}{detail}", reason="not lowered in this version")
            )

    def _rug(self, node_id: str, g: GenRug, m: Mat, visible: bool) -> None:
        width, depth = g.size
        shape = "cylinder" if g.shape in ("round", "oval") else "box"
        if g.shape == "round":
            depth = width
        if g.pattern == "bordered" and len(g.colors) > 1:
            border, inner = g.colors[1], g.colors[0]
            self._shape(
                f"{node_id}/border",
                node_id,
                "rug",
                shape,
                (width, g.thickness, depth),
                m,
                self._color(border, 1.0),
                visible,
            )
            inset = min(0.12, width / 6, depth / 6)
            self._shape(
                f"{node_id}/field",
                node_id,
                "rug",
                shape,
                (width - 2 * inset, g.thickness + 0.002, depth - 2 * inset),
                m,
                self._color(inner, 1.0),
                visible,
            )
        else:
            self._shape(
                node_id, node_id, "rug", shape, (width, g.thickness, depth), m, self._color(g.colors[0], 1.0), visible
            )

    def _stairs(self, node_id: str, g: GenStairs, m: Mat, visible: bool) -> None:
        material = self._material(g.material, "wood_oak")
        for i in range(g.steps):
            sm = mul(m, translate(0.0, 0.0, g.run * (i + 0.5)))
            self._shape(
                f"{node_id}/step{i}", node_id, "step", "box", (g.width, g.rise * (i + 1), g.run), sm, material, visible
            )
        sides = {"left": [1.0], "right": [-1.0], "both": [1.0, -1.0], "none": []}[g.railing]
        rail_material = self._preset("metal_black")
        for side in sides:
            x = side * (g.width / 2 - 0.04)
            z0, z1 = g.run * 0.5, g.run * (g.steps - 0.5)
            y0, y1 = g.rise + 0.9, g.rise * g.steps + 0.9
            length = math.hypot(z1 - z0, y1 - y0)
            pitch = math.degrees(math.atan2(y1 - y0, z1 - z0))
            rm = mul(m, mul(translate(x, (y0 + y1) / 2 - 0.025, (z0 + z1) / 2), rot_x(-pitch)))
            name = "left" if side > 0 else "right"
            self._shape(
                f"{node_id}/rail_{name}", node_id, "railing", "box", (0.05, 0.05, length), rm, rail_material, visible
            )
            for j, (z, step) in enumerate(((z0, 1), (z1, g.steps))):
                pm = mul(m, translate(x, g.rise * step, z))
                self._shape(
                    f"{node_id}/post_{name}{j}",
                    node_id,
                    "railing",
                    "box",
                    (0.05, 0.9, 0.05),
                    pm,
                    rail_material,
                    visible,
                )


def lower(world: World) -> LoweredScene:
    """Lowers a validated world. Deterministic: the same world always gives the same scene."""
    lowerer = _Lowerer(world)
    for root in world.nodes:
        lowerer.lower_node(root, identity(), True)
    ids = [item.id for item in lowerer.items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"lowering produced duplicate item ids: {duplicates}")
    boxes = [item.aabb for item in lowerer.items if getattr(item, "aabb", None)]
    bounds = None
    if boxes:
        bounds = (
            tuple(min(b[0][i] for b in boxes) for i in range(3)),
            tuple(max(b[1][i] for b in boxes) for i in range(3)),
        )
    return LoweredScene(
        world_id=world.id,
        versions={"ir": world.ir_version, "lowering": LOWERING_VERSION, "three": THREE_VERSION},
        environment=world.environment,
        materials=lowerer.materials,
        assets={name: LAsset(uri=a.uri, dims=a.dims, front=a.front) for name, a in world.assets.items()},
        items=lowerer.items,
        bounds=bounds,
        unsupported=lowerer.unsupported,
        extras={"warnings": lowerer.warnings} if lowerer.warnings else {},
    )
