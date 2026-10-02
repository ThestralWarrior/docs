"""Room layout: furniture placed against walls, beside, in front of and on top of other pieces.

The world generator furnishes rooms with it. Footprints and overlaps use the
same convex-polygon maths as the validators, and doors, windows and the space
in front of cupboards are reserved before anything is placed, so a layout
passes the checks by construction. Positions are in room space: the room's
outline coordinates, y up from its floor.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .furniture import Piece
from .terrain_eval import point_in_polygon
from .validate import _area, _clip

Pt = tuple[float, float]

DOOR_DEPTH = 1.0
"""Depth kept free in front of a door: the validators' 0.9 m plus a margin."""


def footprint(x: float, z: float, w: float, d: float, yaw: float) -> list[Pt]:
    """Corners of a w × d box centred at (x, z), turned by yaw (degrees, +z toward +x)."""
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return [
        (x + c * lx + s * lz, z - s * lx + c * lz)
        for lx, lz in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))
    ]


def local_to_room(x: float, z: float, yaw: float, lx: float, lz: float) -> Pt:
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return (x + c * lx + s * lz, z - s * lx + c * lz)


def yaw_toward(x: float, z: float, tx: float, tz: float) -> float:
    return math.degrees(math.atan2(tx - x, tz - z)) % 360


def _cross(o: Pt, a: Pt, b: Pt) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_cross(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> bool:
    """True when two segments properly cross (touching at an end does not count)."""
    d1, d2 = _cross(q1, q2, p1), _cross(q1, q2, p2)
    d3, d4 = _cross(p1, p2, q1), _cross(p1, p2, q2)
    return ((d1 > 1e-9 and d2 < -1e-9) or (d1 < -1e-9 and d2 > 1e-9)) and (
        (d3 > 1e-9 and d4 < -1e-9) or (d3 < -1e-9 and d4 > 1e-9)
    )


def ccw(poly: list[Pt]) -> list[Pt]:
    signed = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))
    return poly if signed > 0 else poly[::-1]


@dataclass
class Wall:
    """One edge of the room outline, seen from inside."""

    index: int
    a: Pt
    b: Pt

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def dir(self) -> Pt:
        n = self.length or 1.0
        return ((self.b[0] - self.a[0]) / n, (self.b[1] - self.a[1]) / n)

    @property
    def normal(self) -> Pt:
        """Inward normal, for a counter-clockwise outline."""
        dx, dz = self.dir
        return (-dz, dx)

    @property
    def yaw(self) -> float:
        """Yaw of an object whose back is against this wall."""
        nx, nz = self.normal
        return round(math.degrees(math.atan2(nx, nz)) % 360, 6)

    def point(self, u: float, inset: float) -> Pt:
        dx, dz = self.dir
        nx, nz = self.normal
        return (self.a[0] + dx * u + nx * inset, self.a[1] + dz * u + nz * inset)


@dataclass
class Box:
    """Something that takes up room: a placed piece, a door's swing, a window, space kept clear."""

    id: str
    poly: list[Pt]
    y0: float
    y1: float
    kind: str = "item"


@dataclass
class Placed:
    """A piece where the layout put it. x, y, z and yaw are in room space."""

    id: str
    piece: Piece
    x: float
    y: float
    z: float
    yaw: float
    poly: list[Pt]
    support: str = "floor"
    on: Optional[str] = None
    child: bool = False
    local: Optional[tuple[float, float, float, float]] = None
    wall: Optional[int] = None
    optional: bool = True
    group: Optional[str] = None
    rot: Optional[tuple[float, float, float]] = None
    intent: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, str]] = field(default_factory=list)
    light: Optional[dict[str, Any]] = None
    name: Optional[str] = None
    role: Optional[str] = None
    category: Optional[str] = None
    span: Optional[tuple[float, float]] = None

    @property
    def semantic_category(self) -> str:
        return self.category or self.piece.category

    @property
    def top(self) -> float:
        return self.y + self.piece.h

    def to_room(self, lx: float, lz: float) -> Pt:
        return local_to_room(self.x, self.z, self.yaw, lx, lz)

    @property
    def front(self) -> Pt:
        return (math.sin(math.radians(self.yaw)), math.cos(math.radians(self.yaw)))


