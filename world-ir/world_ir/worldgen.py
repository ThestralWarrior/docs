"""Clean-world generator: furnished rooms built to pass every check, for training data and tests.

``generate(seed)`` picks a room type, a shape (rectangle or L), its size,
doors and windows, materials, the time of day and a furniture programme for
that room type, and lays the furniture out with the same geometry the
validators use (``layout.py``). It writes down the relations the layout
satisfies (against a wall, beside, facing, on top of, hanging from the
ceiling, counts, required and forbidden categories) and a brief in the words a
user might type. Some rooms get a theme whose licence (abandoned, haunted,
surreal, zero gravity, ...) lets the builder break the default checks on
purpose: a chair knocked over, a lamp floating, a bed sunk into the floor. Each
such oddity carries its intent, so it validates as deliberate.

Then the world is validated. An optional piece that still fails is dropped and
the world is checked again; a world that cannot be made clean is redrawn from
the next sub-seed. What comes out has no errors, no warnings and no questions.
The same seed always gives the same world.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .furniture import Piece, piece
from .layout import Layout, Placed, Pt, Wall, footprint, yaw_toward
from .validate import ValidationReport, _poly_distance, validate
from .world import World

ROOM_KINDS = [
    "bedroom",
    "kids_room",
    "living_room",
    "kitchen",
    "dining_room",
    "bathroom",
    "office",
    "library",
    "classroom",
    "cafe",
    "laundry",
    "hallway",
    "basement",
    "lobby",
]

THEMES = {
    "abandoned": ["knocked_over", "knocked_over", "leaning"],
    "haunted": ["floating", "knocked_over", "ceiling_chair"],
    "horror": ["ceiling_chair", "knocked_over", "floating"],
    "creepy": ["floating", "leaning"],
    "messy": ["knocked_over", "pile"],
    "cluttered": ["pile", "knocked_over"],
    "surreal": ["sunk", "floating", "ceiling_chair"],
    "dreamlike": ["floating", "sunk"],
    "zero_gravity": ["floating", "floating", "floating"],
}
"""Licence → the kinds of deliberate oddity it allows the builder."""

THEMED_ROOMS = {
    "bedroom",
    "kids_room",
    "living_room",
    "kitchen",
    "dining_room",
    "office",
    "library",
    "classroom",
    "basement",
    "lobby",
}

STYLES = {
    "scandinavian": ["#ece9e2", "#b58a5a", "#2f5a46", "#d9c7a7"],
    "industrial": ["#8a8d91", "#3b3b3b", "#b5651d", "#c9c2b8"],
    "mid_century": ["#e8d5b7", "#d35400", "#2c3e50", "#7a9e7e"],
    "japanese": ["#f4efe6", "#8b6f47", "#3a5a40", "#c9b79c"],
    "cottage": ["#f6efe0", "#c08497", "#6b8f71", "#e3c9a8"],
    "minimalist": ["#f5f5f5", "#d0d0d0", "#222222", "#8c8c8c"],
    "rustic": ["#e9dcc9", "#8b5a2b", "#5b7553", "#a47551"],
    "victorian": ["#e8dfd0", "#6d1a36", "#2f4f4f", "#b08d57"],
    "brutalist": ["#b7b4ae", "#5e5b57", "#9c2f2f", "#d8d4cc"],
    "stylised": ["#fbe8d3", "#f08a5d", "#3e8ed0", "#b83b5e"],
}

MOODS = [
    "cosy",
    "bright",
    "calm",
    "airy",
    "warm",
    "quiet",
    "cheerful",
    "elegant",
    "relaxed",
    "tidy",
    "modern",
    "homely",
]

FLOORS = {
    "wood": [
        ("floor_parquet", ["#a87a4f", "#c19a6b", "#8b5a3c", "#d2b48c"]),
        ("wood_oak", ["#b98b5e", "#9c6b43"]),
        ("wood_walnut", ["#6b4a33", "#5a3d2b"]),
        ("wood_pine", ["#d9b77e", "#caa472"]),
    ],
    "tile": [
        ("tile_ceramic_white", ["#f0f0f0", "#d9d4c7", "#9fb4c7", "#c8c8c8", "#2f3a45"]),
        ("stone_grey", ["#8f8f8f", "#a9a39a"]),
    ],
    "carpet": [("fabric_linen", ["#b7a99a", "#8e9aaf", "#a3b18a", "#c9ada7", "#6d6875"])],
    "hard": [("concrete", ["#9a9a9a", "#b5b1a8", "#7d7a75"]), ("stone_grey", ["#8f8f8f", "#a9a39a"])],
}

WALLS = [
    ("plaster_white", ["#ece9e2", "#f4f1ea", "#e6e1d8"]),
    ("paint_warm_grey", ["#cfc8bd", "#b9b2a7", "#d8d2c4"]),
    ("plaster_white", ["#dfe7e2", "#e8dfd2", "#e4e8ef", "#f1e3d3", "#d7e3ea"]),
    ("brick_red", ["#a0522d", "#8b4513", "#9b5b45"]),
    ("concrete", ["#a9a6a0", "#8f8c86"]),
    ("wood_pine", ["#d9b77e", "#c9a46e"]),
]

TIMES = [
    ("07:30", 10, 95, 4000, 1.6, "#f3c79a"),
    ("10:00", 35, 120, 5200, 2.6, "#cfe3f5"),
    ("13:00", 60, 180, 5600, 3.0, "#bcd8f2"),
    ("16:30", 28, 235, 5000, 2.4, "#e9d7b8"),
    ("19:00", 6, 280, 3200, 1.4, "#e8a272"),
    ("22:30", None, 0, 0, 0, "#1b2233"),
]

SIZES = {
    "bedroom": ((3.0, 5.2), (3.0, 4.6), (2.5, 2.9)),
    "kids_room": ((3.0, 4.6), (3.0, 4.2), (2.5, 2.8)),
    "living_room": ((3.8, 6.8), (3.6, 6.0), (2.6, 3.0)),
    "kitchen": ((2.6, 4.8), (2.6, 5.0), (2.5, 2.8)),
    "dining_room": ((3.4, 5.6), (3.2, 5.0), (2.6, 3.0)),
    "bathroom": ((1.9, 3.4), (1.9, 3.4), (2.4, 2.7)),
    "office": ((2.8, 5.2), (2.8, 5.0), (2.5, 2.9)),
    "library": ((4.0, 7.5), (3.6, 6.5), (2.7, 3.4)),
    "classroom": ((6.0, 9.0), (5.4, 7.6), (2.8, 3.2)),
    "cafe": ((5.2, 9.0), (4.6, 7.6), (2.8, 3.4)),
    "laundry": ((1.9, 3.2), (2.0, 3.4), (2.4, 2.7)),
    "hallway": ((1.7, 2.5), (3.4, 6.5), (2.5, 2.8)),
    "basement": ((3.0, 6.0), (3.0, 5.6), (2.3, 2.6)),
    "lobby": ((4.0, 7.5), (3.6, 6.5), (2.7, 3.2)),
}

FLOOR_KIND = {
    "bedroom": ["wood", "wood", "carpet"],
    "kids_room": ["wood", "carpet"],
    "living_room": ["wood", "wood", "carpet", "tile"],
    "kitchen": ["tile", "tile", "wood", "hard"],
    "dining_room": ["wood", "tile"],
    "bathroom": ["tile"],
    "office": ["wood", "carpet", "hard"],
    "library": ["wood", "carpet"],
    "classroom": ["wood", "hard", "tile"],
    "cafe": ["wood", "tile", "hard"],
    "laundry": ["tile", "hard"],
    "hallway": ["wood", "tile"],
    "basement": ["hard"],
    "lobby": ["tile", "hard", "wood"],
}

NAMES = {
    "kids_room": "kids' room",
    "living_room": "living room",
    "dining_room": "dining room",
    "laundry": "laundry room",
    "lobby": "waiting room",
}

PHRASES = {
    "bed": ["a bed", "a comfy bed"],
    "bunk bed": ["bunk beds", "a bunk bed"],
    "nightstand": ["a nightstand", "bedside tables"],
    "table lamp": ["a reading lamp", "a lamp", "a bedside lamp"],
    "desk": ["a desk", "a desk for working", "a work desk"],
    "desk chair": ["an office chair", "a desk chair"],
    "bookcase": ["a bookcase", "lots of shelves", "bookshelves"],
    "sofa": ["a sofa", "a big couch", "a comfy sofa"],
    "armchair": ["an armchair", "a reading chair"],
    "coffee table": ["a coffee table"],
    "television": ["a TV", "a television"],
    "tv cabinet": ["a TV stand"],
    "dining table": ["a dining table", "a big table"],
    "chair": ["chairs"],
    "fridge": ["a fridge", "a big fridge"],
    "stove": ["a stove", "an oven"],
    "kitchen sink": ["a sink"],
    "kitchen cabinet": ["plenty of cupboards", "counters"],
    "microwave": ["a microwave"],
    "coffee machine": ["a coffee machine"],
    "toilet": ["a toilet"],
    "bathtub": ["a bathtub", "a bath"],
    "shower": ["a shower"],
    "sink": ["a sink", "a washbasin"],
    "mirror": ["a mirror"],
    "washing machine": ["a washing machine"],
    "dryer": ["a dryer"],
    "washer dryer": ["a stacked washer and dryer"],
    "bar counter": ["a bar counter", "a coffee bar"],
    "bar stool": ["bar stools"],
    "potted plant": ["plants", "a big plant"],
    "floor lamp": ["a floor lamp"],
    "box": ["boxes", "storage boxes"],
    "bench": ["a bench"],
    "coat rack": ["a coat rack"],
    "teddy bear": ["a teddy bear"],
    "computer screen": ["a computer", "a monitor"],
    "laptop": ["a laptop"],
    "ceiling fan": ["a ceiling fan"],
    "trash can": ["a bin"],
    "speaker": ["speakers"],
    "rug": ["a rug"],
}

PURPOSES = {
    "bedroom": ["to sleep and work in", "for a student", "for guests"],
    "kids_room": ["for two kids", "for a child who loves reading"],
    "living_room": ["for movie nights", "for a family", "to relax in"],
    "kitchen": ["for a small flat", "for someone who loves cooking"],
    "dining_room": ["for family dinners", "for dinner parties"],
    "bathroom": ["for a family home", "for guests"],
    "office": ["for working from home", "for two people", "for a small startup"],
    "library": ["for quiet reading", "full of books"],
    "classroom": ["for a primary school", "for a language class"],
    "cafe": ["on a busy street", "for students"],
    "laundry": ["off the kitchen"],
    "hallway": ["at the front door", "for a busy family"],
    "basement": ["used for storage", "full of old things"],
    "lobby": ["for a small clinic", "for an office"],
}


class Retry(Exception):
    """This draw cannot be completed: start again from the next sub-seed."""


def snake(name: str) -> str:
    out = "".join("_" + c.lower() if c.isupper() else c for c in name)
    return out.replace("__", "_").strip("_")


def _matches(cat: str, category: str) -> bool:
    """The validators' category match, so counts agree with theirs."""
    return cat == category or category in cat.split() or cat.endswith(" " + category)


