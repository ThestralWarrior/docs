"""Lowering: World IR → LoweredScene.

This is the Python half of the compiler. It runs everything that needs real
geometry (rooms, terrain, paths, generators, prefabs, scatters, presets, nested
transforms) and emits simple items with world-space matrices. The validators
and the Three.js loader both read the result, so they always agree on what is where.

Lowered: group, asset, primitive, room, light, camera, zone, marker, terrain
(heights, edits, painted layers, water level), path, prefab, scatter, the
generators listed in lower_generators.py, and behaviours (recorded for the
loader to play; items keep their rest pose). Anything else (water bodies, decals,
text, audio, particles, a few generators) is listed in
``LoweredScene.unsupported`` and skipped; its children are still lowered.
"""

import math
import random
from typing import Any, Optional

from .assets import MATERIAL_PRESETS, Material
from .geometry import (  # noqa: F401  (re-exported for callers and tests)
    Mat,
    apply,
    apply_dir,
    bounds,
    box_aabb,
    column_major,
    identity,
    inverse,
    kelvin_to_hex,
    mul,
    ribbon_mesh,
    rot_x,
    rot_y,
    rot_z,
    translate,
    xform_matrix,
    yaw_of,
)
from .lower_generators import GeneratorMixin
from .lowered import (
    LOWERING_VERSION,
    THREE_VERSION,
    LAsset,
    LAssetItem,
    LBehavior,
    LCamera,
    LHeightfield,
    LInstances,
    LLight,
    LMaterial,
    LMesh,
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
    MarkerNode,
    PathNode,
    PrefabNode,
    PrimitiveNode,
    RoomNode,
    ScatterNode,
    TerrainNode,
    ZoneNode,
)
from .terrain_eval import Heightfield, evaluate_terrain, point_in_polygon, polyline, segment_distance
from .world import World

FLOOR_THICKNESS = 0.1
CEILING_THICKNESS = 0.1
DOOR_PANEL_THICKNESS = 0.04
GLASS_THICKNESS = 0.02
SCATTER_CAP = 3000
PLAYED_BEHAVIORS = ("spin", "bob", "sway", "flicker", "follow_path")


