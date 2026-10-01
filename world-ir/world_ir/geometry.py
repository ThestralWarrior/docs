"""Matrix maths, colour and mesh builders shared by lowering and validators.

Matrices are row-major 4×4 lists. Lowered scenes store them column-major,
which is the order Three.js Matrix4.elements uses.
"""

import math
import random
from typing import Any

Mat = list[list[float]]
Vec3 = tuple[float, float, float]


# Matrices -----------------------------------------------------------------------


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


def xform_matrix(t: Any) -> Mat:
    """T · R · S for an IR Transform. Euler 'rot' uses Three.js XYZ order, which is Rx · Ry · Rz."""
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


def inverse(m: Mat) -> Mat:
    """Inverse of an affine matrix (rotation, scale, translation)."""
    a = [row[:3] for row in m[:3]]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    inv = [
        [
            (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det,
            (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det,
            (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det,
        ],
        [
            (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det,
            (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det,
            (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det,
        ],
        [
            (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det,
            (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det,
            (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det,
        ],
    ]
    t = [m[0][3], m[1][3], m[2][3]]
    out = identity()
    for r in range(3):
        for c in range(3):
            out[r][c] = inv[r][c]
        out[r][3] = -sum(inv[r][k] * t[k] for k in range(3))
    return out


def apply(m: Mat, p: tuple[float, ...]) -> Vec3:
    x, y, z = p
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def apply_dir(m: Mat, v: tuple[float, ...]) -> Vec3:
    x, y, z = v
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z,
        m[1][0] * x + m[1][1] * y + m[1][2] * z,
        m[2][0] * x + m[2][1] * y + m[2][2] * z,
    )


def column_major(m: Mat) -> tuple[float, ...]:
    return tuple(round(m[r][c], 6) + 0.0 for c in range(4) for r in range(4))


def bounds(points: list[tuple[float, ...]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    lo = tuple(round(min(p[i] for p in points), 4) for i in range(3))
    hi = tuple(round(max(p[i] for p in points), 4) for i in range(3))
    return lo, hi


def box_aabb(m: Mat, size: tuple[float, float, float]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """World AABB of a box whose origin is the centre of its bottom face."""
    w, h, d = size
    corners = [apply(m, (x, y, z)) for x in (-w / 2, w / 2) for y in (0.0, h) for z in (-d / 2, d / 2)]
    return bounds(corners)


def yaw_of(dx: float, dz: float) -> float:
    """Yaw that turns local +X onto the direction (dx, dz)."""
    return math.degrees(math.atan2(-dz, dx))


# Colour -------------------------------------------------------------------------


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


# Mesh builders ------------------------------------------------------------------

Mesh = tuple[list[float], list[int]]


def _flatten(vertices: list[Vec3]) -> list[float]:
    return [round(c, 5) for v in vertices for c in v]


def roof_mesh(style: str, w: float, d: float, pitch_deg: float, overhang: float) -> tuple[Mesh, str]:
    """A closed roof solid over a w × d footprint, ridge along z. Its base is the top of the walls (y = 0).

    A dome is a flat deck with a dome on the footprint's inscribed ellipse; pitch_deg sets its
    height (45 gives a hemisphere). Returns the mesh and the style actually built; styles without a builder fall back to gable.
    """
    built = style if style in ("flat", "shed", "gable", "hip", "pyramid", "dome") else "gable"
    t = math.tan(math.radians(pitch_deg))
    hw, hd = w / 2 + overhang, d / 2 + overhang
    drop = -overhang * t  # eaves sit below the wall tops when the roof overhangs
    if built == "dome":
        # A flat deck over the footprint with a dome on its inscribed ellipse; pitch 45 is a hemisphere.
        deck = 0.25
        positions, indices = roof_mesh("flat", w, d, 0.0, overhang)[0]
        rx, rz = w / 2, d / 2
        dome_p, dome_i = dome_mesh(rx, rz, min(rx, rz) * t)
        offset = len(positions) // 3
        positions = positions + [c + deck if k % 3 == 1 else c for k, c in enumerate(dome_p)]
        return (positions, indices + [i + offset for i in dome_i]), built
    if built == "flat":
        top = 0.25
        v = [
            (-hw, 0, -hd),
            (hw, 0, -hd),
            (hw, 0, hd),
            (-hw, 0, hd),
            (-hw, top, -hd),
            (hw, top, -hd),
            (hw, top, hd),
            (-hw, top, hd),
        ]
        f = [
            (0, 2, 1),
            (0, 3, 2),
            (4, 5, 6),
            (4, 6, 7),
            (0, 1, 5),
            (0, 5, 4),
            (1, 2, 6),
            (1, 6, 5),
            (2, 3, 7),
            (2, 7, 6),
            (3, 0, 4),
            (3, 4, 7),
        ]
    elif built == "shed":
        rise = w * t
        v = [(-hw, drop, -hd), (hw, drop, -hd), (hw, drop, hd), (-hw, drop, hd), (hw, rise, -hd), (hw, rise, hd)]
        f = [(0, 2, 1), (0, 3, 2), (0, 4, 5), (0, 5, 3), (1, 2, 5), (1, 5, 4), (0, 1, 4), (3, 5, 2)]
    elif built == "gable":
        ridge = (w / 2) * t
        v = [(-hw, drop, -hd), (hw, drop, -hd), (hw, drop, hd), (-hw, drop, hd), (0, ridge, -hd), (0, ridge, hd)]
        f = [(0, 2, 1), (0, 3, 2), (3, 5, 2), (0, 1, 4), (0, 4, 5), (0, 5, 3), (1, 2, 5), (1, 5, 4)]
    elif built == "hip":
        ridge = (min(w, d) / 2) * t
        half = max(0.0, (max(d, w) - min(d, w)) / 2)
        if d >= w:
            r0, r1 = (0, ridge, -half), (0, ridge, half)
        else:
            r0, r1 = (-half, ridge, 0), (half, ridge, 0)
        v = [(-hw, drop, -hd), (hw, drop, -hd), (hw, drop, hd), (-hw, drop, hd), r0, r1]
        if d >= w:
            f = [(0, 2, 1), (0, 3, 2), (0, 1, 4), (2, 3, 5), (1, 2, 5), (1, 5, 4), (3, 0, 4), (3, 4, 5)]
        else:
            f = [(0, 2, 1), (0, 3, 2), (1, 2, 5), (3, 0, 4), (0, 1, 5), (0, 5, 4), (2, 3, 4), (2, 4, 5)]
    else:  # pyramid
        apex = (min(w, d) / 2) * t
        v = [(-hw, drop, -hd), (hw, drop, -hd), (hw, drop, hd), (-hw, drop, hd), (0, apex, 0)]
        f = [(0, 2, 1), (0, 3, 2), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)]
    return (_flatten(v), [i for tri in f for i in tri]), built


def _icosphere(subdivisions: int = 1) -> tuple[list[Vec3], list[tuple[int, int, int]]]:
    p = (1 + math.sqrt(5)) / 2
    verts = [
        (-1, p, 0),
        (1, p, 0),
        (-1, -p, 0),
        (1, -p, 0),
        (0, -1, p),
        (0, 1, p),
        (0, -1, -p),
        (0, 1, -p),
        (p, 0, -1),
        (p, 0, 1),
        (-p, 0, -1),
        (-p, 0, 1),
    ]
    verts = [tuple(c / math.sqrt(1 + p * p) for c in v) for v in verts]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]  # fmt: skip
    for _ in range(subdivisions):
        cache: dict[tuple[int, int], int] = {}

        def midpoint(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = [(verts[a][i] + verts[b][i]) / 2 for i in range(3)]
                n = math.sqrt(sum(c * c for c in m))
                verts.append(tuple(c / n for c in m))
                cache[key] = len(verts) - 1
            return cache[key]

        new_faces = []
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new_faces
    return verts, faces


def rock_mesh(seed: int, roughness: float) -> Mesh:
    """A lumpy rock filling the unit box [-0.5, 0.5] × [0, 1] × [-0.5, 0.5]; scale it to the rock's size."""
    rng = random.Random(seed)
    verts, faces = _icosphere(1)
    bumps = [(rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.6, 1.4)) for _ in range(6)]
    shaped = []
    for v in verts:
        r = 1.0
        for bx, by, bz, k in bumps:
            r += roughness * 0.18 * k * (v[0] * bx + v[1] * by + v[2] * bz)
        r += roughness * 0.12 * rng.uniform(-1, 1)
        shaped.append((v[0] * r, v[1] * r * 0.8, v[2] * r))
    lo = [min(v[i] for v in shaped) for i in range(3)]
    hi = [max(v[i] for v in shaped) for i in range(3)]
    unit = [
        (
            (v[0] - lo[0]) / (hi[0] - lo[0]) - 0.5,
            (v[1] - lo[1]) / (hi[1] - lo[1]),
            (v[2] - lo[2]) / (hi[2] - lo[2]) - 0.5,
        )
        for v in shaped
    ]
    return _flatten(unit), [i for tri in faces for i in tri]


def ribbon_mesh(left: list[Vec3], right: list[Vec3]) -> Mesh:
    """A strip between two matching rows of points (path surfaces)."""
    verts = [p for pair in zip(left, right) for p in pair]
    indices: list[int] = []
    for i in range(len(left) - 1):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        indices += [a, c, b, b, c, d]
    return _flatten(verts), indices


def triangle_prism(points: list[tuple[float, float]], depth: float) -> Mesh:
    """A flat polygon (in x, y) extruded along z by depth, centred on z = 0. Used for gable ends."""
    n = len(points)
    v = [(x, y, -depth / 2) for x, y in points] + [(x, y, depth / 2) for x, y in points]
    f: list[tuple[int, int, int]] = []
    for i in range(1, n - 1):
        f += [(0, i + 1, i), (n, n + i, n + i + 1)]
    for i in range(n):
        j = (i + 1) % n
        f += [(i, j, n + j), (i, n + j, n + i)]
    return _flatten(v), [i for tri in f for i in tri]


def dome_mesh(rx: float, rz: float, height: float, segments: int = 24, rings: int = 8) -> Mesh:
    """A closed half-ellipsoid on y = 0: radii rx and rz, top at y = height."""
    v: list[Vec3] = [(0.0, height, 0.0)]
    for j in range(1, rings + 1):
        phi = (math.pi / 2) * j / rings
        for i in range(segments):
            theta = math.tau * i / segments
            v.append(
                (rx * math.sin(phi) * math.sin(theta), height * math.cos(phi), rz * math.sin(phi) * math.cos(theta))
            )
    ring = lambda j, i: 1 + (j - 1) * segments + i % segments  # noqa: E731
    f: list[tuple[int, int, int]] = [(0, ring(1, i), ring(1, i + 1)) for i in range(segments)]
    for j in range(1, rings):
        for i in range(segments):
            a, b, c, d = ring(j, i), ring(j, i + 1), ring(j + 1, i), ring(j + 1, i + 1)
            f += [(a, c, d), (a, d, b)]
    v.append((0.0, 0.0, 0.0))
    centre = len(v) - 1
    f += [(centre, ring(rings, i + 1), ring(rings, i)) for i in range(segments)]
    return _flatten(v), [i for tri in f for i in tri]


def sweep_frames(points: list[Vec3], closed: bool) -> list[tuple[Vec3, Vec3, Vec3]]:
    """Tangent, right and up at each point of a line, keeping up close to +Y."""
    n = len(points)
    frames = []
    right_prev: Vec3 = (1.0, 0.0, 0.0)
    for i in range(n):
        if closed:
            a, b = points[(i - 1) % n], points[(i + 1) % n]
        else:
            a, b = points[max(0, i - 1)], points[min(n - 1, i + 1)]
        t = tuple(b[k] - a[k] for k in range(3))
        length = math.sqrt(sum(c * c for c in t)) or 1.0
        t = tuple(c / length for c in t)
        r = (-t[2], 0.0, t[0])  # t × +Y
        rl = math.hypot(r[0], r[2])
        r = (r[0] / rl, 0.0, r[2] / rl) if rl > 1e-6 else right_prev
        right_prev = r
        up = (r[1] * t[2] - r[2] * t[1], r[2] * t[0] - r[0] * t[2], r[0] * t[1] - r[1] * t[0])
        frames.append((t, r, up))
    return frames


def simplify_line(points: list[Vec3], closed: bool, max_turn_deg: float = 3.0, max_length: float = 6.0) -> list[Vec3]:
    """Drops points where the line runs nearly straight, keeping turns, ends and at most max_length gaps."""
    if len(points) < 3:
        return list(points)
    kept = [points[0]]
    limit = math.cos(math.radians(max_turn_deg))
    for i in range(1, len(points) - 1):
        a, b = kept[-1], points[i + 1]
        d1 = [points[i][c] - a[c] for c in range(3)]
        d2 = [b[c] - points[i][c] for c in range(3)]
        n1, n2 = math.sqrt(sum(v * v for v in d1)), math.sqrt(sum(v * v for v in d2))
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        turn = sum(d1[c] * d2[c] for c in range(3)) / (n1 * n2)
        if turn < limit or n1 + n2 > max_length:
            kept.append(points[i])
    if not closed:
        kept.append(points[-1])
    elif math.dist(kept[-1], points[-1]) > 1e-6:
        kept.append(points[-1])
    return kept


def sweep_mesh(points: list[Vec3], profile: list[tuple[float, float]], closed: bool) -> Mesh:
    """A profile swept along a line. Profile points are [right, up] offsets, counter-clockwise
    seen from behind, looking along the line. Open lines get flat end caps."""
    frames = sweep_frames(points, closed)
    k = len(profile)
    v: list[Vec3] = []
    for p, (_, r, up) in zip(points, frames):
        for u, w in profile:
            v.append(tuple(round(p[c] + r[c] * u + up[c] * w, 4) for c in range(3)))
    f: list[tuple[int, int, int]] = []
    count = len(points) if closed else len(points) - 1
    for i in range(count):
        i2 = (i + 1) % len(points)
        for j in range(k):
            a, b = i * k + j, i * k + (j + 1) % k
            c, d = i2 * k + j, i2 * k + (j + 1) % k
            f += [(a, c, b), (b, c, d)]
    if not closed:
        for ring, start in ((0, True), (len(points) - 1, False)):
            base = len(v)
            centre = points[ring]
            v += [v[ring * k + j] for j in range(k)] + [centre]
            for j in range(k):
                a, b = base + j, base + (j + 1) % k
                f.append((base + k, a, b) if start else (base + k, b, a))
    return _flatten(v), [i for tri in f for i in tri]


def circle_profile(radius: float, segments: int) -> list[tuple[float, float]]:
    return [
        (radius * math.cos(math.tau * i / segments), radius * math.sin(math.tau * i / segments))
        for i in range(segments)
    ]


def rect_profile(width: float, height: float) -> list[tuple[float, float]]:
    w, h = width / 2, height / 2
    return [(w, -h), (w, h), (-w, h), (-w, -h)]