def _article(phrase: str) -> str:
    return ("an " if phrase[0] in "aeiou" else "a ") + phrase


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def euler_xyz(m: list[list[float]]) -> tuple[float, float, float]:
    """Three.js 'XYZ' Euler angles (degrees) of a rotation matrix."""
    m13 = max(-1.0, min(1.0, m[0][2]))
    ry = math.asin(m13)
    if abs(m13) < 0.9999999:
        rx = math.atan2(-m[1][2], m[2][2])
        rz = math.atan2(-m[0][1], m[0][0])
    else:
        rx = math.atan2(m[2][1], m[1][1])
        rz = 0.0
    return tuple(round(math.degrees(a), 2) for a in (rx, ry, rz))


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _ry(deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def _rx(deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def _rz(deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def _centre_gap(a: Placed, b: Placed) -> float:
    """Distance between two footprint centres plus a little slack, for a 'distance' relation that holds."""
    ca = (sum(p[0] for p in a.poly) / len(a.poly), sum(p[1] for p in a.poly) / len(a.poly))
    cb = (sum(p[0] for p in b.poly) / len(b.poly), sum(p[1] for p in b.poly) / len(b.poly))
    return round(math.dist(ca, cb) + 0.05, 2)


def _rotated_box(p: Piece, r: list[list[float]]) -> tuple[float, float, float, float, float, float]:
    """(min y, max y, min x, max x, min z, max z) of a piece's box rotated by r about its base centre."""
    w, h, d = p.dims
    pts = [
        [sum(r[i][k] * v[k] for k in range(3)) for i in range(3)]
        for v in ([x, y, z] for x in (-w / 2, w / 2) for y in (0, h) for z in (-d / 2, d / 2))
    ]
    ys, xs, zs = [q[1] for q in pts], [q[0] for q in pts], [q[2] for q in pts]
    return min(ys), max(ys), min(xs), max(xs), min(zs), max(zs)


@dataclass
class RoomDraw:
    """One room of a world: its programme's choices and what the brief should say about it."""

    room_type: str
    id: str
    layout: Optional[Layout] = None
    room: dict[str, Any] = field(default_factory=dict)
    mention: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    anchor: Optional[str] = None
    links: list[dict[str, Any]] = field(default_factory=list)
    shared: set[int] = field(default_factory=set)
    outside_doors: Optional[int] = None
    size: Optional[tuple[float, float, float]] = None
    offset: tuple[float, float] = (0.0, 0.0)
    zones: list[dict[str, Any]] = field(default_factory=list)
    floor: str = ""
    walls: str = ""


@dataclass
class Draw:
    """What one generated world is: the choices made, before validation."""

    seed: int
    style: str
    palette: list[str]
    mood: list[str]
    theme: Optional[str]
    time: tuple
    rooms: list[RoomDraw] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)
    materials: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    xform: Optional[dict[str, Any]] = None

    @property
    def suite(self) -> bool:
        return len(self.rooms) > 1


class _Builder:
    """Draws and furnishes one room."""

    def __init__(self, seed: int, attempt: int, room_types: list[str], theme: Optional[str]):
        self.rng = random.Random(f"worldgen:{seed}:{attempt}")
        rng = self.rng
        for kind in room_types:
            if kind not in SIZES:
                raise ValueError(f"unknown room type '{kind}'; choose from {', '.join(ROOM_KINDS)}")
        if theme is None and any(k in THEMED_ROOMS for k in room_types) and rng.random() < 0.14:
            theme = rng.choice(sorted(THEMES))
        style = rng.choice(sorted(STYLES))
        palette = list(STYLES[style])
        rng.shuffle(palette)
        mood = rng.sample(MOODS, rng.choice([1, 1, 2]))
        if theme:
            mood = [theme] + (
                [m for m in mood if m in ("quiet", "calm")] if theme in ("abandoned", "haunted", "creepy") else []
            )
        time = rng.choice(TIMES[-1:] * 2 + TIMES[:-1] if theme in ("haunted", "horror", "creepy") else TIMES)
        self.d = Draw(seed, style, palette[:3], mood, theme, time)
        seen: dict[str, int] = {}
        for kind in room_types:
            seen[kind] = seen.get(kind, 0) + 1
            self.d.rooms.append(RoomDraw(kind, kind if seen[kind] == 1 else f"{kind}_{seen[kind]}"))
        self.r = self.d.rooms[0]
        self.fabric = 0
        self.ids: dict[str, int] = {}

    # Small helpers ----------------------------------------------------------------------------

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def pick(self, *names: str) -> Piece:
        return piece(self.rng.choice(names))

    def colour(self, options: list[str]) -> str:
        return self.rng.choice(options)

    def material(self, mid: str, preset: str, colour: str, **extra: Any) -> str:
        self.d.materials[mid] = {"preset": preset, "base_color": colour, **extra}
        return mid

    def fabric_for(self, p: Placed, slot: str = "carpet") -> None:
        if slot not in p.piece.slots or not self.chance(0.75):
            return
        self.fabric += 1
        mid = self.material(
            f"fabric_{self.fabric}", "fabric_linen", self.colour(self.d.palette + ["#f2efe9", "#30343f"])
        )
        p.materials.append({"slot": slot, "material": mid})

    def bulb(self, p: Placed, intensity: float, kelvin: float = 2700) -> None:
        y = {"table lamp": 0.75, "floor lamp": 0.85, "ceiling lamp": 0.25, "wall lamp": 0.4, "ceiling fan": 0.1}.get(
            p.piece.category, 0.7
        )
        p.light = {
            "type": "point",
            "kelvin": kelvin,
            "intensity": intensity,
            "range": 6.0,
            "pos": [0, round(p.piece.h * y, 3), 0],
        }

    # The room shell ---------------------------------------------------------------------------

    def size(self, kind: str) -> tuple[float, float, float]:
        rng = self.rng
        (w0, w1), (d0, d1), (h0, h1) = SIZES[kind]
        w = round(rng.uniform(w0, w1) / 0.05) * 0.05
        d = round(rng.uniform(d0, d1) / 0.05) * 0.05
        if kind == "hallway" and rng.random() < 0.5 and not self.d.suite:
            w, d = d, w
        return w, d, round(rng.uniform(h0, h1) / 0.05) * 0.05

    def shell(self) -> Layout:
        rng, kind = self.rng, self.r.room_type
        w, d, height = self.r.size or self.size(kind)
        shape = (
            "l"
            if not self.d.suite
            and kind not in ("hallway", "classroom", "bathroom", "laundry")
            and min(w, d) >= 3.6
            and rng.random() < 0.3
            else "rect"
        )
        if shape == "l":
            nw = round(w * rng.uniform(0.3, 0.42) / 0.05) * 0.05
            nd = round(d * rng.uniform(0.3, 0.42) / 0.05) * 0.05
            corner = rng.randrange(4)
            outline = [
                [(0, 0), (w, 0), (w, d - nd), (w - nw, d - nd), (w - nw, d), (0, d)],
                [(0, 0), (w, 0), (w, d), (nw, d), (nw, d - nd), (0, d - nd)],
                [(0, 0), (w - nw, 0), (w - nw, nd), (w, nd), (w, d), (0, d)],
                [(nw, 0), (w, 0), (w, d), (0, d), (0, nd), (nw, nd)],
            ][corner]
        else:
            outline = [(0, 0), (w, 0), (w, d), (0, d)]
        outline = [(round(x, 3), round(z, 3)) for x, z in outline]
        openings = self.openings(outline, height)
        room_id = self.r.id
        self.r.room = {"id": room_id, "outline": outline, "height": height, "openings": openings, "shape": shape}
        L = Layout(rng, room_id, outline, height, openings)
        L.counts = self.ids
        return L

    def openings(self, outline: list[Pt], height: float) -> list[dict[str, Any]]:
        rng, kind = self.rng, self.r.room_type
        walls = [Wall(i, outline[i], outline[(i + 1) % len(outline)]) for i in range(len(outline))]
        out: list[dict[str, Any]] = [dict(o) for o in self.r.links]
        taken: dict[int, list[tuple[float, float]]] = {}
        for o in out:
            taken.setdefault(o["wall"], []).append((o["offset"] - o["width"] / 2, o["offset"] + o["width"] / 2))
        prefix = f"{self.r.id}_" if self.d.suite else ""
        walls = [w for w in walls if w.index not in self.r.shared]

        def free_span(wall: Wall, width: float) -> Optional[float]:
            lo, hi = width / 2 + 0.2, wall.length - width / 2 - 0.2
            if hi < lo:
                return None
            for _ in range(20):
                u = rng.uniform(lo, hi)
                if all(u + width / 2 + 0.3 < a or u - width / 2 - 0.3 > b for a, b in taken.get(wall.index, [])):
                    return u
            return None

        doors = 1
        if kind == "hallway":
            doors = rng.choice([2, 3, 3])
        elif kind in ("living_room", "kitchen", "dining_room", "classroom", "lobby", "cafe") and rng.random() < 0.35:
            doors = 2
        if self.r.outside_doors is not None:
            doors = self.r.outside_doors
        wide = kind in ("classroom", "cafe", "lobby", "library") and rng.random() < 0.4
        for i in range(doors):
            width = round(rng.uniform(1.3, 1.6), 2) if wide and i == 0 else round(rng.uniform(0.8, 0.95), 2)
            options = [w for w in walls if w.length >= width + 0.5]
            rng.shuffle(options)
            for wall in options:
                u = free_span(wall, width)
                if u is None:
                    continue
                taken.setdefault(wall.index, []).append((u - width / 2, u + width / 2))
                swing = rng.choice(["in", "in", "in", "out", "sliding"])
                out.append(
                    {
                        "id": f"{prefix}door_{i + 1}" if doors > 1 else f"{prefix}door",
                        "type": "door",
                        "wall": wall.index,
                        "offset": round(u, 3),
                        "width": width,
                        "height": round(min(2.1, height - 0.2), 2),
                        "swing": swing,
                        "hinge": rng.choice(["left", "right"]),
                        "open": rng.choice([0.0, 0.0, 0.3, 0.5]) if swing == "in" else 0.0,
                    }
                )
                break
        if not any(o["type"] in ("door", "archway") for o in out):
            raise Retry("no wall takes a door")
        if kind == "hallway":
            n_windows = rng.choice([0, 0, 1])
        elif kind in ("bathroom", "laundry", "basement"):
            n_windows = rng.choice([0, 1, 1])
        elif kind in ("classroom", "cafe", "library", "lobby", "living_room"):
            n_windows = rng.choice([1, 2, 2, 3])
        else:
            n_windows = rng.choice([1, 1, 2])
        high = kind in ("bathroom", "laundry", "basement")
        for i in range(n_windows):
            width = round(rng.uniform(0.6, 1.0) if high else rng.uniform(0.9, 2.0), 2)
            sill = round(rng.uniform(1.3, 1.6) if high else rng.uniform(0.8, 1.0), 2)
            top = min(height - 0.25, sill + (rng.uniform(0.45, 0.7) if high else rng.uniform(1.0, 1.45)))
            options = [w for w in walls if w.length >= width + 0.6]
            rng.shuffle(options)
            for wall in options:
                u = free_span(wall, width)
                if u is None:
                    continue
                taken.setdefault(wall.index, []).append((u - width / 2, u + width / 2))
                out.append(
                    {
                        "id": f"{prefix}window_{i + 1}" if n_windows > 1 else f"{prefix}window",
                        "type": "window",
                        "wall": wall.index,
                        "offset": round(u, 3),
                        "width": width,
                        "height": round(top - sill, 2),
                        "sill": sill,
                    }
                )
                break
        return out

    # Shared furnishing moves ------------------------------------------------------------------

    def door_walls(self, L: Layout) -> set[int]:
        return {o["wall"] for o in L.openings if o["type"] in ("door", "archway")}

    def quiet_walls(self, L: Layout, min_len: float = 0.0) -> list[Wall]:
        """Walls without a door, longest first; all walls if none."""
        doors = self.door_walls(L)
        walls = sorted((w for w in L.walls if w.index not in doors and w.length >= min_len), key=lambda w: -w.length)
        return walls or sorted((w for w in L.walls if w.length >= min_len), key=lambda w: -w.length)

    def not_blocking(self, L: Layout, p: Placed) -> None:
        for o in L.openings:
            if o["type"] == "door":
                L.relate("not_blocking", a=p.id, opening=o["id"])

    def decorate(self, L: Layout, base: Optional[Placed], n: int, choices: tuple[str, ...], **kw: Any) -> list[Placed]:
        out = []
        if base is None:
            return out
        for _ in range(n):
            p = L.on_top(base, self.pick(*choices), child=self.chance(0.7), **kw)
            if p is not None:
                if p.piece.category in ("table lamp",):
                    self.bulb(p, 30)
                out.append(p)
        return out

    def corner_plants(self, L: Layout, n: int) -> None:
        for _ in range(n):
            p = L.against_wall(piece("pottedPlant"), where="corner", relate=False, margin=0.05)
            if p is None:
                break

    def ceiling_light(self, L: Layout, at: Optional[Pt] = None, fan: bool = False) -> Optional[Placed]:
        x, z = at or L.room_centre()
        p = L.from_ceiling(piece("ceilingFan" if fan else "lampSquareCeiling"), x, z, yaw=self.rng.choice([0, 45, 90]))
        if p is not None:
            self.bulb(p, 90 if not fan else 70, kelvin=3000)
        return p

    def floor_lamp(self, L: Layout, near: Optional[Placed] = None) -> Optional[Placed]:
        lamp = self.pick("lampRoundFloor", "lampSquareFloor")
        p = None
        if near is not None:
            p = L.beside(near, lamp, self.rng.choice(["left", "right"]), gap=0.08)
        if p is None:
            p = L.against_wall(lamp, where="corner", relate=False)
        if p is not None:
            self.bulb(p, 45)
        return p

    def desk_with_chair(
        self,
        L: Layout,
        desk_piece: Piece,
        chair_piece: Piece,
        *,
        walls=None,
        equip: bool = True,
        group: Optional[str] = None,
        optional: bool = True,
    ) -> Optional[tuple[Placed, Placed]]:
        for _ in range(6):
            desk = L.against_wall(
                desk_piece,
                walls=walls,
                margin=0.05,
                optional=optional,
                stem="desk" if desk_piece.category == "desk" else None,
            )
            if desk is None:
                return None
            chair = L.in_front(
                desk,
                chair_piece,
                -self.rng.uniform(0.04, 0.14),
                turn=180,
                allow={desk.id: 0.5},
                margin=0.04,
                optional=optional,
            )
            if chair is None:
                L.remove(desk.id)
                L.relations = [r for r in L.relations if r.get("a") != desk.id]
                continue
            break
        else:
            return None
        L.relate("faces", a=chair.id, b=desk.id, tolerance_deg=20)
        self.fabric_for(chair)
        if group or self.chance(0.4):
            gid = group or L.new_id("work_corner")
            desk.group = chair.group = gid
            self.d.groups[gid] = {"category": "work area"}
        if equip:
            if self.chance(0.55):
                screen = L.on_top(desk, piece("computerScreen"), where="back", child=self.chance(0.7))
                if screen is not None:
                    kb = L.on_top(desk, piece("computerKeyboard"), child=self.chance(0.7))
                    if kb is not None and self.chance(0.7):
                        L.on_top(desk, piece("computerMouse"), child=self.chance(0.7), relate=False)
            else:
                L.on_top(desk, piece("laptop"), where="back", child=self.chance(0.7))
            if self.chance(0.6):
                lamp = L.on_top(
                    desk,
                    self.pick("lampSquareTable", "lampRoundTable"),
                    where=self.rng.choice(["left", "right"]),
                    child=self.chance(0.7),
                )
                if lamp is not None:
                    self.bulb(lamp, 30)
            self.decorate(
                L,
                desk,
                self.rng.choice([0, 1, 1, 2]),
                ("books", "plantSmall1", "plantSmall2", "plantSmall3", "speakerSmall", "radio"),
            )
        return desk, chair

    def table_with_chairs(
        self,
        L: Layout,
        table_piece: Piece,
        chair_names: tuple[str, ...],
        n: int,
        *,
        near: Optional[Pt] = None,
        group: bool = False,
        optional: bool = False,
    ) -> Optional[tuple[Placed, list[Placed]]]:
        long_axis = max(L.walls, key=lambda w: w.length).yaw
        yaws = [long_axis, (long_axis + 90) % 360] if self.chance(0.3) else [long_axis]
        table = L.free(table_piece, margin=0.85, yaws=yaws, near=near or L.room_centre(), spread=0.8, optional=optional)
        if table is None:
            table = L.free(table_piece, margin=0.7, yaws=yaws, optional=optional)
        if table is None:
            return None
        chair_piece = self.pick(*chair_names)
        tw, td = table.piece.w, table.piece.d
        per_side = max(1, min(3, int((tw - 0.1) // 0.6)))
        seats = []
        for side in (1, -1):
            for k in range(per_side):
                lx = (k - (per_side - 1) / 2) * (tw / per_side)
                seats.append((lx, side * (td / 2 + chair_piece.d / 2 - self.rng.uniform(0.0, 0.08))))
        if tw >= 1.3:
            for side in (1, -1):
                seats.append((side * (tw / 2 + chair_piece.d / 2 - 0.04), 0.0))
        self.rng.shuffle(seats)
        chairs = []
        for lx, lz in seats:
            if len(chairs) >= n:
                break
            x, z = table.to_room(lx, lz)
            yaw = yaw_toward(x, z, table.x, table.z)
            yaw = round(yaw / 90) * 90 % 360
            poly = L.free_at(chair_piece, x, z, yaw, margin=0.03, allow={table.id: 0.5})
            if poly is None:
                continue
            c = L.add(L.put(chair_piece, x, z, yaw, optional=optional))
            self.faces_if_true(L, c, table)
            self.fabric_for(c)
            chairs.append(c)
        if not chairs:
            L.remove(table.id)
            return None
        if group:
            gid = L.new_id("dining_set")
            self.d.groups[gid] = {"category": "dining set"}
            for p in [table] + chairs:
                p.group = gid
        return table, chairs

    def kitchen_run(
        self,
        L: Layout,
        wall: Wall,
        units: list[str],
        *,
        from_end: bool = False,
        start: float = 0.02,
        uppers: bool = True,
    ) -> list[Placed]:
        """Base units side by side along a wall, from one corner."""
        placed: list[Placed] = []
        u = start
        prev_end = None
        for name in units:
            p = piece(name)
            for _ in range(12):
                cu = u + p.w / 2
                if cu + p.w / 2 > wall.length - 0.02:
                    break
                uu = wall.length - cu if from_end else cu
                x, z = wall.point(uu, 0.01 + p.d / 2)
                poly = L.free_at(p, x, z, wall.yaw, margin=0.0)
                if poly is not None and (
                    p.h <= 1.0 or L.fits(footprint(x, z, p.w + 0.06, p.d + 0.06, wall.yaw), 0, p.h, kinds=("window",))
                ):
                    q = L.add(L.put(p, x, z, wall.yaw, wall=wall.index, stem=None))
                    if placed and prev_end is not None and cu - p.w / 2 - prev_end < 0.01 and self.chance(0.35):
                        L.relate("adjacent", a=q.id, b=placed[-1].id, max_gap=0.05)
                    L.relate("against_wall", a=q.id, wall=f"{L.room}:{wall.index}", side="back")
                    placed.append(q)
                    u = cu + p.w / 2 + 0.005
                    prev_end = cu + p.w / 2
                    break
                u += 0.1
        if uppers:
            upper = self.pick("kitchenCabinetUpper", "kitchenCabinetUpperDouble", "kitchenCabinetUpperLow")
            for q in placed:
                if q.piece.category == "kitchen cabinet" and self.chance(0.75):
                    uu = self._u_on(wall, q)
                    L.on_wall(upper, wall, uu, base_y=round(q.top + self.rng.uniform(0.55, 0.65), 2))
                elif q.piece.category == "stove":
                    hood = L.on_wall(
                        self.pick("hoodModern", "hoodLarge"), wall, self._u_on(wall, q), base_y=round(q.top + 0.62, 2)
                    )
                    if hood is not None:
                        L.relate("distance", a=hood.id, b=q.id, range=[0.0, _centre_gap(hood, q)], measure="center")
        return placed

    @staticmethod
    def _u_on(wall: Wall, p: Placed) -> float:
        dx, dz = wall.dir
        return (p.x - wall.a[0]) * dx + (p.z - wall.a[1]) * dz

    def facing_wall(self, L: Layout, p: Placed) -> Optional[Wall]:
        fx, fz = p.front
        best = None
        for w in L.walls:
            nx, nz = w.normal
            if nx * fx + nz * fz < -0.99:
                # the wall must be in front of p
                if (w.a[0] - p.x) * fx + (w.a[1] - p.z) * fz > 0.5:
                    dist = (w.a[0] - p.x) * fx + (w.a[1] - p.z) * fz
                    u = self._u_on(w, p)
                    if 0.3 < u < w.length - 0.3 and (best is None or dist < best[0]):
                        best = (dist, w)
        return best[1] if best else None

    def faces_if_true(self, L: Layout, a: Placed, b: Placed, tol: float = 20.0) -> None:
        to = (b.x - a.x, b.z - a.z)
        n = math.hypot(*to) or 1.0
        fx, fz = a.front
        off = math.degrees(math.acos(max(-1.0, min(1.0, (fx * to[0] + fz * to[1]) / n))))
        if off <= tol - 5:
            L.relate("faces", a=a.id, b=b.id, tolerance_deg=tol)

    # Programmes ----------------------------------------------------------------------------------

    def bedroom(self, L: Layout) -> None:
        rng = self.rng
        double = min(L.bounds[2], L.bounds[3]) >= 3.3 and self.chance(0.75)
        bed_piece = piece("bedDouble" if double else "bedSingle")
        sides = 2 if double and self.chance(0.75) else (1 if self.chance(0.75) else 0)
        need = bed_piece.w + sides * 0.62
        bed = L.against_wall(
            bed_piece,
            walls=self.quiet_walls(L, need) or None,
            where="middle" if self.chance(0.7) else "any",
            front_clear=0.55,
            optional=False,
            stem="bed",
        )
        if bed is None:
            raise Retry("no room for the bed")
        self.r.anchor = bed.id
        self.fabric_for(bed)
        self.r.mention.append("bed")
        self.not_blocking(L, bed)
        side_names = ["left", "right"]
        rng.shuffle(side_names)
        stand = self.pick("cabinetBed", "cabinetBedDrawer", "cabinetBedDrawerTable")
        for side in side_names[:sides]:
            ns = L.beside(bed, stand, side, gap=round(rng.uniform(0.03, 0.1), 2), stem="nightstand")
            if ns is None:
                continue
            L.relate("adjacent", a=ns.id, b=bed.id, max_gap=0.15)
            L.relate("against_wall", a=ns.id, wall=f"{L.room}:{bed.wall}", side="back")
            if self.chance(0.8):
                lamp = L.on_top(
                    ns, self.pick("lampRoundTable", "lampSquareTable"), where="centre", child=self.chance(0.75)
                )
                if lamp is not None:
                    self.bulb(lamp, 35)
                    if self.chance(0.5):
                        L.relate("near", a=lamp.id, b=bed.id, max=0.6, hard=False, weight=0.5)
                    if "table lamp" not in self.r.mention and self.chance(0.6):
                        self.r.mention.append("table lamp")
            else:
                self.decorate(L, ns, 1, ("books", "plantSmall1", "radio"))
        if double and self.chance(0.4):
            for _ in range(rng.choice([1, 2])):
                L.on_top(bed, self.pick("pillow", "pillowBlue", "pillowLong"), where="back", relate=self.chance(0.5))
        if self.chance(0.45):
            rug = self.pick("rugRectangle", "rugRounded")
            x, z = bed.to_room(0.0, bed.piece.d * 0.18)
            if L.free_at(rug, x, z, bed.yaw) is not None:
                L.add(L.put(rug, x, z, bed.yaw))
        if self.chance(0.8):
            ward = L.against_wall(
                self.pick("bookcaseClosedDoors", "bookcaseClosedWide", "bookcaseClosed"), front_clear=0.6
            )
            if ward is not None:
                if self.chance(0.4):
                    L.relate("clear", around=ward.id, sides=["front"], margin=0.5)
                self.decorate(L, ward, rng.choice([0, 1]), ("cardboardBoxClosed", "plantSmall2", "books"))
        if self.chance(0.55):
            pair = self.desk_with_chair(L, piece("desk"), piece("chairDesk"))
            if pair and self.chance(0.6):
                self.r.mention.append("desk")
                if self.chance(0.4) and _poly_distance(pair[0].poly, bed.poly) >= 0.6:
                    L.relate(
                        "far_from",
                        a=pair[0].id,
                        b=bed.id,
                        min=round(max(0.2, _poly_distance(pair[0].poly, bed.poly) - 0.3), 2),
                        hard=False,
                    )
        if self.chance(0.4):
            dresser = L.against_wall(
                self.pick("sideTableDrawers", "sideTable", "cabinetTelevisionDoors"), front_clear=0.5
            )
            self.decorate(
                L, dresser, rng.choice([1, 2]), ("radio", "plantSmall1", "plantSmall3", "books", "lampSquareTable")
            )
        if self.chance(0.2):
            tv = L.against_wall(
                piece("cabinetTelevision"), walls=[w for w in [self.facing_wall(L, bed)] if w], where="middle"
            )
            if tv is not None:
                screen = L.on_top(tv, piece("televisionModern"), where="centre")
                if screen is not None:
                    self.faces_if_true(L, bed, tv, 25)
        elif self.chance(0.5):
            self.r.forbidden.append("television")
        if self.chance(0.3):
            L.in_front(bed, piece("benchCushionLow"), 0.08, margin=0.05)
        if self.chance(0.35):
            self.floor_lamp(L)
        self.corner_plants(L, rng.choice([0, 0, 1]))
        if self.chance(0.3):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.25):
            L.against_wall(piece("coatRackStanding"), where="corner", relate=False)
        if self.chance(0.6):
            self.ceiling_light(L, fan=self.chance(0.25))

    def kids_room(self, L: Layout) -> None:
        rng = self.rng
        bunk = self.chance(0.45)
        bed = L.against_wall(
            piece("bedBunk" if bunk else "bedSingle"),
            walls=self.quiet_walls(L, 1.2) or None,
            where="corner" if self.chance(0.6) else "any",
            front_clear=0.5,
            optional=False,
            stem="bunk_bed" if bunk else "bed",
        )
        if bed is None:
            raise Retry("no room for the bed")
        self.r.anchor = bed.id
        self.r.mention.append("bunk bed" if bunk else "bed")
        self.fabric_for(bed)
        self.not_blocking(L, bed)
        if not bunk and self.chance(0.7):
            ns = L.beside(
                bed,
                self.pick("cabinetBed", "cabinetBedDrawer"),
                rng.choice(["left", "right"]),
                gap=0.05,
                stem="nightstand",
            )
            if ns is not None:
                L.relate("adjacent", a=ns.id, b=bed.id, max_gap=0.15)
                lamp = L.on_top(ns, self.pick("lampRoundTable", "lampSquareTable"), where="centre")
                if lamp is not None:
                    self.bulb(lamp, 25)
        if self.chance(0.4) and not bunk:
            L.on_top(bed, piece("pillowBlue"), where="back")
        if self.chance(0.75):
            bear = (
                L.free(piece("bear"), margin=0.1, yaws=[rng.uniform(0, 360)])
                if self.chance(0.6)
                else L.against_wall(piece("bear"), where="corner", relate=False)
            )
            if bear is not None:
                self.r.mention.append("teddy bear")
        play = L.room_centre()
        if self.chance(0.7):
            rug = self.pick("rugRound", "rugSquare")
            if L.free_at(rug, play[0], play[1], 0.0) is not None:
                L.add(L.put(rug, play[0], play[1], 0.0))
        if self.chance(0.55):
            self.table_with_chairs(
                L,
                piece("tableCoffeeSquare"),
                ("chairRounded", "chairCushion"),
                rng.choice([2, 2, 3]),
                near=play,
                optional=True,
            )
        for _ in range(rng.choice([1, 2, 3])):
            box = L.free(
                self.pick("cardboardBoxOpen", "cardboardBoxClosed"),
                margin=0.08,
                yaws=[rng.uniform(0, 360)],
                stem="toy_box",
            )
            if box is None:
                break
        low = L.against_wall(piece("bookcaseOpenLow"), front_clear=0.5)
        self.decorate(L, low, rng.choice([1, 2]), ("books", "plantSmall1", "radio", "bear"))
        if self.chance(0.4):
            self.desk_with_chair(L, piece("desk"), self.pick("chairDesk", "chairRounded"))
        if self.chance(0.5):
            self.ceiling_light(L, fan=self.chance(0.3))
        if self.chance(0.5):
            self.r.forbidden.append("television")

    def living_room(self, L: Layout) -> None:
        rng = self.rng
        sofa_piece = self.pick("loungeSofa", "loungeDesignSofa", "loungeSofaLong", "loungeSofa")
        sofa = L.against_wall(
            sofa_piece,
            walls=self.quiet_walls(L, sofa_piece.w + 0.6),
            where="middle" if self.chance(0.6) else "any",
            optional=False,
        )
        if sofa is None:
            raise Retry("no wall for the sofa")
        self.r.anchor = sofa.id
        self.fabric_for(sofa)
        self.r.mention.append("sofa")
        if self.chance(0.35):
            L.on_top(
                sofa, self.pick("pillow", "pillowBlue", "pillowBlueLong", "pillowLong"), where="back", relate=False
            )
        tv_wall = self.facing_wall(L, sofa)
        tvcab = None
        if tv_wall is not None and self.chance(0.85):
            u = self._u_on(tv_wall, sofa)
            cab = self.pick("cabinetTelevision", "cabinetTelevisionDoors")
            for du in (0.0, 0.3, -0.3, 0.6, -0.6):
                if not cab.w / 2 + 0.02 <= u + du <= tv_wall.length - cab.w / 2 - 0.02:
                    continue
                x, z = tv_wall.point(u + du, 0.01 + cab.d / 2)
                if L.free_at(cab, x, z, tv_wall.yaw, margin=0.05) is not None:
                    tvcab = L.add(L.put(cab, x, z, tv_wall.yaw, wall=tv_wall.index))
                    L.relate("against_wall", a=tvcab.id, wall=f"{L.room}:{tv_wall.index}", side="back")
                    break
        if tvcab is not None:
            tv = L.on_top(tvcab, piece("televisionModern"), where="centre")
            if tv is not None:
                self.r.mention.append("television")
                self.faces_if_true(L, sofa, tvcab)
                gap = _poly_distance(sofa.poly, tvcab.poly)
                L.relate(
                    "distance",
                    a=sofa.id,
                    b=tvcab.id,
                    range=[round(max(0.0, gap - 0.6), 2), round(gap + 0.6, 2)],
                    hard=False,
                    weight=0.5,
                )
            if self.chance(0.35):
                for side in ("left", "right"):
                    L.beside(tvcab, piece("speaker"), side, gap=0.1)
        elif self.chance(0.6):
            self.r.forbidden.append("television")
        table = L.in_front(
            sofa,
            self.pick("tableCoffee", "tableCoffeeGlass", "tableCoffeeSquare", "tableCoffeeGlassSquare"),
            round(rng.uniform(0.32, 0.5), 2),
            margin=0.05,
        )
        if table is not None:
            L.relate("near", a=table.id, b=sofa.id, max=0.6)
            self.decorate(L, table, rng.choice([0, 1, 2]), ("books", "plantSmall1", "plantSmall2", "plantSmall3"))
            if self.chance(0.6):
                rug = self.pick("rugRectangle", "rugRounded", "rugSquare", "rugRound")
                if L.free_at(rug, table.x, table.z, sofa.yaw) is not None:
                    L.add(L.put(rug, table.x, table.z, sofa.yaw))
            for side in rng.sample([1, -1], rng.choice([0, 1, 1, 2])):
                chair = self.pick("loungeChair", "loungeDesignChair", "loungeChairRelax")
                lx = side * (table.piece.w / 2 + 0.3 + chair.d / 2)
                x, z = table.to_room(lx, 0.0)
                yaw = (table.yaw - side * 90) % 360
                if L.free_at(chair, x, z, yaw, margin=0.05) is not None:
                    c = L.add(L.put(chair, x, z, yaw))
                    L.relate("faces", a=c.id, b=table.id, tolerance_deg=20)
                    self.fabric_for(c)
                    if "armchair" not in self.r.mention and self.chance(0.4):
                        self.r.mention.append("armchair")
        if self.chance(0.55):
            end = L.beside(
                sofa,
                self.pick("cabinetBedDrawerTable", "cabinetBed"),
                rng.choice(["left", "right"]),
                gap=0.06,
                category="end table",
            )
            if end is not None:
                L.relate("adjacent", a=end.id, b=sofa.id, max_gap=0.15)
                lamp = L.on_top(end, self.pick("lampRoundTable", "lampSquareTable"), where="centre")
                if lamp is not None:
                    self.bulb(lamp, 30)
        if self.chance(0.5):
            self.floor_lamp(L, near=sofa)
        for _ in range(rng.choice([0, 1, 2])):
            bc = L.against_wall(
                self.pick("bookcaseOpen", "bookcaseClosedWide", "bookcaseOpenLow", "bookcaseClosedDoors"),
                front_clear=0.5,
            )
            self.decorate(L, bc, rng.choice([0, 1]), ("plantSmall1", "books", "radio", "speakerSmall"))
        if self.chance(0.2):
            L.free(piece("loungeSofaOttoman"), margin=0.4)
        self.corner_plants(L, rng.choice([0, 1, 2]))
        if self.chance(0.6):
            self.ceiling_light(L, fan=self.chance(0.35))

    def kitchen(self, L: Layout) -> None:
        rng = self.rng
        walls = sorted(L.walls, key=lambda w: (w.index in self.door_walls(L), -w.length))
        fridge = self.pick("kitchenFridge", "kitchenFridgeLarge", "kitchenFridgeBuiltIn", "kitchenFridgeSmall")
        stove = self.pick("kitchenStove", "kitchenStoveElectric")
        base = ["kitchenCabinet", "kitchenCabinetDrawer"]
        with_fridge, fridge_first, from_end = self.chance(0.85), self.chance(0.5), self.chance(0.5)
        run: list[Placed] = []
        for main in walls[:3]:
            n = max(2, int((main.length - 0.1) // 0.82))
            units = [rng.choice(base) for _ in range(n)]
            sink_at = rng.randrange(1, max(2, n - 1)) if n > 2 else 0
            stove_at = (sink_at + rng.choice([2, 3])) % n if n > 3 else (n - 1 if sink_at == 0 else 0)
            units[sink_at] = "kitchenSink"
            units[stove_at] = stove.name
            if with_fridge:
                units = ([fridge.name] + units) if fridge_first else (units + [fridge.name])
            for end in (from_end, not from_end):
                before = set(L.placed)
                run = self.kitchen_run(L, main, units, from_end=end)
                if {"stove", "kitchen sink"} <= {p.piece.category for p in run}:
                    break
                for node_id in set(L.placed) - before:
                    L.remove(node_id)
                run = []
            if run:
                break
        if not run:
            raise Retry("no wall takes a sink and a stove")
        cats = {p.piece.category for p in run}
        self.r.anchor = next(p.id for p in run if p.piece.category == "stove")
        self.r.mention += [c for c in ("stove", "fridge", "kitchen sink") if c in cats and self.chance(0.6)]
        self.r.counts["kitchen cabinet"] = sum(1 for p in run if p.piece.category == "kitchen cabinet")
        if self.chance(0.4) or len(run) < 4:
            side = [
                w
                for w in L.walls
                if w.index != main.index
                and w.index not in self.door_walls(L)
                and abs(w.normal[0] * main.normal[0] + w.normal[1] * main.normal[1]) < 0.1
            ]
            if side:
                w = rng.choice(side)
                k = max(1, int((w.length - 1.0) // 0.82) - 1)
                self.kitchen_run(
                    L, w, [rng.choice(base) for _ in range(min(k, 3))], start=0.9, from_end=self.chance(0.5)
                )
        tops = [p for p in L.placed.values() if p.piece.category == "kitchen cabinet"]
        for name in rng.sample(
            ["kitchenMicrowave", "kitchenCoffeeMachine", "toaster", "kitchenBlender", "plantSmall2"],
            rng.choice([1, 2, 3]),
        ):
            if not tops:
                break
            p = L.on_top(rng.choice(tops), piece(name), where="back", child=self.chance(0.6))
            if p is not None and p.piece.category in ("microwave", "coffee machine") and self.chance(0.5):
                self.r.mention.append(p.piece.category)
        area = (L.bounds[2] - L.bounds[0]) * (L.bounds[3] - L.bounds[1])
        if area > 11 and self.chance(0.6):
            self.table_with_chairs(
                L,
                self.pick("table", "tableCross", "tableRound", "tableGlass"),
                ("chair", "chairCushion", "chairModernCushion"),
                rng.choice([2, 3, 4]),
                optional=True,
            )
        elif area > 10 and self.chance(0.4):
            bar = L.free(piece("kitchenBar"), margin=0.7)
            if bar is not None:
                for lateral in rng.choice([[0.0], [-0.22, 0.22]]):
                    st = L.in_front(
                        bar,
                        self.pick("stoolBar", "stoolBarSquare"),
                        -0.05,
                        turn=180,
                        lateral=lateral,
                        allow={bar.id: 0.5},
                        margin=0.03,
                    )
                    if st is not None:
                        self.faces_if_true(L, st, bar, 25)
        if self.chance(0.5):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.6):
            self.ceiling_light(L)

    def dining_room(self, L: Layout) -> None:
        rng = self.rng
        table_piece = self.pick("table", "tableCloth", "tableCross", "tableCrossCloth", "tableGlass", "tableRound")
        n = rng.choice([4, 4, 6, 6, 2, 5])
        got = self.table_with_chairs(
            L,
            table_piece,
            ("chair", "chairCushion", "chairModernCushion", "chairModernFrameCushion", "chairRounded"),
            n,
            group=self.chance(0.4),
        )
        if got is None:
            raise Retry("no room for the table")
        table, chairs = got
        self.r.anchor = table.id
        self.r.mention.append("dining table")
        self.r.counts["chair"] = len(chairs)
        if (
            L.placed
            and all(len(L.outline) == 4 for _ in [0])
            and abs(table.x - L.room_centre()[0]) < 0.15
            and abs(table.z - L.room_centre()[1]) < 0.15
        ):
            L.relate("centered", a=table.id, within=L.room, axes="xz", tolerance=0.2)
        if self.chance(0.55):
            rug = self.pick("rugRectangle", "rugRounded", "rugRound")
            if L.free_at(rug, table.x, table.z, table.yaw) is not None:
                L.add(L.put(rug, table.x, table.z, table.yaw))
        lamp = self.ceiling_light(L, at=(table.x, table.z)) if self.chance(0.7) else None
        if lamp is not None:
            L.relate("distance", a=lamp.id, b=table.id, range=[0.0, 0.1], measure="center")
        if self.chance(0.75):
            side = L.against_wall(
                self.pick("sideTableDrawers", "sideTable", "cabinetTelevisionDoors", "bookcaseClosedWide"),
                front_clear=0.5,
                category="sideboard",
            )
            self.decorate(
                L, side, rng.choice([1, 2, 3]), ("plantSmall1", "plantSmall2", "lampSquareTable", "radio", "books")
            )
        if self.chance(0.3):
            L.against_wall(self.pick("bookcaseClosedDoors", "bookcaseOpen"), front_clear=0.5)
        self.corner_plants(L, rng.choice([0, 1, 2]))

    def bathroom(self, L: Layout) -> None:
        rng = self.rng
        tub = None
        order = [("bathtub", "corner"), ("shower", "corner"), ("bathtub", "any"), ("shower", "any")]
        if self.chance(0.45):
            order = order[1:2] + order[:1] + order[3:] + order[2:3]
        for name, where in order:
            tub = L.against_wall(piece(name), where=where, front_clear=0.5, optional=False)
            if tub is not None:
                break
        if tub is None:
            raise Retry("no wall for the bath or shower")
        self.r.mention.append(tub.piece.category)
        toilet = L.against_wall(self.pick("toilet", "toiletSquare"), front_clear=0.55, optional=False)
        if toilet is None:
            raise Retry("no wall for the toilet")
        self.r.anchor = toilet.id
        if self.chance(0.6):
            self.r.mention.append("toilet")
        sink = None
        for clear in (0.55, 0.45):
            sink = sink or L.against_wall(self.pick("bathroomSink", "bathroomSinkSquare"), front_clear=clear, tries=60)
        if sink is not None:
            self.r.mention.append("sink")
            wall = L.walls[sink.wall]
            mirror = L.on_wall(piece("bathroomMirror"), wall, self._u_on(wall, sink), base_y=round(sink.top + 0.08, 2))
            if mirror is not None:
                L.relate("distance", a=mirror.id, b=sink.id, range=[0.0, _centre_gap(mirror, sink)], measure="center")
                if self.chance(0.5):
                    self.r.mention.append("mirror")
                if self.chance(0.3):
                    lamp = L.on_wall(
                        piece("lampWall"),
                        wall,
                        self._u_on(wall, sink) + rng.choice([-1, 1]) * 0.5,
                        base_y=round(mirror.top - 0.3, 2),
                    )
                    if lamp is not None:
                        self.bulb(lamp, 20)
        if self.chance(0.45):
            vanity = L.against_wall(piece("bathroomCabinetDrawer"), front_clear=0.5)
            self.decorate(L, vanity, 1, ("plantSmall1", "plantSmall3"))
        if self.chance(0.35):
            w = rng.choice(L.walls)
            L.on_wall(piece("bathroomCabinet"), w, rng.uniform(0.4, max(0.41, w.length - 0.4)), base_y=1.35)
        if self.chance(0.25):
            L.against_wall(piece("washer"), front_clear=0.5)
        if self.chance(0.4):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.5):
            mat = piece("rugDoormat")
            x, z = tub.to_room(0.0, tub.piece.d / 2 + 0.3)
            if L.free_at(mat, x, z, tub.yaw) is not None:
                L.add(L.put(mat, x, z, tub.yaw, category="bath mat"))
        if self.chance(0.5):
            self.ceiling_light(L)
        if self.chance(0.4):
            self.r.forbidden.append("television")

    def office(self, L: Layout) -> None:
        rng = self.rng
        n = rng.choice([1, 1, 2, 2, 3])
        desks = []
        for i in range(n):
            pair = self.desk_with_chair(L, piece("desk"), piece("chairDesk"), optional=i > 0)
            if pair is None:
                if i == 0:
                    raise Retry("no wall for a desk")
                break
            desks.append(pair)
        self.r.anchor = desks[0][0].id
        self.r.mention.append("desk")
        if len(desks) > 1:
            self.r.counts["desk"] = len(desks)
        for _ in range(rng.choice([1, 2, 3])):
            bc = L.against_wall(
                self.pick("bookcaseOpen", "bookcaseClosedDoors", "bookcaseClosed", "bookcaseOpenLow"), front_clear=0.6
            )
            self.decorate(L, bc, rng.choice([0, 1, 2]), ("books", "cardboardBoxClosed", "plantSmall2", "radio"))
        if self.chance(0.35):
            chair = L.against_wall(self.pick("loungeChair", "loungeDesignChair"), front_clear=0.5)
            if chair is not None:
                self.fabric_for(chair)
                side = L.beside(
                    chair,
                    piece("cabinetBedDrawerTable"),
                    rng.choice(["left", "right"]),
                    gap=0.05,
                    category="side table",
                )
                if side is not None:
                    self.decorate(L, side, 1, ("plantSmall1", "books", "lampSquareTable"))
        if self.chance(0.5):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.4):
            self.floor_lamp(L)
        if self.chance(0.3):
            L.against_wall(piece("coatRackStanding"), where="corner", relate=False)
        for _ in range(rng.choice([0, 0, 1, 2])):
            L.free(self.pick("cardboardBoxClosed", "cardboardBoxOpen"), margin=0.1, yaws=[rng.choice([0, 15, 30, 80])])
        self.corner_plants(L, rng.choice([0, 1, 1, 2]))
        if self.chance(0.7):
            self.ceiling_light(L)

    def library(self, L: Layout) -> None:
        rng = self.rng
        cases = 0
        for _ in range(rng.randint(4, 9)):
            bc = L.against_wall(
                self.pick("bookcaseOpen", "bookcaseOpen", "bookcaseClosed", "bookcaseClosedWide", "bookcaseOpenLow"),
                front_clear=0.7,
                optional=cases > 1,
            )
            if bc is not None:
                cases += 1
                self.decorate(L, bc, rng.choice([0, 0, 1]), ("books", "plantSmall1", "plantSmall3"))
        if cases < 2:
            raise Retry("too few bookcases")
        self.r.anchor = next(p.id for p in L.placed.values() if p.piece.category == "bookcase")
        self.r.mention.append("bookcase")
        self.r.counts["bookcase"] = sum(1 for p in L.placed.values() if p.piece.category == "bookcase")
        if self.chance(0.6):
            got = self.table_with_chairs(
                L,
                self.pick("table", "tableCross", "tableRound"),
                ("chair", "chairCushion"),
                rng.choice([2, 4]),
                optional=True,
            )
            if got is not None:
                lamp = L.on_top(got[0], piece("lampSquareTable"), where="centre")
                if lamp is not None:
                    self.bulb(lamp, 30)
                self.decorate(L, got[0], rng.choice([1, 2]), ("books",))
        for _ in range(rng.choice([1, 2])):
            chair = self.pick("loungeChair", "loungeChairRelax", "loungeDesignChair")
            c = L.free(chair, margin=0.5, yaws=[rng.choice([w.yaw for w in L.walls])])
            if c is None:
                continue
            self.fabric_for(c)
            if "armchair" not in self.r.mention:
                self.r.mention.append("armchair")
            lamp = self.floor_lamp(L, near=c)
            if lamp is not None and self.chance(0.5) and _poly_distance(lamp.poly, c.poly) <= 0.3:
                L.relate("near", a=lamp.id, b=c.id, max=0.4)
        if self.chance(0.4):
            self.desk_with_chair(L, piece("desk"), self.pick("chairDesk", "chair"))
        if self.chance(0.5):
            rug = self.pick("rugRectangle", "rugRound", "rugSquare")
            x, z = L.room_centre()
            if L.free_at(rug, x, z, 0.0) is not None:
                L.add(L.put(rug, x, z, 0.0))
        self.corner_plants(L, rng.choice([0, 1]))
        if self.chance(0.5):
            self.ceiling_light(L)
        self.r.forbidden.append(rng.choice(["television", "speaker"]))

    def classroom(self, L: Layout) -> None:
        rng = self.rng
        front = max(self.quiet_walls(L), key=lambda w: w.length)
        nx, nz = front.normal
        depth = min(
            abs((p[0] - front.a[0]) * nx + (p[1] - front.a[1]) * nz)
            for p in L.outline
            if abs((p[0] - front.a[0]) * nx + (p[1] - front.a[1]) * nz) > 1.0
        )
        # Teacher's desk: front toward the board wall, chair between them.
        teacher = None
        for du in (0.0, 0.6, -0.6, 1.2, -1.2):
            u = front.length / 2 + du
            d = piece("desk")
            x, z = front.point(u, 1.25 + d.d / 2)
            yaw = (front.yaw + 180) % 360
            if L.free_at(d, x, z, yaw, margin=0.05) is not None:
                teacher = L.add(
                    L.put(d, x, z, yaw, optional=False, name="Teacher's desk", stem="teacher_desk", role="teacher")
                )
                break
        if teacher is None:
            raise Retry("no room for the teacher's desk")
        chair = L.in_front(
            teacher, piece("chairDesk"), -0.08, turn=180, allow={teacher.id: 0.5}, margin=0.03, stem="teacher_chair"
        )
        if chair is not None:
            L.relate("faces", a=chair.id, b=teacher.id, tolerance_deg=20)
        L.relate("faces_direction", a=teacher.id, yaw=round((front.yaw + 180) % 360, 1), tolerance_deg=10)
        self.r.anchor = teacher.id
        if self.chance(0.6):
            L.on_top(teacher, piece("laptop"), where="back")
        sd = piece("sideTable")
        sc = self.pick("chair", "chairCushion", "chairModernCushion", "chairModernFrameCushion")
        row_gap = sd.d + sc.d + rng.uniform(0.55, 0.75)
        col_gap = sd.w + rng.uniform(0.35, 0.6)
        cols = max(1, int((front.length - 0.6) // col_gap))
        rows = max(1, int((depth - 2.4 - (sd.d + sc.d) - 0.3) // row_gap) + 1)
        start = (front.length - (cols - 1) * col_gap) / 2
        students = 0
        for r in range(rows):
            for c in range(cols):
                x, z = front.point(start + c * col_gap, 2.4 + r * row_gap + sd.d / 2)
                if L.free_at(sd, x, z, front.yaw, margin=0.05) is None:
                    continue
                desk = L.add(L.put(sd, x, z, front.yaw, category="school desk", stem="desk", optional=students > 1))
                ch = L.in_front(desk, sc, -0.05, turn=180, allow={desk.id: 0.5}, margin=0.03, optional=students > 1)
                if ch is None:
                    L.remove(desk.id)
                    continue
                students += 1
                if self.chance(0.3):
                    L.relate("faces", a=ch.id, b=desk.id, tolerance_deg=20)
                if self.chance(0.15):
                    L.on_top(desk, piece("books"), relate=False)
        if students < 2:
            raise Retry("no room for pupils")
        self.r.extra.append(f"desks for {students} pupils")
        self.r.counts["school desk"] = students
        for _ in range(rng.choice([1, 2, 3])):
            bc = L.against_wall(self.pick("bookcaseOpen", "bookcaseOpenLow", "bookcaseClosed"), front_clear=0.6)
            self.decorate(L, bc, rng.choice([0, 1]), ("books", "plantSmall2"))
        if self.chance(0.6):
            door = next(o for o in L.openings if o["type"] in ("door", "archway"))
            w = L.walls[door["wall"]]
            u = door["offset"] + rng.choice([-1, 1]) * (door["width"] / 2 + 0.7)
            L.on_wall(piece("coatRack"), w, u, base_y=1.45)
        L.against_wall(piece("trashcan"), relate=False)
        self.corner_plants(L, rng.choice([0, 1, 2]))
        for _ in range(rng.choice([1, 2])):
            self.ceiling_light(
                L, at=(rng.uniform(L.bounds[0] + 1, L.bounds[2] - 1), rng.uniform(L.bounds[1] + 1, L.bounds[3] - 1))
            )

    def cafe(self, L: Layout) -> None:
        rng = self.rng
        wall = max(self.quiet_walls(L), key=lambda w: w.length)
        back = self.kitchen_run(
            L,
            wall,
            ["kitchenCabinet", "kitchenFridgeSmall", "kitchenCabinetDrawer", "kitchenCabinet"][: rng.choice([2, 3, 4])],
            start=rng.uniform(0.3, 1.0),
            uppers=self.chance(0.5),
        )
        if not back:
            raise Retry("no wall for the back counter")
        for p in back:
            if p.piece.category == "kitchen cabinet" and self.chance(0.6):
                L.on_top(p, self.pick("kitchenCoffeeMachine", "kitchenBlender", "toaster"), where="back")
        mid_u = sum(self._u_on(wall, p) for p in back) / len(back)
        bar_piece = piece("kitchenBar")
        k = rng.choice([2, 3, 3, 4])
        bars: list[Placed] = []
        span = k * (bar_piece.w + 0.005)
        u0 = mid_u - span / 2 + bar_piece.w / 2
        inset = 0.01 + 0.855 + 1.0 + bar_piece.d / 2
        for i in range(k):
            x, z = wall.point(u0 + i * (bar_piece.w + 0.005), inset)
            if L.free_at(bar_piece, x, z, wall.yaw, margin=0.0) is None:
                continue
            b = L.add(L.put(bar_piece, x, z, wall.yaw, optional=bool(bars)))
            if bars and abs(math.dist((b.x, b.z), (bars[-1].x, bars[-1].z)) - bar_piece.w - 0.005) < 1e-3:
                L.relate("adjacent", a=b.id, b=bars[-1].id, max_gap=0.05)
            bars.append(b)
        if not bars:
            raise Retry("no room for the bar")
        self.r.anchor = bars[0].id
        self.r.mention.append("bar counter")
        stools = 0
        stool = self.pick("stoolBar", "stoolBarSquare")
        for b in bars:
            s = L.in_front(b, stool, -0.06, turn=180, allow={b.id: 0.5}, margin=0.04)
            if s is not None:
                stools += 1
                if self.chance(0.5):
                    L.relate("faces", a=s.id, b=b.id, tolerance_deg=25)
        if self.chance(0.7):
            for end, side in ((bars[0], "right"), (bars[-1], "left")):
                cap = L.beside(end, piece("kitchenBarEnd"), side, gap=0.005, margin=0.0, category="bar counter")
                if cap is not None and self.chance(0.5):
                    L.relate("adjacent", a=cap.id, b=end.id, max_gap=0.05)
        if stools:
            self.r.mention.append("bar stool")
        for _ in range(rng.choice([2, 3, 4, 5])):
            self.table_with_chairs(
                L,
                self.pick("tableRound", "tableCross", "tableCrossCloth", "table"),
                ("chair", "chairCushion", "chairRounded", "chairModernCushion"),
                rng.choice([2, 2, 4]),
                near=(rng.uniform(L.bounds[0], L.bounds[2]), rng.uniform(L.bounds[1], L.bounds[3])),
                optional=True,
            )
        self.corner_plants(L, rng.choice([1, 2, 3]))
        if self.chance(0.4):
            L.against_wall(piece("coatRackStanding"), where="corner", relate=False)
        L.against_wall(piece("trashcan"), relate=False)
        for _ in range(rng.choice([1, 2, 3])):
            self.ceiling_light(
                L,
                at=(
                    rng.uniform(L.bounds[0] + 0.8, L.bounds[2] - 0.8),
                    rng.uniform(L.bounds[1] + 0.8, L.bounds[3] - 0.8),
                ),
            )

    def laundry(self, L: Layout) -> None:
        rng = self.rng
        if self.chance(0.4):
            w = L.against_wall(piece("washerDryerStacked"), front_clear=0.6, optional=False)
            if w is None:
                raise Retry("no wall for the washer")
            self.r.mention.append("washer dryer")
        else:
            w = L.against_wall(piece("washer"), front_clear=0.6, optional=False)
            if w is None:
                raise Retry("no wall for the washer")
            self.r.mention.append("washing machine")
            dr = L.beside(w, piece("dryer"), rng.choice(["left", "right"]), gap=0.03)
            if dr is not None:
                L.relate("adjacent", a=dr.id, b=w.id, max_gap=0.1)
                self.r.mention.append("dryer")
                self.decorate(L, dr, rng.choice([0, 1]), ("cardboardBoxClosed", "plantSmall1"))
        self.r.anchor = w.id
        if self.chance(0.6):
            wall = rng.choice(self.quiet_walls(L, 1.8))
            self.kitchen_run(
                L,
                wall,
                ["kitchenSink", "kitchenCabinet"] if self.chance(0.5) else ["kitchenCabinet", "kitchenSink"],
                start=rng.uniform(0.05, 0.4),
                uppers=self.chance(0.5),
            )
        for _ in range(rng.choice([0, 1, 2, 3])):
            L.free(self.pick("cardboardBoxClosed", "cardboardBoxOpen"), margin=0.1, yaws=[rng.choice([0, 10, 25, 90])])
        if self.chance(0.4):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.3):
            wl = rng.choice(L.walls)
            L.on_wall(piece("coatRack"), wl, wl.length / 2, base_y=1.5)
        if self.chance(0.5):
            self.ceiling_light(L)

    def hallway(self, L: Layout) -> None:
        rng = self.rng
        long_walls = [w for w in L.walls if w.length >= 2.5]
        bench = L.against_wall(self.pick("bench", "benchCushion", "benchCushionLow"), walls=long_walls, optional=False)
        rack = L.against_wall(piece("coatRackStanding"), where="corner", relate=False, optional=bench is not None)
        if bench is None and rack is None:
            raise Retry("hallway too tight")
        self.r.anchor = (bench or rack).id
        self.r.mention.append("bench" if bench else "coat rack")
        if self.chance(0.6):
            for o in L.openings:
                if o["type"] != "door":
                    continue
                w = L.walls[o["wall"]]
                u = o["offset"] + rng.choice([-1, 1]) * (o["width"] / 2 + 0.6)
                if L.on_wall(piece("coatRack"), w, u, base_y=1.5) is not None:
                    break
        if self.chance(0.6):
            side = L.against_wall(
                self.pick("sideTable", "sideTableDrawers", "cabinetBedDrawerTable"),
                walls=long_walls,
                front_clear=0.4,
                category="console table",
            )
            if side is not None:
                self.decorate(L, side, rng.choice([1, 2]), ("plantSmall1", "plantSmall3", "lampSquareTable", "books"))
        door = L.openings[0]
        mat = piece("rugDoormat")
        w = L.walls[door["wall"]]
        x, z = w.point(door["offset"], 0.35)
        if self.chance(0.7) and L.free_at(mat, x, z, w.yaw) is not None:
            L.add(L.put(mat, x, z, w.yaw))
        if self.chance(0.4):
            runner = piece("rugRectangle")
            longest = max(L.walls, key=lambda w: w.length)
            cx, cz = L.room_centre()
            yaw = (longest.yaw + 90) % 360
            if L.free_at(runner, cx, cz, yaw) is not None:
                L.add(L.put(runner, cx, cz, yaw, category="runner"))
        self.corner_plants(L, rng.choice([0, 1]))
        if self.chance(0.4):
            wl = rng.choice(long_walls or L.walls)
            lamp = L.on_wall(piece("lampWall"), wl, wl.length / 2 + rng.uniform(-0.5, 0.5), base_y=1.7)
            if lamp is not None:
                self.bulb(lamp, 20)
        if self.chance(0.6):
            self.ceiling_light(L)

    def basement(self, L: Layout) -> None:
        rng = self.rng
        shelves = 0
        for _ in range(rng.randint(1, 4)):
            bc = L.against_wall(
                self.pick("bookcaseOpen", "bookcaseClosed", "bookcaseOpenLow"), front_clear=0.6, optional=shelves > 0
            )
            if bc is not None:
                shelves += 1
                self.decorate(L, bc, rng.choice([0, 1]), ("cardboardBoxClosed", "books", "radio"))
        boxes = []
        for _ in range(rng.randint(3, 9)):
            b = L.free(
                self.pick("cardboardBoxClosed", "cardboardBoxClosed", "cardboardBoxOpen"),
                margin=0.05,
                yaws=[rng.choice([0, 0, 5, 12, 20, 45, 90])],
                optional=len(boxes) > 1,
            )
            if b is None:
                b = L.against_wall(
                    self.pick("cardboardBoxClosed", "cardboardBoxOpen"),
                    margin=0.03,
                    relate=False,
                    optional=len(boxes) > 1,
                )
            if b is None:
                continue
            boxes.append(b)
            if b.piece.top is not None and self.chance(0.35):
                top = L.on_top(
                    b,
                    piece("cardboardBoxClosed"),
                    where="centre",
                    turn=rng.choice([0, 90]),
                    child=self.chance(0.5),
                    relate=self.chance(0.5),
                )
                if top is not None:
                    boxes.append(top)
        if len(boxes) < 2:
            raise Retry("too few boxes")
        self.r.anchor = boxes[0].id
        self.r.mention.append("box")
        self.r.counts["box"] = len(boxes)
        if self.chance(0.4):
            L.against_wall(self.pick("washer", "dryer", "washerDryerStacked"), front_clear=0.5)
        if self.chance(0.3):
            L.against_wall(piece("bench"), front_clear=0.3)
        if self.chance(0.3):
            L.free(self.pick("chair", "chairRounded"), margin=0.2, yaws=[rng.uniform(0, 360)])
        if self.chance(0.5):
            L.against_wall(piece("trashcan"), relate=False)
        self.ceiling_light(L)

    def lobby(self, L: Layout) -> None:
        rng = self.rng
        pair = self.desk_with_chair(L, piece("desk"), piece("chairDesk"), optional=False, equip=True)
        if pair is None:
            raise Retry("no room for the reception desk")
        desk, _ = pair
        self.r.anchor = desk.id
        self.r.mention.append("desk")
        chair = self.pick("chair", "chairCushion", "chairModernCushion", "chairModernFrameCushion")
        seats = 0
        for wall in self.quiet_walls(L, 1.5)[:2]:
            first = L.against_wall(chair, walls=[wall], margin=0.05, relate=False, optional=seats > 1)
            if first is None:
                continue
            seats += 1
            row = [first]
            for _ in range(rng.randint(1, 4)):
                nxt = L.beside(row[-1], chair, "left", gap=0.08)
                if nxt is None:
                    break
                row.append(nxt)
                seats += 1
            self.fabric_for(first)
            if len(row) > 1:
                L.relate("against_wall", a=row[-1].id, wall=f"{L.room}:{wall.index}", side="back")
        if seats < 2:
            raise Retry("no room for seats")
        self.r.counts["chair"] = sum(1 for p in L.placed.values() if _matches(p.semantic_category, "chair"))
        self.r.extra.append(f"seats for {seats} people waiting")
        table = L.free(self.pick("tableCoffeeSquare", "tableCoffeeGlassSquare", "tableCoffee"), margin=0.6)
        if table is not None:
            self.decorate(L, table, rng.choice([1, 2]), ("books", "plantSmall1", "plantSmall2"))
        self.corner_plants(L, rng.choice([1, 2, 3]))
        if self.chance(0.5):
            L.against_wall(piece("coatRackStanding"), where="corner", relate=False)
        if self.chance(0.6):
            L.against_wall(piece("trashcan"), relate=False)
        if self.chance(0.4):
            L.against_wall(self.pick("bookcaseOpenLow", "bookcaseOpen"), front_clear=0.5)
        for _ in range(rng.choice([1, 2])):
            self.ceiling_light(
                L, at=(rng.uniform(L.bounds[0] + 1, L.bounds[2] - 1), rng.uniform(L.bounds[1] + 1, L.bounds[3] - 1))
            )

    def fill(self, L: Layout) -> None:
        """Tops up a sparse room with pieces that suit it, so no room comes out nearly empty."""
        kind = self.r.room_type
        want = MINIMUM.get(kind, 6)
        options = FILLERS.get(kind, FILLERS["default"])
        for _ in range(24):
            if sum(1 for p in L.placed.values() if p.piece.mount != "covering") >= want:
                return
            name, how = self.rng.choice(options)
            p = piece(name)
            if how == "corner":
                q = L.against_wall(p, where="corner", relate=False)
            elif how == "wall":
                q = L.against_wall(p, front_clear=0.4 if p.h > 1.0 else 0.0)
            elif how == "hung":
                w = self.rng.choice(L.walls)
                q = L.on_wall(
                    p, w, self.rng.uniform(0.3, max(0.31, w.length - 0.3)), base_y=round(self.rng.uniform(1.35, 1.6), 2)
                )
            elif how == "floor":
                q = L.free(p, margin=0.15, yaws=[self.rng.choice([0, 0, 10, 30, 90])])
            else:  # on top of something already there
                bases = [
                    b for b in L.placed.values() if b.piece.top is not None and b.support == "floor" and b.rot is None
                ]
                q = L.on_top(self.rng.choice(bases), p, child=self.chance(0.6)) if bases else None
            if q is not None and q.piece.category in ("floor lamp", "table lamp", "wall lamp"):
                self.bulb(q, 30)

    # Deliberate oddities ------------------------------------------------------------------------

    def oddities(self, L: Layout) -> None:
        theme = self.d.theme
        if not theme:
            return
        rng = self.rng
        quoted = self.chance(0.4)
        kinds = list(THEMES[theme])
        rng.shuffle(kinds)
        done = 0
        for kind in kinds[: rng.choice([1, 2, 2, 3])]:
            if self.oddity(L, kind, theme, quoted and done == 0):
                done += 1
        if done == 0:
            raise Retry("the theme found nothing to make odd")

    def _intent(self, allows: list[str], theme: str, note: str, quote: Optional[str]) -> dict[str, Any]:
        if quote:
            self.d.quotes.append(quote)
            return {"allows": allows, "source": "prompt", "quote": quote}
        return {"allows": allows, "source": "builder", "licence": theme, "note": note}

    def _a(self, word: str, p: Placed) -> str:
        """'the sofa' when the brief already names one, else 'a sofa'."""
        return f"the {word}" if p.semantic_category in self.r.mention else _article(word)

    def _free_movable(self, L: Layout, cats: tuple[str, ...]) -> list[Placed]:
        return [
            p
            for p in L.placed.values()
            if p.piece.category in cats
            and p.support == "floor"
            and p.optional
            and p.group is None
            and not any(q.on == p.id for q in L.placed.values())
            and not p.intent
        ]

    def oddity(self, L: Layout, kind: str, theme: str, quoted: bool) -> bool:
        rng = self.rng
        if kind == "knocked_over":
            options = self._free_movable(
                L, ("chair", "desk chair", "bar stool", "floor lamp", "box", "trash can", "armchair", "potted plant")
            )
            rng.shuffle(options)
            for p in options:
                r = _mul(_ry(p.yaw), _rz(rng.choice([90, -90])))
                if self._replace(L, p, r, lift=True):
                    word = {
                        "desk chair": "chair",
                        "bar stool": "stool",
                        "trash can": "bin",
                        "potted plant": "plant",
                    }.get(p.piece.category, p.piece.category)
                    quote = f"{self._a(word, p)} knocked over on its side" if quoted else None
                    p.intent.append(
                        self._intent(["upright"], theme, f"The {word} lies on its side, as if knocked over.", quote)
                    )
                    return True
            return False
        if kind == "leaning":
            options = [
                p
                for p in L.placed.values()
                if p.piece.category in ("bookcase", "low bookcase", "coat rack")
                and p.support == "floor"
                and p.optional
                and not any(q.on == p.id for q in L.placed.values())
                and not p.intent
            ]
            rng.shuffle(options)
            for p in options:
                angle = rng.uniform(16, 24)
                r = _mul(_ry(p.yaw), _rx(angle))
                if self._replace(L, p, r, lift=True, shift=0.0):
                    quote = (
                        "a bookcase leaning forward as if about to fall"
                        if quoted and p.piece.category == "bookcase"
                        else None
                    )
                    p.intent.append(
                        self._intent(
                            ["upright"],
                            theme,
                            f"The {p.piece.category} leans {angle:.0f}° forward, about to topple.",
                            quote,
                        )
                    )
                    return True
            return False
        if kind == "floating":
            options = self._free_movable(
                L,
                (
                    "chair",
                    "desk chair",
                    "box",
                    "teddy bear",
                    "floor lamp",
                    "armchair",
                    "bar stool",
                    "potted plant",
                    "trash can",
                    "ottoman",
                ),
            )
            options += [
                p
                for p in L.placed.values()
                if p.support == "node"
                and not p.intent
                and p.piece.category in ("books", "table lamp", "small plant", "radio", "laptop", "pillow")
            ]
            rng.shuffle(options)
            for p in options:
                lift = round(rng.uniform(0.35, max(0.4, min(1.2, L.height - p.top - 0.3))), 2)
                if p.top + lift > L.height - 0.1:
                    continue
                spin = rng.choice([-1, 1]) * rng.uniform(18, 35) if theme == "zero_gravity" or self.chance(0.4) else 0.0
                r = _mul(_ry(p.yaw), _rx(spin)) if spin else None
                if self._replace(L, p, r, lift=False, raise_by=lift):
                    word = {
                        "desk chair": "chair",
                        "small plant": "plant",
                        "table lamp": "lamp",
                        "potted plant": "plant",
                    }.get(p.piece.category, p.piece.category)
                    allows = ["floating"] + (["upright"] if spin else [])
                    if theme == "zero_gravity":
                        note, phrase = f"The {word} drifts in zero gravity.", f"the {word} drifting in the air"
                    else:
                        note, phrase = (
                            f"The {word} hovers {lift:.1f} m up, unexplained.",
                            f"{self._a(word, p)} floating in mid-air",
                        )
                    p.intent.append(self._intent(allows, theme, note, phrase if quoted else None))
                    return True
            return False
        if kind == "ceiling_chair":
            chair = self.pick("chair", "chairCushion", "chairRounded", "chairDesk")
            for _ in range(20):
                x, z = rng.uniform(L.bounds[0] + 0.5, L.bounds[2] - 0.5), rng.uniform(
                    L.bounds[1] + 0.5, L.bounds[3] - 0.5
                )
                yaw = rng.uniform(0, 360)
                r = _mul(_rx(180), _ry(yaw))
                y0, y1, x0, x1, z0, z1 = _rotated_box(chair, r)
                y = L.height
                poly = [(x + x0, z + z0), (x + x1, z + z0), (x + x1, z + z1), (x + x0, z + z1)]
                if not L.inside(poly) or not L.fits(poly, y + y0 - 0.05, y + y1):
                    continue
                p = Placed(
                    L.new_id("ceiling_chair"),
                    chair,
                    round(x, 3),
                    round(y, 3),
                    round(z, 3),
                    yaw,
                    poly,
                    support="ceiling",
                    rot=euler_xyz(r),
                    name="Chair on the ceiling",
                    span=(y + y0, y + y1),
                )
                L.add(p)
                quote = "a chair stuck upside down on the ceiling" if quoted else None
                p.intent.append(self._intent(["upright"], theme, "A chair hangs upside down from the ceiling.", quote))
                return True
            return False
        if kind == "sunk":
            options = [
                p
                for p in L.placed.values()
                if p.piece.category in ("bed", "sofa", "dining table", "desk", "bookcase", "armchair")
                and p.support == "floor"
                and not p.intent
                and p.group is None
            ]
            rng.shuffle(options)
            for p in options:
                depth = round(rng.uniform(0.2, min(0.35, p.piece.h * 0.45)), 2)
                riders = [q for q in L.placed.values() if q.on == p.id]
                if any(not q.child for q in riders):
                    continue
                p.y = round(p.y - depth, 3)
                for b in L.boxes:
                    if b.id == p.id:
                        b.y0 -= depth
                        b.y1 -= depth
                quote = f"{self._a(p.piece.category, p)} half sunk into the floor" if quoted else None
                p.intent.append(
                    self._intent(
                        ["sunk"],
                        theme,
                        f"The {p.piece.category} has sunk {depth:.2f} m into the floor, like a dream.",
                        quote,
                    )
                )
                return True
            return False
        if kind == "pile":
            for _ in range(15):
                box = piece("cardboardBoxClosed")
                base = L.free(box, margin=0.15, yaws=[rng.uniform(0, 360)], stem="box")
                if base is None:
                    return False
                lean = L.put(
                    box,
                    base.x + rng.uniform(-0.15, 0.15),
                    base.z + rng.uniform(-0.15, 0.15),
                    (base.yaw + rng.uniform(20, 40)) % 360,
                    y=0.0,
                    stem="box",
                )
                if not L.inside(lean.poly):
                    L.remove(base.id)
                    continue
                L.add(lean)
                quote = "boxes piled on top of each other" if quoted else None
                lean.intent.append(
                    self._intent(["overlap"], theme, "Boxes shoved together in a heap, overlapping.", quote)
                )
                return True
            return False
        return False

    def _replace(
        self,
        L: Layout,
        p: Placed,
        r: Optional[list[list[float]]],
        lift: bool,
        shift: float = 0.0,
        raise_by: float = 0.0,
    ) -> bool:
        """Re-poses a placed piece with rotation r (room space), checking it still fits."""
        if r is None:
            y0, y1, x0, x1, z0, z1 = 0.0, p.piece.h, None, None, None, None
            poly = p.poly
        else:
            y0, y1, x0, x1, z0, z1 = _rotated_box(p.piece, r)
            poly = [(p.x + x0, p.z + z0), (p.x + x1, p.z + z0), (p.x + x1, p.z + z1), (p.x + x0, p.z + z1)]
        y = (-y0 if lift else 0.0) + raise_by + p.y
        if not L.inside(poly):
            return False
        others = [b for b in L.boxes if b.id != p.id]
        saved = L.boxes
        L.boxes = others
        ok = L.fits(poly, y + y0 + 0.001, y + y1)
        L.boxes = saved
        if not ok:
            return False
        for b in L.boxes:
            if b.id == p.id:
                b.poly, b.y0, b.y1 = poly, y + y0, y + y1
        p.span = (y + y0, y + y1)
        p.poly = poly
        p.child = False  # written in room space from now on, so the new pose is what the file says
        if r is not None:  # turned over: it no longer faces, lines up with or sits against what it did
            L.relations = [rel for rel in L.relations if p.id not in (rel.get("a"), rel.get("b"), rel.get("around"))]
        p.y = round(y, 3)
        if r is not None:
            p.rot = euler_xyz(r)
        return True

    # Putting it together ---------------------------------------------------------------------

    def plan_suite(self) -> None:
        """Rooms in a row along +x, back to back, each joined to the next by a door on one side and an open doorway on the other."""
        rng, rooms = self.rng, self.d.rooms
        height = self.size(rooms[0].room_type)[2]
        for i, r in enumerate(rooms):
            w, d, _ = self.size(r.room_type)
            if 0 < i < len(rooms) - 1:  # doors on both sides: leave room between them
                w, d = max(w, 2.9), max(d, 2.8)
            r.size = (w, d, height)
            r.outside_doors = 0
        entry = next((r for r in rooms if r.room_type == "hallway"), rooms[0])
        entry.outside_doors = 1
        x = 0.0
        for i, r in enumerate(rooms):
            r.offset = (round(x, 3), 0.0)
            x += r.size[0] + 2 * WALL
        for a, b in zip(rooms, rooms[1:]):
            width = round(rng.uniform(0.8, 0.95), 2)
            lo, hi = 0.25 + width / 2, min(a.size[1], b.size[1]) - 0.25 - width / 2
            if hi < lo:
                raise Retry("neighbouring rooms too shallow for a door")
            c = round(lo + (hi - lo) * rng.random() ** 2, 3)
            door_in_a = rng.random() < 0.5
            door_h = round(min(2.1, height - 0.2), 2)
            for here, there, wall, offset, is_door in (
                (a, b, 1, c, door_in_a),
                (b, a, 3, round(b.size[1] - c, 3), not door_in_a),
            ):
                link = {
                    "id": f"{here.id}_to_{there.id}",
                    "type": "door" if is_door else "archway",
                    "wall": wall,
                    "offset": offset,
                    "width": width,
                    "height": door_h,
                    "connects_to": there.id,
                }
                if is_door:
                    link.update(swing="in", hinge=rng.choice(["left", "right"]), open=rng.choice([0.0, 0.5, 0.8]))
                here.links.append(link)
                here.shared.add(wall)

    def passages(self, r: RoomDraw, L: Layout) -> None:
        """Open doorways have no door to keep clear, so each gets an explicit clearance zone."""
        for o in L.openings:
            if o["type"] != "archway":
                continue
            wall = L.walls[o["wall"]]
            u0, u1 = o["offset"] - o["width"] / 2, o["offset"] + o["width"] / 2
            area = [wall.point(u0, 0.0), wall.point(u1, 0.0), wall.point(u1, 0.9), wall.point(u0, 0.9)]
            zid = f"{o['id']}_clear"
            r.zones.append(
                {
                    "kind": "zone",
                    "id": zid,
                    "purpose": "clearance",
                    "area": [[round(x, 3), round(z, 3)] for x, z in area],
                    "y_range": [0.0, 2.0],
                    "label": f"way through to {o['connects_to'].replace('_', ' ')}",
                }
            )
            L.relate("clear", zone=zid, source="default")

    def furnish(self) -> None:
        if self.d.suite:
            self.plan_suite()
        odd = next((r for r in self.d.rooms if r.room_type in THEMED_ROOMS), None) if self.d.theme else None
        for r in self.d.rooms:
            self.r = r
            L = self.shell()
            r.layout = L
            getattr(self, r.room_type)(L)
            self.fill(L)
            self.passages(r, L)
            if r is odd:
                self.oddities(L)
            r.floor, r.walls = self.room_materials()
        if self.d.theme and odd is None:
            raise Retry("no room to theme")
        self.r = self.d.rooms[0]

    def environment(self) -> dict[str, Any]:
        clock, elevation, azimuth, kelvin, lux, sky = self.d.time
        env: dict[str, Any] = {
            "sky": {"type": "color", "color": sky},
            "ambient": {"type": "hemisphere", "color": "#fff4e6", "ground_color": "#6b5a4a", "intensity": 0.55},
            "time_of_day": clock,
            "tone_mapping": "aces",
            "exposure": 1.1,
        }
        if elevation is not None:
            env["sun"] = {
                "elevation_deg": elevation,
                "azimuth_deg": round((azimuth + self.rng.uniform(-20, 20)) % 360, 1),
                "kelvin": kelvin,
                "intensity_lux": lux,
            }
        else:
            env["ambient"]["intensity"] = 0.3
            env["exposure"] = 1.25
        if self.d.theme in ("haunted", "horror", "creepy", "abandoned"):
            env["ambient"].update(color="#9fb0c8", intensity=0.25)
        return env

    def room_materials(self) -> tuple[str, str]:
        kind = self.rng.choice(FLOOR_KIND[self.r.room_type])
        preset, colours = self.rng.choice(FLOORS[kind])
        prefix = f"{self.r.id}_" if self.d.suite else ""
        floor = self.material(f"{prefix}floor", preset, self.colour(colours))
        wall_preset, wall_colours = self.rng.choice(
            WALLS
            if self.r.room_type != "bathroom"
            else [WALLS[0], ("tile_ceramic_white", ["#f4f4f4", "#dfe8ea", "#e9e2d5"])]
        )
        walls = self.material(
            f"{prefix}walls",
            wall_preset,
            self.colour(wall_colours + ([self.d.palette[0]] if wall_preset.startswith("plaster") else [])),
        )
        return floor, walls


MINIMUM = {"bathroom": 4, "laundry": 4, "hallway": 4, "kitchen": 6, "basement": 7}
"""Fewest pieces a room of each type should hold (rugs not counted)."""

FILLERS = {
    "default": [
        ("pottedPlant", "corner"),
        ("trashcan", "wall"),
        ("lampSquareFloor", "corner"),
        ("bookcaseOpenLow", "wall"),
        ("plantSmall1", "top"),
        ("books", "top"),
        ("coatRackStanding", "corner"),
        ("cardboardBoxClosed", "floor"),
    ],
    "bathroom": [
        ("trashcan", "wall"),
        ("bathroomCabinet", "hung"),
        ("plantSmall3", "top"),
        ("pottedPlant", "corner"),
        ("washer", "wall"),
    ],
    "laundry": [
        ("cardboardBoxClosed", "floor"),
        ("bookcaseOpenLow", "wall"),
        ("trashcan", "wall"),
        ("kitchenCabinetUpper", "hung"),
        ("coatRack", "hung"),
        ("plantSmall2", "top"),
    ],
    "hallway": [
        ("pottedPlant", "corner"),
        ("coatRack", "hung"),
        ("lampWall", "hung"),
        ("plantSmall1", "top"),
        ("benchCushionLow", "wall"),
    ],
    "kitchen": [
        ("trashcan", "wall"),
        ("pottedPlant", "corner"),
        ("kitchenMicrowave", "top"),
        ("plantSmall2", "top"),
        ("toaster", "top"),
    ],
    "basement": [
        ("cardboardBoxClosed", "floor"),
        ("cardboardBoxOpen", "floor"),
        ("televisionVintage", "floor"),
        ("radio", "top"),
        ("chairRounded", "floor"),
        ("bookcaseOpen", "wall"),
    ],
}
"""Pieces that may top up a sparse room, and how each is placed."""

WALL = 0.12
"""Room wall thickness (the IR's default). Rooms in a suite sit two walls apart."""

SUITES = [
    ("bedroom", "bathroom"),
    ("hallway", "bedroom", "bathroom"),
    ("kitchen", "dining_room"),
    ("living_room", "kitchen"),
    ("living_room", "dining_room", "kitchen"),
    ("hallway", "living_room", "kitchen"),
    ("office", "lobby"),
    ("bedroom", "kids_room"),
    ("kids_room", "bathroom", "bedroom"),
    ("kitchen", "laundry"),
    ("bedroom", "office"),
    ("bathroom", "bedroom", "hallway", "kitchen"),
]
"""Room sequences for multi-room worlds, laid out left to right."""


def _asset_node(p: Placed, children: list[dict[str, Any]], local: bool, room: str) -> dict[str, Any]:
    if local and p.local is not None:
        lx, ly, lz, lyaw = p.local
        xform: dict[str, Any] = {"pos": [lx, round(ly, 3), lz], "yaw": round(lyaw, 1)}
    elif p.rot is not None:
        xform = {"pos": [p.x, p.y, p.z], "rot": list(p.rot)}
    else:
        xform = {"pos": [p.x, p.y, p.z], "yaw": round(p.yaw % 360, 1)}
    node: dict[str, Any] = {"kind": "asset", "id": p.id, "asset": snake(p.piece.name), "xform": xform}
    if p.name:
        node["name"] = p.name
    if p.category:
        node["semantic"] = (
            {"category": p.category, "function": p.piece.function} if p.piece.function else {"category": p.category}
        )
    node["support"] = {
        "floor": {"on": "floor"},
        "node": {"on": "node", "target": p.on, "surface": "top"},
        "wall": {"on": "wall", "wall": f"{room}:{p.wall}"},
        "ceiling": {"on": "ceiling"},
    }[p.support]
    if p.materials:
        node["materials"] = p.materials
    if p.intent:
        node["intent"] = p.intent
    if p.light:
        light = dict(p.light)
        pos = light.pop("pos")
        children = [{"kind": "light", "id": f"{p.id}_bulb", "xform": {"pos": pos}, **light}] + children
    if children:
        node["children"] = children
    return node


def _relation_refs(rel: dict[str, Any]) -> list[str]:
    return [rel[k] for k in ("a", "b", "around", "target") if isinstance(rel.get(k), str)]


def _placed(d: Draw) -> dict[str, Placed]:
    return {k: p for r in d.rooms for k, p in r.layout.placed.items()}


def _room_node(b: _Builder, r: RoomDraw, cameras: list[dict[str, Any]]) -> dict[str, Any]:
    d, L = b.d, r.layout
    kids: dict[str, list[Placed]] = {}
    for p in L.placed.values():
        if p.child and p.on in L.placed:
            kids.setdefault(p.on, []).append(p)

    def build(p: Placed) -> dict[str, Any]:
        local = p.child and p.on in L.placed
        return _asset_node(p, [build(c) for c in kids.get(p.id, [])], local=local, room=L.room)

    children: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in L.placed.values():
        if p.child and p.on in L.placed:
            continue
        (grouped.setdefault(p.group, []) if p.group else children).append(build(p))
    for gid, members in grouped.items():
        children.append(
            {
                "kind": "group",
                "id": gid,
                "semantic": {"category": d.groups[gid]["category"], "group": gid},
                "children": members,
            }
        )
    if not any(p.light and p.support == "ceiling" for p in L.placed.values()):
        cx, cz = L.room_centre()
        children.append(
            {
                "kind": "light",
                "id": f"{r.id}_light" if d.suite else "ceiling_light",
                "type": "point",
                "kelvin": 3000,
                "intensity": 110,
                "xform": {"pos": [round(cx, 2), round(L.height - 0.15, 2), round(cz, 2)]},
            }
        )
    children += r.zones + cameras
    node: dict[str, Any] = {
        "kind": "room",
        "id": r.id,
        "room_type": r.room_type,
        "outline": [list(q) for q in r.room["outline"]],
        "height": r.room["height"],
        "floor_material": r.floor,
        "wall_material": r.walls,
        "openings": r.room["openings"],
        "children": children,
    }
    xform = dict(d.xform or {})
    if d.suite:
        pos = xform.get("pos", [0.0, 0.0, 0.0])
        c, s_ = math.cos(math.radians(xform.get("yaw", 0.0))), math.sin(math.radians(xform.get("yaw", 0.0)))
        ox, oz = r.offset
        xform["pos"] = [round(pos[0] + c * ox + s_ * oz, 3), 0.0, round(pos[2] - s_ * ox + c * oz, 3)]
    if xform:
        node["xform"] = xform
    return node


def _cameras(b: _Builder) -> list[dict[str, Any]]:
    """A hero view of the first room's main piece and a plan view of everything, in the first room's space."""
    d = b.d
    first = d.rooms[0]
    L = first.layout
    out: list[dict[str, Any]] = []
    anchor = L.placed.get(first.anchor) if first.anchor else None
    if anchor is not None:
        doors = [L.walls[o["wall"]].point(o["offset"], 0.0) for o in L.openings if o["type"] in ("door", "archway")]

        def view(q: Pt) -> float:
            near_door = min((math.dist(q, dp) for dp in doors), default=9.0)
            return math.dist(q, (anchor.x, anchor.z)) - (3.0 if near_door < 1.4 else 0.0)

        corner = max(L.outline, key=view)
        cx, cz = L.room_centre()
        k = 0.35 / max(0.01, math.dist(corner, (cx, cz)))
        cam = (corner[0] + (cx - corner[0]) * k, corner[1] + (cz - corner[1]) * k)
        out.append(
            {
                "kind": "camera",
                "id": "hero_cam",
                "purpose": "hero",
                "fov_deg": 70,
                "xform": {"pos": [round(cam[0], 2), 1.6, round(cam[1], 2)]},
                "look_at": anchor.id,
            }
        )
    x0 = min(r.offset[0] + r.layout.bounds[0] for r in d.rooms)
    x1 = max(r.offset[0] + r.layout.bounds[2] for r in d.rooms)
    z0 = min(r.offset[1] + r.layout.bounds[1] for r in d.rooms)
    z1 = max(r.offset[1] + r.layout.bounds[3] for r in d.rooms)
    top = max(r.layout.height for r in d.rooms)
    out.append(
        {
            "kind": "camera",
            "id": "plan_cam",
            "purpose": "top_down",
            "projection": "orthographic",
            "ortho_height": round(max(x1 - x0, z1 - z0) + 0.8, 2),
            "xform": {
                "pos": [round((x0 + x1) / 2, 2), round(top + 3.5, 2), round((z0 + z1) / 2, 2)],
                "rot": [-90, 0, 0],
            },
        }
    )
    return out


def assemble(b: _Builder, required: bool = True) -> tuple[dict[str, Any], list[Optional[tuple[int, int]]]]:
    """The world as a JSON-ready dict, and for each world relation the (room, index) it came from in a layout."""
    d = b.d
    placed = _placed(d)
    cameras = _cameras(b)
    nodes = [_room_node(b, r, cameras if i == 0 else []) for i, r in enumerate(d.rooms)]
    relations: list[dict[str, Any]] = []
    origin: list[Optional[tuple[int, int]]] = []
    turn = (d.xform or {}).get("yaw", 0.0)
    for ri, r in enumerate(d.rooms):
        L = r.layout
        for i, rel in enumerate(L.relations):
            if all(ref in placed for ref in _relation_refs(rel)):
                rel = dict(rel)
                if rel["rel"] == "faces_direction":
                    rel["yaw"] = round((rel["yaw"] + turn) % 360, 1)
                relations.append(rel)
                origin.append((ri, i))
    for r in d.rooms:
        L = r.layout
        mine = list(L.placed.values())
        for c in r.mention:
            if any(_matches(p.semantic_category, c) for p in mine):
                relations.append({"rel": "requires", "category": c, "region": L.room, "source": "prompt"})
                origin.append(None)
        for cat in r.counts:
            have = sum(1 for p in mine if _matches(p.semantic_category, cat))
            if have:
                relations.append(
                    {"rel": "count", "category": cat, "min": have, "max": have, "region": L.room, "source": "prompt"}
                )
                origin.append(None)
    everything = list(placed.values())
    forbidden = []
    for r in d.rooms:
        r.forbidden = [c for c in r.forbidden if not any(_matches(p.semantic_category, c) for p in everything)]
        forbidden += [c for c in r.forbidden if c not in forbidden]
    for c in forbidden:
        relations.append({"rel": "forbids", "category": c, "source": "prompt"})
        origin.append(None)
    the_brief = brief(b, required, forbidden)
    first = d.rooms[0].room_type
    world = {
        "ir_version": "1.0",
        "id": f"flat_{d.seed}" if d.suite else f"{first}_{d.seed}",
        "name": the_brief.pop("title"),
        "seed": d.seed,
        "brief": the_brief,
        "environment": b.environment_cache,
        "materials": d.materials,
        "assets": {
            snake(n): piece(n).asset().model_dump(mode="json", exclude_none=True)
            for n in sorted({p.piece.name for p in everything})
        },
        "nodes": nodes,
        "relations": relations,
        "provenance": {"by": "generator", "model": "world_ir.worldgen", "note": f"seed {d.seed}; validated clean"},
    }
    return world, origin


def _room_name(kind: str) -> str:
    return NAMES.get(kind, kind.replace("_", " "))


def brief(b: _Builder, required: bool, forbidden: list[str]) -> dict[str, Any]:
    d, rng = b.d, random.Random(f"brief:{b.d.seed}")
    first = d.rooms[0]
    x0, z0, x1, z1 = first.layout.bounds
    area = (x1 - x0) * (z1 - z0)
    kind = first.room_type
    small = {"bathroom": 4, "laundry": 4, "hallway": 6}.get(kind, 12)
    large = {"classroom": 55, "cafe": 50}.get(kind, 24)
    size = "" if d.suite else ("small" if area < small else ("large" if area > large else ""))
    moods = [m.replace("_", "-") for m in d.mood if m != "zero_gravity"]
    words = [w for w in [size, *moods, d.style.replace("_", "-") if rng.random() < 0.5 else ""] if w]
    words = words[: rng.choice([1, 2, 3])]
    if d.theme == "zero_gravity":
        words.append("zero-gravity")
    adjs = ", ".join(words)

    def things_in(r: RoomDraw) -> list[str]:
        mine = list(r.layout.placed.values())
        present = [c for c in dict.fromkeys(r.mention) if any(_matches(p.semantic_category, c) for p in mine)]
        out = [rng.choice(PHRASES.get(c, [_article(c)])) for c in present]
        if "chair" in r.counts and r.room_type == "dining_room":
            n = sum(1 for p in mine if _matches(p.semantic_category, "chair"))
            out.append(f"{n} chairs" if rng.random() < 0.5 else f"seating for {n}")
        return out + r.extra

    lead = rng.choice(["A", "Make a", "I'd like a", "Design a", "Build me a", "Create a", "Can you make a"])
    if d.suite:
        home = rng.choice(["flat", "apartment", "small home", "studio flat"])
        rooms = [_article(_room_name(r.room_type)) for r in d.rooms]
        text = f"{lead} {adjs + ' ' if adjs else ''}{home} with {_join(rooms)}"
        if d.quotes:
            text += ", and " + _join(d.quotes)
        text += "."
        for r in d.rooms:
            things = things_in(r)
            if things:
                text += " " + rng.choice(
                    ["The {room} needs {x}.", "In the {room}, {x}.", "Put {x} in the {room}."]
                ).format(room=_room_name(r.room_type), x=_join(things))
        title = f"{(words[0] + ' ') if words else ''}{home}"
    else:
        room = _room_name(kind)
        text = f"{lead} {adjs + ' ' if adjs else ''}{room}"
        if rng.random() < 0.5:
            text += " " + rng.choice(PURPOSES.get(kind, ["to live in"]))
        things = things_in(first)
        if things:
            text += " with " + _join(things)
        if d.quotes:
            text += ", and " + _join(d.quotes)
        text += "."
        title = f"{(words[0] + ' ') if words else ''}{room}"
    for a, an in ((" a a", " an a"), (" a e", " an e"), (" a i", " an i"), (" a o", " an o"), (" a u", " an u")):
        text = text.replace(a, an)
    if text[:3] in ("A a", "A e", "A i", "A o", "A u"):
        text = "An" + text[1:]
    if lead.startswith("Can you"):
        text = text.replace(".", "?", 1)
    if forbidden:
        x = "TV" if forbidden[0] == "television" else forbidden[0]
        text += " " + rng.choice(["No {x}, please.", "Don't add a {x}.", "Leave out the {x}."]).format(x=x)
    reqs = []
    if required:
        for r in d.rooms:
            mine = list(r.layout.placed.values())
            present = [c for c in dict.fromkeys(r.mention) if any(_matches(p.semantic_category, c) for p in mine)]
            region = r.room_type if d.suite else None
            reqs += [{"category": c, "source": "prompt", **({"region": region} if region else {})} for c in present]
            anchor = r.layout.placed.get(r.anchor) if r.anchor else None
            if anchor is not None and anchor.semantic_category not in present:
                reqs.append(
                    {
                        "category": anchor.semantic_category,
                        "source": "archetype",
                        **({"region": region} if region else {}),
                    }
                )
    return {
        "prompt": text,
        "setting": "interior",
        "archetype": "apartment" if d.suite else kind,
        "style": [d.style],
        "palette": d.palette,
        "time_of_day": d.time[0],
        "mood": d.mood,
        "required": reqs,
        "forbidden": forbidden,
        "title": title[0].upper() + title[1:],
    }


@dataclass
class Generated:
    """A clean world and how it was made."""

    world: World
    report: ValidationReport
    attempts: int
    dropped: list[str]


def _offenders(report: ValidationReport, placed: dict[str, Placed]) -> tuple[set[str], set[str], bool]:
    """(optional pieces to drop, relation refs to drop, whether something required is broken)."""
    drop, rels, stuck = set(), set(), False
    for issue in report.issues:
        if issue.severity == "info":
            continue
        if issue.relation is not None and issue.code.startswith("relation:"):
            rels.add(issue.relation)
            continue
        candidates = [c for c in (issue.part, issue.node, issue.other) if c and c in placed]
        group_members = [p.id for p in placed.values() if p.group in (issue.node, issue.other)]
        optional = [c for c in candidates + group_members if placed[c].optional]
        if optional:
            drop.add(optional[0])
        else:
            stuck = True
    return drop, rels, stuck


def _clean(report: ValidationReport) -> bool:
    return report.count("error") == report.count("warning") == report.count("ask") == 0


def _build(seed: int, rooms: list[str], theme: Optional[str], tries: int, place: bool) -> Generated:
    if theme is not None and theme not in THEMES:
        raise ValueError(f"unknown theme '{theme}'; choose from {', '.join(sorted(THEMES))}")
    last = None
    for attempt in range(tries):
        b = _Builder(seed, attempt, rooms, theme)
        try:
            b.furnish()
        except Retry as e:
            last = str(e)
            continue
        b.environment_cache = b.environment()
        if place and b.rng.random() < 0.3:
            b.d.xform = {"pos": [round(b.rng.uniform(-12, 12), 2), 0.0, round(b.rng.uniform(-12, 12), 2)]}
            if b.rng.random() < 0.5:
                b.d.xform["yaw"] = b.rng.choice([90.0, 180.0, 270.0])
        dropped: list[str] = []
        for _ in range(6):
            data, origin = assemble(b)
            try:
                world = World.model_validate(data)
            except ValueError as e:
                last = f"invalid world: {str(e)[:300]}"
                break
            report = validate(world)
            if _clean(report):
                return Generated(world, report, attempt + 1, dropped)
            placed = _placed(b.d)
            drop, rels, stuck = _offenders(report, placed)
            if stuck:
                last = "; ".join(i.message for i in report.issues if i.severity != "info")[:300]
                break
            for ref in rels:
                pos = int(ref.split("[")[1].rstrip("]")) if ref.startswith("relations[") else None
                if pos is None:
                    continue
                rel = data["relations"][pos]
                dropped.append(f"relation {rel['rel']}")
                if origin[pos] is not None:
                    ri, i = origin[pos]
                    b.d.rooms[ri].layout.relations[i] = {"rel": "_dropped"}
                    continue
                for r in b.d.rooms:
                    if rel["rel"] == "count":
                        r.counts.pop(rel["category"], None)
                    elif rel["rel"] == "requires":
                        r.mention = [m for m in r.mention if m != rel["category"]]
                    elif rel["rel"] == "forbids":
                        r.forbidden = [f for f in r.forbidden if f != rel["category"]]
            for node_id in drop:
                for r in b.d.rooms:
                    if node_id in r.layout.placed:
                        r.layout.remove(node_id)
                dropped.append(node_id)
            for r in b.d.rooms:
                r.layout.relations = [rel for rel in r.layout.relations if rel.get("rel") != "_dropped"]
            if not drop and not rels:
                last = "nothing left to drop"
                break
        else:
            last = "did not settle"
    raise RuntimeError(f"seed {seed}: no clean {'+'.join(rooms)} after {tries} tries ({last})")


def generate(
    seed: int, room_type: Optional[str] = None, *, theme: Optional[str] = None, tries: int = 12, place: bool = True
) -> Generated:
    """A furnished room that validates clean: no errors, warnings or questions. Deterministic per seed.

    place=True sometimes moves and turns the room away from the origin, so positions are not always room-relative.
    """
    kind = room_type or random.Random(f"kind:{seed}").choice(ROOM_KINDS)
    return _build(seed, [kind], theme, tries, place)


def generate_suite(
    seed: int, rooms: Optional[list[str]] = None, *, theme: Optional[str] = None, tries: int = 12, place: bool = True
) -> Generated:
    """Several rooms side by side, joined by doors, each furnished by its own programme. Clean, like generate()."""
    kinds = list(rooms or random.Random(f"suite:{seed}").choice(SUITES))
    return _build(seed, kinds, theme, tries, place)


def generate_many(count: int, seed: int = 0, suites: float = 0.2) -> list[Generated]:
    """count clean worlds from consecutive seeds: an even mix of room types, and a share of multi-room flats."""
    out = []
    for i in range(count):
        s = seed + i
        if random.Random(f"mix:{s}").random() < suites:
            out.append(generate_suite(s))
        else:
            out.append(generate(s, ROOM_KINDS[s % len(ROOM_KINDS)]))
    return out