class _Lowerer(GeneratorMixin):
    def __init__(self, world: World):
        self.world = world
        self.items: list[Any] = []
        self.unsupported: list[Unsupported] = []
        self.warnings: list[str] = []
        self.behaviors: list[LBehavior] = []
        self.deferred: list[tuple[Any, Mat, bool]] = []
        self._owner: Optional[str] = None
        self.materials: dict[str, LMaterial] = {}
        for name, material in world.materials.items():
            self.materials[name] = self._resolve(material, name)
        self.node_world: dict[str, Mat] = {}
        self.prefab_copies: dict[str, list[Any]] = {}
        self.children_of: dict[str, list[str]] = {}
        for root in world.nodes:
            self._index(root, identity())
        self._precompute_geometry()

    # Setup ---------------------------------------------------------------------

    def _index(self, node: Any, parent: Mat) -> None:
        m = mul(parent, xform_matrix(node.xform))
        self.node_world[node.id] = m
        self.children_of[node.id] = [c.id for c in node.children]
        if isinstance(node, PrefabNode):
            prefab = self.world.prefabs[node.prefab]
            copies = [self._namespaced(root, node.id, node.overrides) for root in prefab.nodes]
            self.prefab_copies[node.id] = copies
            self.children_of[node.id] += [c.id for c in copies]
            for copy in copies:
                self._index(copy, m)
        for child in node.children:
            self._index(child, m)

    def _namespaced(self, node: Any, prefix: str, overrides: dict[str, Any]) -> Any:
        """A copy of a prefab node with its ID prefixed by the instance ID and any overrides applied."""
        update: dict[str, Any] = {"id": f"{prefix}/{node.id}"}
        patch = overrides.get(node.id)
        if patch is not None:
            for field in ("xform", "asset", "materials", "visible"):
                value = getattr(patch, field)
                if value is not None and hasattr(node, field):
                    update[field] = value
        update["children"] = [self._namespaced(child, prefix, overrides) for child in node.children]
        return node.model_copy(update=update)

    def _walk_all(self) -> list[Any]:
        out: list[Any] = []

        def visit(node: Any) -> None:
            out.append(node)
            for copy in self.prefab_copies.get(node.id, []):
                visit(copy)
            for child in node.children:
                visit(child)

        for root in self.world.nodes:
            visit(root)
        return out

    def _precompute_geometry(self) -> None:
        """Paths, zones and terrain heights, which other nodes depend on."""
        nodes = self._walk_all()
        self.path_world: dict[str, list[tuple[float, float, float]]] = {}
        self.path_width: dict[str, float] = {}
        self.path_closed: dict[str, bool] = {}
        self.path_conform: dict[str, bool] = {}
        self.zone_world: dict[str, list[tuple[float, float]]] = {}
        for node in nodes:
            m = self.node_world[node.id]
            if isinstance(node, PathNode):
                curve = polyline([tuple(p) for p in node.points], node.closed, node.smooth)
                self.path_world[node.id] = [apply(m, p) for p in curve]
                self.path_width[node.id] = node.width
                self.path_closed[node.id] = node.closed
                self.path_conform[node.id] = node.conform_to_terrain
            elif isinstance(node, ZoneNode):
                self.zone_world[node.id] = [(p[0], p[2]) for p in (apply(m, (x, 0.0, z)) for x, z in node.area)]
        self.terrains: list[tuple[Any, Mat, Mat, Heightfield]] = []
        for node in nodes:
            if isinstance(node, TerrainNode):
                m = self.node_world[node.id]
                inv = inverse(m)
                paths = {k: [apply(inv, p) for p in v] for k, v in self.path_world.items()}
                zones = {
                    k: [(p[0], p[2]) for p in (apply(inv, (x, 0.0, z)) for x, z in v)]
                    for k, v in self.zone_world.items()
                }
                hf = evaluate_terrain(node, paths, zones)
                self.warnings += [f"terrain '{node.id}': {w}" for w in hf.warnings]
                self.terrains.append((node, m, inv, hf))

    def ground_height(self, x: float, z: float) -> Optional[float]:
        """World height of the first terrain under world (x, z), or None if there is none."""
        for _, m, inv, hf in self.terrains:
            lx, _, lz = apply(inv, (x, 0.0, z))
            h = hf.height(lx, lz)
            if h is not None:
                return apply(m, (lx, h, lz))[1]
        return None

    def slope_at(self, x: float, z: float) -> float:
        for _, m, inv, hf in self.terrains:
            lx, _, lz = apply(inv, (x, 0.0, z))
            if hf.height(lx, lz) is not None:
                return hf.slope_deg(lx, lz)
        return 0.0

    # Materials -------------------------------------------------------------------

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
            defaults = Material().model_dump(include=set(LMaterial.model_fields))
            self.materials[key] = LMaterial(**{**defaults, **MATERIAL_PRESETS[preset]})
        return key

    def _color(self, hex_color: str, roughness: float = 0.85) -> str:
        key = f"_c_{hex_color.lstrip('#').lower()}"
        if key not in self.materials:
            self.materials[key] = LMaterial(base_color=hex_color, metallic=0.0, roughness=roughness)
        return key

    def _material(self, ref: Optional[str], fallback_preset: str) -> str:
        return ref if ref else self._preset(fallback_preset)

    # Emitting --------------------------------------------------------------------

    def _ref(self, node_id: str) -> str:
        """The IR node an item belongs to: the prefab instance for nodes inside a prefab."""
        return self._owner or node_id

    def _add(self, item: Any) -> None:
        self.items.append(item)

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
        segments: Optional[int] = None,
        flat: bool = False,
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
                segments=segments,
                flat=flat,
            )
        )

    def _point_target(self, target: Any, parent: Mat) -> Optional[tuple[float, float, float]]:
        """A target given as coordinates (in the parent's space) or as a node ID."""
        if target is None:
            return None
        if isinstance(target, str):
            node = self.world.node(target)
            height = 0.0
            if isinstance(node, AssetNode):
                height = self.world.assets[node.asset].dims[1] / 2
            elif isinstance(node, GeneratorNode):
                height = 2.0
            return tuple(round(v, 4) for v in apply(self.node_world[target], (0.0, height, 0.0)))
        return tuple(round(v, 4) for v in apply(parent, tuple(target)))

    # Walking -----------------------------------------------------------------------

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
            MarkerNode: self._marker,
            GeneratorNode: self._generator,
            TerrainNode: self._terrain,
            PathNode: self._path,
            PrefabNode: self._prefab,
        }
        kind = type(node)
        first_item = len(self.items)
        if kind is ScatterNode:
            self.deferred.append((node, m, visible, self._owner))
        elif kind in handler:
            if handler[kind] is not None:
                handler[kind](node, m, parent, visible)
        else:
            self.unsupported.append(Unsupported(node=node.id, kind=node.kind, reason="not lowered in this version"))
        for child in node.children:
            self.lower_node(child, m, visible)
        if node.behaviors:
            self._behaviors(node, m, [item.id for item in self.items[first_item:]])

    def _behaviors(self, node: Any, m: Mat, items: list[str]) -> None:
        """Records the node's animations for the loader. Items keep their rest pose."""
        for b in node.behaviors:
            if b.preset not in PLAYED_BEHAVIORS:
                self.warnings.append(f"'{node.id}': behaviour '{b.preset}' is not played yet")
                continue
            path = None
            closed = False
            if b.preset == "follow_path":
                line = self.path_world.get(b.path or "", [])
                if len(line) < 2:
                    self.warnings.append(f"'{node.id}': follow_path needs a path")
                    continue
                closed = self.path_closed.get(b.path, False)
                lift = float(b.params.get("height_m", 0.0))
                path = []
                for x, y, z in line:
                    ground = self.ground_height(x, z) if self.path_conform.get(b.path) else None
                    path.append((round(x, 3), round((ground if ground is not None else y) + lift, 3), round(z, 3)))
                if closed and len(path) > 2 and path[0] == path[-1]:
                    path = path[:-1]
            self.behaviors.append(
                LBehavior(
                    node=node.id,
                    preset=b.preset,
                    items=items,
                    origin=column_major(m),
                    params=b.params,
                    path=path,
                    closed=closed,
                )
            )

    # Node kinds --------------------------------------------------------------------

    def _asset(self, node: AssetNode, m: Mat, parent: Mat, visible: bool) -> None:
        dims = self.world.assets[node.asset].dims
        self._add(
            LAssetItem(
                id=node.id,
                node=self._ref(node.id),
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
        self._shape(
            node.id,
            self._ref(node.id),
            "primitive",
            node.shape,
            node.size,
            m,
            material,
            visible,
            segments=node.segments,
        )

    def _light(self, node: LightNode, m: Mat, parent: Mat, visible: bool) -> None:
        target = self._point_target(node.target, parent)
        if target is None and node.type in ("spot", "directional"):
            target = tuple(round(v, 4) for v in apply(m, (0.0, -1.0, 0.0)))
        self._add(
            LLight(
                id=node.id,
                node=self._ref(node.id),
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
                node=self._ref(node.id),
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
        polygon = [(round(p[0], 4), round(p[1], 4)) for p in self.zone_world[node.id]]
        base = apply(m, (0.0, 0.0, 0.0))[1]
        self._add(
            LZone(
                id=node.id,
                node=self._ref(node.id),
                role="zone",
                matrix=column_major(m),
                visible=visible,
                purpose=node.purpose,
                polygon=polygon,
                y_range=(round(base + node.y_range[0], 4), round(base + node.y_range[1], 4)),
                label=node.label,
            )
        )

    def _marker(self, node: MarkerNode, m: Mat, parent: Mat, visible: bool) -> None:
        cx, cy, cz = apply(m, (0.0, 0.0, 0.0))
        r = max(node.radius, 0.2)
        polygon = [
            (round(cx + r * math.sin(a), 4), round(cz + r * math.cos(a), 4))
            for a in (i * math.tau / 10 for i in range(10))
        ]
        self._add(
            LZone(
                id=node.id,
                node=self._ref(node.id),
                role="marker",
                matrix=column_major(m),
                visible=visible,
                purpose=node.purpose,
                polygon=polygon,
                y_range=(round(cy, 4), round(cy + 2.0, 4)),
                label=node.name or node.purpose,
            )
        )

    def _prefab(self, node: PrefabNode, m: Mat, parent: Mat, visible: bool) -> None:
        previous = self._owner
        self._owner = previous or node.id
        for copy in self.prefab_copies[node.id]:
            self.lower_node(copy, m, visible)
        self._owner = previous

    # Terrain and paths ---------------------------------------------------------------

    def _terrain(self, node: TerrainNode, m: Mat, parent: Mat, visible: bool) -> None:
        hf = next(t[3] for t in self.terrains if t[0].id == node.id)
        if node.layers:
            layer_materials = [layer.material for layer in node.layers]
        else:
            layer_materials = [self._preset("grass")]
        corners = [
            apply(m, (x, y, z))
            for x in (-hf.size[0] / 2, hf.size[0] / 2)
            for z in (-hf.size[1] / 2, hf.size[1] / 2)
            for y in (min(hf.heights), max(hf.heights))
        ]
        self._add(
            LHeightfield(
                id=node.id,
                node=self._ref(node.id),
                role="terrain",
                matrix=column_major(m),
                visible=visible,
                aabb=bounds(corners),
                size=hf.size,
                rows=hf.rows,
                cols=hf.cols,
                heights=[round(h, 3) for h in hf.heights],
                layer_materials=layer_materials,
                layers=hf.layers,
            )
        )
        if node.water_level is not None:
            wm = mul(m, translate(0.0, node.water_level - 0.02, 0.0))
            self._shape(
                f"{node.id}/water",
                self._ref(node.id),
                "water",
                "box",
                (hf.size[0], 0.02, hf.size[1]),
                wm,
                node.water_material or self._preset("water"),
                visible,
            )

    def _path(self, node: PathNode, m: Mat, parent: Mat, visible: bool) -> None:
        line = self.path_world[node.id]
        if len(line) < 2 or node.purpose == "guide":
            return  # guide paths only steer other things: sweeps, copies, follow_path
        left, right = [], []
        half = node.width / 2
        for i, p in enumerate(line):
            a, b = line[max(0, i - 1)], line[min(len(line) - 1, i + 1)]
            tx, tz = b[0] - a[0], b[2] - a[2]
            n = math.hypot(tx, tz) or 1.0
            nx, nz = -tz / n, tx / n
            for side, out in ((1.0, left), (-1.0, right)):
                x, z = p[0] + nx * half * side, p[2] + nz * half * side
                y = p[1]
                if node.conform_to_terrain:
                    ground = self.ground_height(x, z)
                    y = (ground if ground is not None else p[1]) + 0.04
                out.append((x, y, z))
        default = {"river": "water", "road": "asphalt"}.get(node.purpose, "dirt")
        material = node.material or self._preset(default)
        positions, indices = ribbon_mesh(left, right)
        self._add(
            LMesh(
                id=node.id,
                node=self._ref(node.id),
                role="path",
                matrix=column_major(identity()),
                visible=visible,
                aabb=bounds(left + right),
                positions=positions,
                indices=indices,
                material=material,
                flat=False,
                double_sided=True,
            )
        )

    # Rooms and walls -------------------------------------------------------------------

    def _room(self, node: RoomNode, m: Mat, parent: Mat, visible: bool) -> None:
        outline = [tuple(p) for p in node.outline]
        floor = node.floor_material or self._preset("floor_parquet")
        wall_default = node.wall_material or self._preset("plaster_white")
        ceiling = node.ceiling_material or wall_default
        ref = self._ref(node.id)
        self._add(
            LSlab(
                id=f"{node.id}/floor",
                node=ref,
                role="floor",
                matrix=column_major(m),
                visible=visible,
                aabb=bounds([apply(m, (x, y, z)) for x, z in outline for y in (0.0, -FLOOR_THICKNESS)]),
                polygon=outline,
                thickness=FLOOR_THICKNESS,
                material=floor,
            )
        )
        if node.has_ceiling:
            cm = mul(m, translate(0.0, node.height + CEILING_THICKNESS, 0.0))
            self._add(
                LSlab(
                    id=f"{node.id}/ceiling",
                    node=ref,
                    role="ceiling",
                    matrix=column_major(cm),
                    visible=visible,
                    aabb=bounds([apply(cm, (x, y, z)) for x, z in outline for y in (0.0, -CEILING_THICKNESS)]),
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
            self._wall(
                node.id,
                f"{node.id}/wall{edge}",
                m,
                a,
                b,
                override.height if override and override.height else node.height,
                node.wall_thickness,
                override.material if override and override.material else wall_default,
                [o for o in node.openings if o.wall == edge],
                1.0 if area > 0 else -1.0,
                "outside",
                visible,
                self.world.rules.door_clearance,
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
        mode: str,
        visible: bool,
        clearance: float,
        cutaway: bool = True,
        clear_outside: bool = False,
    ) -> None:
        """One straight wall from a to b, split into boxes around its openings.

        mode 'outside': room walls sit outside the outline so the outline stays the interior
        face, and run past both corners by their thickness to close convex corners.
        mode 'inset': building walls sit inside the footprint. mode 'centred': on the line.
        """
        dx, dz = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dz)
        if length < 1e-6:
            return
        dx, dz = dx / length, dz / length
        nx, nz = (dz * outward_sign, -dx * outward_sign)
        offset = {"outside": thickness / 2, "inset": -thickness / 2, "centred": 0.0}[mode]
        yaw = yaw_of(dx, dz)
        world_n = apply_dir(m, (nx, 0.0, nz))
        facing = (round(world_n[0], 4), round(world_n[2], 4)) if cutaway else None
        ref = self._ref(node_id)

        def piece(name: str, u0: float, u1: float, y0: float, y1: float) -> None:
            if u1 - u0 < 1e-4 or y1 - y0 < 1e-4:
                return
            u = (u0 + u1) / 2
            cx, cz = a[0] + dx * u + nx * offset, a[1] + dz * u + nz * offset
            pm = mul(m, mul(translate(cx, y0, cz), rot_y(yaw)))
            self._shape(
                f"{prefix}/{name}", ref, "wall", "box", (u1 - u0, y1 - y0, thickness), pm, material, visible, facing
            )

        extend = thickness if mode == "outside" else 0.0
        cursor = -extend
        for i, o in enumerate(sorted(openings, key=lambda o: o.offset)):
            o0, o1 = o.offset - o.width / 2, o.offset + o.width / 2
            top = min(o.sill + o.height, height)
            piece(f"{i}_before", cursor, o0, 0.0, height)
            piece(f"{i}_above", o0, o1, top, height)
            piece(f"{i}_below", o0, o1, 0.0, o.sill)
            cursor = max(cursor, o1)
            self._opening(
                ref, prefix, m, o, a, (dx, dz), (nx, nz), offset, yaw, visible, clearance, facing, clear_outside
            )
        piece("end", cursor, length + extend, 0.0, height)

    def _opening(
        self,
        ref: str,
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
        facing: Optional[tuple[float, float]],
        clear_outside: bool = False,
    ) -> None:
        dx, dz = d
        nx, nz = n
        mid = lambda u: (a[0] + dx * u + nx * offset, a[1] + dz * u + nz * offset)  # noqa: E731
        if o.type == "window":
            cx, cz = mid(o.offset)
            pm = mul(m, mul(translate(cx, o.sill, cz), rot_y(yaw)))
            glass = self._preset("glass_clear")
            self._shape(
                f"{prefix}/{o.id}",
                ref,
                "window",
                "box",
                (o.width, o.height, GLASS_THICKNESS),
                pm,
                glass,
                visible,
                facing,
            )
            return
        if o.type != "door":
            return
        # Hinge side is judged from inside, looking out through the door (along +n).
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
                v = apply_dir(rot_y(sign * angle), (dx * toward_free, 0.0, dz * toward_free))
                options.append((v[0] * into[0] + v[2] * into[1], v))
            panel_dir = max(options)[1]
        else:
            panel_dir = (dx * toward_free, 0.0, dz * toward_free)
        hx, hz = mid(hinge_u)
        width = o.width - 0.02
        cx, cz = hx + panel_dir[0] * width / 2, hz + panel_dir[2] * width / 2
        pm = mul(m, mul(translate(cx, o.sill, cz), rot_y(yaw_of(panel_dir[0], panel_dir[2]))))
        size = (width, o.height - 0.01, DOOR_PANEL_THICKNESS)
        self._shape(f"{prefix}/{o.id}", ref, "door", "box", size, pm, self._preset("wood_oak"), visible, facing)
        # The free zone in front of the door: inside a room, outside a building.
        depth = o.clearance or clearance
        sx, sz = (nx, nz) if clear_outside else (-nx, -nz)
        p0 = (a[0] + dx * (o.offset - o.width / 2), a[1] + dz * (o.offset - o.width / 2))
        p1 = (a[0] + dx * (o.offset + o.width / 2), a[1] + dz * (o.offset + o.width / 2))
        corners = [p0, p1, (p1[0] + sx * depth, p1[1] + sz * depth), (p0[0] + sx * depth, p0[1] + sz * depth)]
        polygon = [(round(p[0], 4), round(p[2], 4)) for p in (apply(m, (x, 0.0, z)) for x, z in corners)]
        base = apply(m, (0.0, 0.0, 0.0))[1]
        self._add(
            LZone(
                id=f"{prefix}/{o.id}/clearance",
                node=ref,
                role="door_clearance",
                matrix=column_major(m),
                visible=visible,
                purpose="clearance",
                polygon=polygon,
                y_range=(round(base, 4), round(base + 2.0, 4)),
                label=f"clearance of {o.id}",
            )
        )

    # Scatter (runs last, so it can avoid everything already placed) ---------------------

    def lower_scatters(self) -> None:
        for node, m, visible, owner in self.deferred:
            previous, self._owner = self._owner, owner
            self._scatter(node, m, visible)
            self._owner = previous

    def _subtree(self, node_id: str) -> set[str]:
        out = {node_id}
        for child in self.children_of.get(node_id, []):
            out |= self._subtree(child)
        return out

    def _scatter(self, node: ScatterNode, m: Mat, visible: bool) -> None:
        if node.area is not None:
            poly = [(p[0], p[2]) for p in (apply(m, (x, 0.0, z)) for x, z in node.area)]
        else:
            poly = self.zone_world.get(node.zone, [])
        if len(poly) < 3:
            return
        area = (
            abs(
                sum(
                    poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                    for i in range(len(poly))
                )
            )
            / 2
        )
        target = node.count if node.count is not None else int(node.density * area)
        if target > SCATTER_CAP:
            self.warnings.append(f"scatter '{node.id}': capped at {SCATTER_CAP} copies")
            target = SCATTER_CAP
        margin = max(node.min_spacing / 2, 0.4)
        avoid_polys, avoid_lines, avoid_boxes = [], [], []
        for ref in node.avoid:
            if ref in self.zone_world:
                avoid_polys.append(self.zone_world[ref])
            elif ref in self.path_world:
                avoid_lines.append((self.path_world[ref], self.path_width[ref] / 2 + margin))
            else:
                ids = self._subtree(ref)
                for item in self.items:
                    if (
                        item.node in ids
                        and getattr(item, "aabb", None)
                        and item.type not in ("zone", "light", "camera")
                    ):
                        (x0, _, z0), (x1, _, z1) = item.aabb
                        avoid_boxes.append((x0 - margin, z0 - margin, x1 + margin, z1 + margin))
        if node.align_to_slope > 0:
            self.warnings.append(f"scatter '{node.id}': align_to_slope is not applied yet")
        rng = random.Random(node.seed * 7919 + self.world.seed)
        xs, zs = [p[0] for p in poly], [p[1] for p in poly]
        cell = max(node.min_spacing, 0.05)
        grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
        weights = [item.weight for item in node.items]
        placed: dict[str, list[tuple[float, float, float, float, float]]] = {}
        accepted = 0
        floor_y = apply(m, (0.0, 0.0, 0.0))[1]
        for _ in range(target * 30):
            if accepted >= target:
                break
            x, z = rng.uniform(min(xs), max(xs)), rng.uniform(min(zs), max(zs))
            if not point_in_polygon(x, z, poly):
                continue
            if any(point_in_polygon(x, z, p) for p in avoid_polys):
                continue
            if any(b[0] <= x <= b[2] and b[1] <= z <= b[3] for b in avoid_boxes):
                continue
            if any(
                min(segment_distance(x, z, (p[0], p[2]), (q[0], q[2]))[0] for p, q in zip(line, line[1:])) < reach
                for line, reach in avoid_lines
            ):
                continue
            gx, gz = int(math.floor(x / cell)), int(math.floor(z / cell))
            if any(
                math.dist((x, z), other) < node.min_spacing
                for i in (-1, 0, 1)
                for j in (-1, 0, 1)
                for other in grid.get((gx + i, gz + j), [])
            ):
                continue
            if node.on == "terrain":
                y = self.ground_height(x, z)
                if y is None or self.slope_at(x, z) > node.max_slope_deg:
                    continue
            else:
                y = floor_y
            if node.height_range and not (node.height_range[0] <= y <= node.height_range[1]):
                continue
            index = rng.choices(range(len(node.items)), weights=weights)[0]
            item = node.items[index]
            s = rng.uniform(*item.scale)
            yaw = rng.uniform(*item.yaw)
            placed.setdefault(index, []).append((x, y, z, yaw, s))
            grid.setdefault((gx, gz), []).append((x, z))
            accepted += 1
        assets = [item.asset for item in node.items]
        for index, transforms in sorted(placed.items()):
            item = node.items[index]
            name = item.asset if assets.count(item.asset) == 1 else f"{item.asset}_{index}"
            dims = self.world.assets[item.asset].dims
            materials = {o.slot: o.material for o in item.materials}
            self._instances(
                node, name, "scatter", transforms, visible, identity(), dims, asset=item.asset, materials=materials
            )


def lower(world: World, lowerer: Optional["_Lowerer"] = None) -> LoweredScene:
    """Lowers a validated world. Deterministic: the same world always gives the same scene.

    Pass a fresh ``_Lowerer`` to keep its ground heights and node matrices for later checks.
    """
    lowerer = lowerer or _Lowerer(world)
    for root in world.nodes:
        lowerer.lower_node(root, identity(), True)
    lowerer.lower_scatters()
    ids = [item.id for item in lowerer.items]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"lowering produced duplicate item ids: {duplicates}")
    boxes = [item.aabb for item in lowerer.items if getattr(item, "aabb", None)]
    scene_bounds = None
    if boxes:
        scene_bounds = (
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
        bounds=scene_bounds,
        behaviors=lowerer.behaviors,
        unsupported=lowerer.unsupported,
        extras={"warnings": sorted(set(lowerer.warnings))} if lowerer.warnings else {},
    )