class Layout:
    """One room being furnished: its walls, what is reserved, and what has been placed."""

    def __init__(
        self, rng: random.Random, room_id: str, outline: list[Pt], height: float, openings: list[dict[str, Any]]
    ):
        self.rng = rng
        self.room = room_id
        self.outline = ccw(outline)
        if self.outline is not outline:
            raise ValueError("outlines must run counter-clockwise")
        self.height = height
        self.openings = openings
        self.walls = [Wall(i, outline[i], outline[(i + 1) % len(outline)]) for i in range(len(outline))]
        self.boxes: list[Box] = []
        self.placed: dict[str, Placed] = {}
        self.relations: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        xs, zs = [p[0] for p in outline], [p[1] for p in outline]
        self.bounds = (min(xs), min(zs), max(xs), max(zs))
        for o in openings:
            wall = self.walls[o["wall"]]
            u0, u1 = o["offset"] - o["width"] / 2, o["offset"] + o["width"] / 2
            if o["type"] in ("door", "archway"):
                depth = o.get("clearance", DOOR_DEPTH)
                poly = [
                    wall.point(u0 - 0.1, 0.0),
                    wall.point(u1 + 0.1, 0.0),
                    wall.point(u1 + 0.1, depth),
                    wall.point(u0 - 0.1, depth),
                ]
                self.boxes.append(Box(f"{o['id']}_swing", poly, 0.0, min(height, 2.3), "door"))
            else:
                poly = [
                    wall.point(u0 - 0.05, 0.0),
                    wall.point(u1 + 0.05, 0.0),
                    wall.point(u1 + 0.05, 0.35),
                    wall.point(u0 - 0.05, 0.35),
                ]
                self.boxes.append(Box(f"{o['id']}_light", poly, o["sill"] - 0.02, o["sill"] + o["height"], "window"))

    # Bookkeeping ------------------------------------------------------------------------

    def new_id(self, stem: str) -> str:
        stem = "".join(ch if ch.isalnum() else "_" for ch in stem.lower()).strip("_")[:40] or "item"
        n = self.counts.get(stem, 0) + 1
        self.counts[stem] = n
        return stem if n == 1 else f"{stem}_{n}"

    def relate(self, rel: str, **fields: Any) -> None:
        self.relations.append({"rel": rel, **fields})

    def add(self, p: Placed, keep_clear: Optional[list[Pt]] = None) -> Placed:
        self.placed[p.id] = p
        if p.piece.mount != "covering":
            y0, y1 = p.span or (p.y, p.top)
            self.boxes.append(Box(p.id, p.poly, y0, y1))
        if keep_clear:
            self.boxes.append(Box(f"{p.id}_front", keep_clear, 0.0, 2.2, "keep_clear"))
        return p

    def remove(self, node_id: str) -> None:
        gone = {node_id} | {k for k, p in self.placed.items() if p.on == node_id}
        for k in list(gone):
            self.placed.pop(k, None)
        self.boxes = [b for b in self.boxes if b.id not in gone and b.id.removesuffix("_front") not in gone]

    # Geometry tests ---------------------------------------------------------------------

    def inside(self, poly: list[Pt]) -> bool:
        if not all(point_in_polygon(x, z, self.outline) for x, z in poly):
            return False
        edges = list(zip(self.outline, self.outline[1:] + self.outline[:1]))
        for p, q in zip(poly, poly[1:] + poly[:1]):
            if any(segments_cross(p, q, a, b) for a, b in edges):
                return False
        return True

    def fits(
        self,
        poly: list[Pt],
        y0: float,
        y1: float,
        allow: Optional[dict[str, float]] = None,
        ignore: tuple[str, ...] = (),
        kinds: tuple[str, ...] = ("item", "door", "window", "keep_clear"),
    ) -> bool:
        allow = allow or {}
        for b in self.boxes:
            if b.id in ignore or b.kind not in kinds:
                continue
            if min(y1, b.y1) - max(y0, b.y0) <= 0.0:
                continue
            inter = _clip(poly, b.poly)
            if len(inter) < 3:
                continue
            area = _area(inter)
            if area <= 1e-6:
                continue
            if b.id in allow and area / max(1e-9, min(_area(poly), _area(b.poly))) <= allow[b.id]:
                continue
            return False
        return True

    def free_at(
        self,
        piece: Piece,
        x: float,
        z: float,
        yaw: float,
        y: float = 0.0,
        margin: float = 0.05,
        allow: Optional[dict[str, float]] = None,
        ignore: tuple[str, ...] = (),
    ) -> Optional[list[Pt]]:
        """The footprint if the piece fits here, else None."""
        poly = footprint(x, z, piece.w, piece.d, yaw)
        if not self.inside(poly):
            return None
        if piece.mount == "covering":
            return poly
        test = footprint(x, z, piece.w + 2 * margin, piece.d + 2 * margin, yaw) if margin else poly
        top = min(y + piece.h, self.height - 0.01)
        if not self.fits(test, y, top, allow, ignore):
            return None
        return poly

    def band(self, x: float, z: float, w: float, d: float, yaw: float, depth: float) -> list[Pt]:
        """The strip of floor in front of a piece."""
        cx, cz = local_to_room(x, z, yaw, 0.0, d / 2 + depth / 2)
        return footprint(cx, cz, w, depth, yaw)

    # Placement ---------------------------------------------------------------------------

    def put(
        self, piece: Piece, x: float, z: float, yaw: float, *, y: float = 0.0, stem: Optional[str] = None, **kw: Any
    ) -> Placed:
        poly = footprint(x, z, piece.w, piece.d, yaw)
        return Placed(
            self.new_id(stem or kw.get("category") or piece.category),
            piece,
            round(x, 3),
            round(y, 3),
            round(z, 3),
            yaw % 360,
            poly,
            **kw,
        )

    def against_wall(
        self,
        piece: Piece,
        *,
        walls: Optional[list[Wall]] = None,
        where: str = "any",
        gap: float = 0.01,
        margin: float = 0.05,
        front_clear: float = 0.0,
        tries: int = 30,
        stem: Optional[str] = None,
        relate: bool = True,
        **kw: Any,
    ) -> Optional[Placed]:
        """Back to a wall. where: 'any', 'middle', 'start', 'end' or 'corner'."""
        options = [w for w in (walls or self.walls) if w.length >= piece.w + 0.04]
        if not options:
            return None
        for _ in range(tries):
            wall = self.rng.choice(options)
            lo, hi = piece.w / 2 + 0.02, wall.length - piece.w / 2 - 0.02
            if hi < lo:
                continue
            if where == "middle":
                u = min(hi, max(lo, wall.length / 2 + self.rng.uniform(-0.25, 0.25)))
            elif where in ("start", "end", "corner"):
                u = lo if where == "start" or (where == "corner" and self.rng.random() < 0.5) else hi
            else:
                u = self.rng.uniform(lo, hi)
            x, z = wall.point(u, gap + piece.d / 2)
            yaw = wall.yaw
            poly = self.free_at(piece, x, z, yaw, margin=margin)
            if poly is None:
                continue
            clear = None
            if front_clear:
                clear = self.band(x, z, piece.w, piece.d, yaw, front_clear)
                if not self.inside(clear) or not self.fits(clear, 0.0, 2.2, kinds=("item", "keep_clear")):
                    continue
            p = self.put(piece, x, z, yaw, stem=stem, wall=wall.index, **kw)
            self.add(p, clear)
            if relate:
                self.relate("against_wall", a=p.id, wall=f"{self.room}:{wall.index}", side="back")
            return p
        return None

    def beside(
        self,
        anchor: Placed,
        piece: Piece,
        side: str,
        *,
        gap: float = 0.05,
        align: str = "back",
        margin: float = 0.03,
        stem: Optional[str] = None,
        **kw: Any,
    ) -> Optional[Placed]:
        """Next to an anchor on its left (+x) or right (-x), backs lined up."""
        sign = 1.0 if side == "left" else -1.0
        lx = sign * (anchor.piece.w / 2 + gap + piece.w / 2)
        lz = {"back": -anchor.piece.d / 2 + piece.d / 2, "front": anchor.piece.d / 2 - piece.d / 2}.get(align, 0.0)
        x, z = anchor.to_room(lx, lz)
        poly = self.free_at(piece, x, z, anchor.yaw, margin=margin, ignore=(anchor.id,))
        if poly is None or not self.fits(poly, 0.0, piece.h, ignore=()):
            return None
        p = self.put(piece, x, z, anchor.yaw, stem=stem, wall=anchor.wall, **kw)
        return self.add(p)

    def in_front(
        self,
        anchor: Placed,
        piece: Piece,
        dist: float,
        *,
        turn: float = 0.0,
        lateral: float = 0.0,
        margin: float = 0.05,
        allow: Optional[dict[str, float]] = None,
        stem: Optional[str] = None,
        **kw: Any,
    ) -> Optional[Placed]:
        """In front of an anchor: dist from its front edge to the piece's near edge (negative tucks it in)."""
        lz = anchor.piece.d / 2 + dist + piece.d / 2
        x, z = anchor.to_room(lateral, lz)
        yaw = (anchor.yaw + turn) % 360
        poly = self.free_at(piece, x, z, yaw, margin=margin, allow=allow)
        if poly is None:
            return None
        return self.add(self.put(piece, x, z, yaw, stem=stem, **kw))

    def free(
        self,
        piece: Piece,
        *,
        margin: float = 0.3,
        yaws: Optional[list[float]] = None,
        tries: int = 60,
        near: Optional[Pt] = None,
        spread: float = 0.6,
        stem: Optional[str] = None,
        allow: Optional[dict[str, float]] = None,
        **kw: Any,
    ) -> Optional[Placed]:
        """Anywhere on the floor with room around it; near a point when given."""
        x0, z0, x1, z1 = self.bounds
        yaws = yaws or sorted({w.yaw for w in self.walls})
        for i in range(tries):
            if near is not None and i < tries // 2:
                x, z = near[0] + self.rng.gauss(0, spread * (0.2 + i / tries)), near[1] + self.rng.gauss(
                    0, spread * (0.2 + i / tries)
                )
            else:
                x, z = self.rng.uniform(x0, x1), self.rng.uniform(z0, z1)
            yaw = self.rng.choice(yaws)
            poly = self.free_at(piece, x, z, yaw, margin=margin, allow=allow)
            if poly is not None:
                return self.add(self.put(piece, x, z, yaw, stem=stem, **kw))
        return None

    def on_top(
        self,
        base: Placed,
        piece: Piece,
        *,
        where: str = "any",
        turn: float = 0.0,
        child: bool = True,
        margin: float = 0.03,
        tries: int = 16,
        stem: Optional[str] = None,
        relate: bool = True,
        **kw: Any,
    ) -> Optional[Placed]:
        """On the base's top surface. where: 'any', 'back', 'centre', 'left', 'right'."""
        if base.piece.top is None or base.rot is not None:
            return None
        x0, z0, x1, z1 = base.piece.area or (-base.piece.w / 2, -base.piece.d / 2, base.piece.w / 2, base.piece.d / 2)
        quarter = round(turn / 90) % 2 == 1
        hw, hd = (piece.d / 2, piece.w / 2) if quarter else (piece.w / 2, piece.d / 2)
        lo_x, hi_x, lo_z, hi_z = x0 + hw + 0.01, x1 - hw - 0.01, z0 + hd + 0.01, z1 - hd - 0.01
        if hi_x < lo_x or hi_z < lo_z:
            return None
        y = base.y + base.piece.top
        if y + piece.h > self.height - 0.05:
            return None
        for _ in range(tries):
            lx = {"centre": (lo_x + hi_x) / 2, "left": hi_x, "right": lo_x}.get(where, self.rng.uniform(lo_x, hi_x))
            lz = {"back": lo_z, "centre": (lo_z + hi_z) / 2}.get(where, self.rng.uniform(lo_z, hi_z))
            if where in ("left", "right"):
                lz = self.rng.uniform(lo_z, hi_z)
            x, z = base.to_room(lx, lz)
            yaw = (base.yaw + turn) % 360
            poly = footprint(x, z, piece.w, piece.d, yaw)
            test = footprint(x, z, piece.w + 2 * margin, piece.d + 2 * margin, yaw)
            if not self.fits(test, y, y + piece.h, ignore=(base.id,)):
                continue
            p = Placed(
                self.new_id(stem or piece.category),
                piece,
                round(x, 3),
                round(y, 3),
                round(z, 3),
                yaw,
                poly,
                support="node",
                on=base.id,
                child=child,
                local=(round(lx, 3), base.piece.top, round(lz, 3), turn % 360),
                **kw,
            )
            self.add(p)
            if relate:
                self.relate("on", a=p.id, b=base.id, surface="top")
            return p
        return None

    def on_wall(
        self,
        piece: Piece,
        wall: Wall,
        u: float,
        base_y: float,
        *,
        gap: float = 0.01,
        margin: float = 0.03,
        ignore: tuple[str, ...] = (),
        stem: Optional[str] = None,
        **kw: Any,
    ) -> Optional[Placed]:
        """Hung on a wall: its back against it, its base at base_y."""
        if u - piece.w / 2 < 0.02 or u + piece.w / 2 > wall.length - 0.02:
            return None
        if base_y + piece.h > self.height - 0.02:
            return None
        x, z = wall.point(u, gap + piece.d / 2)
        poly = footprint(x, z, piece.w, piece.d, wall.yaw)
        if not self.inside(poly):
            return None
        test = footprint(x, z, piece.w + 2 * margin, piece.d + 2 * margin, wall.yaw)
        if not self.fits(test, base_y, base_y + piece.h, ignore=ignore):
            return None
        p = self.put(piece, x, z, wall.yaw, y=base_y, stem=stem, support="wall", wall=wall.index, **kw)
        return self.add(p)

    def from_ceiling(
        self, piece: Piece, x: float, z: float, *, yaw: float = 0.0, stem: Optional[str] = None, **kw: Any
    ) -> Optional[Placed]:
        y = self.height - piece.h
        poly = self.free_at(piece, x, z, yaw, y=y, margin=0.05)
        if poly is None:
            return None
        p = self.add(self.put(piece, x, z, yaw, y=y, stem=stem, support="ceiling", **kw))
        self.relate("hangs_from_ceiling", a=p.id, drop=[0.0, 0.02])
        return p

    def room_centre(self) -> Pt:
        """A point well inside the room: the bounds' centre if inside, else the centre of the largest wall-hugging rectangle."""
        x0, z0, x1, z1 = self.bounds
        c = ((x0 + x1) / 2, (z0 + z1) / 2)
        if point_in_polygon(c[0], c[1], self.outline):
            return c
        xs = [p[0] for p in self.outline]
        return (sum(xs) / len(xs), sum(p[1] for p in self.outline) / len(self.outline))

    def floor_share(self) -> float:
        used = sum(_area(p.poly) for p in self.placed.values() if p.support == "floor" and p.piece.mount != "covering")
        return used / max(1e-9, _area(self.outline))
