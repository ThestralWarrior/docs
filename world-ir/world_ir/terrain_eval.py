"""Evaluates terrain nodes into height grids, and answers 'how high is the ground here?'.

Everything is deterministic and pure Python: the same terrain node always gives
the same heights, so lowering, validators and snapping agree.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .terrain import (
    Circle,
    HeightFlat,
    HeightGrid,
    HeightImage,
    HeightNoise,
    ModBump,
    ModCarvePath,
    ModCrater,
    ModErosion,
    ModFlatten,
    ModSmooth,
    ModTerrace,
)

# Noise --------------------------------------------------------------------------

_GRADIENTS = [(math.cos(a), math.sin(a)) for a in (i * math.tau / 16 for i in range(16))]
_F2 = 0.5 * (math.sqrt(3.0) - 1.0)
_G2 = (3.0 - math.sqrt(3.0)) / 6.0


class Noise2D:
    """Seeded 2D gradient noise: Perlin, simplex, value, Worley and ridged. Output is about -1..1."""

    def __init__(self, seed: int):
        rng = random.Random(seed)
        perm = list(range(256))
        rng.shuffle(perm)
        self.perm = perm + perm
        self.values = [rng.uniform(-1, 1) for _ in range(256)]
        self.seed = seed

    def _grad(self, ix: int, iy: int) -> tuple[float, float]:
        return _GRADIENTS[self.perm[(self.perm[ix & 255] + iy) & 255] & 15]

    def perlin(self, x: float, y: float) -> float:
        x0, y0 = math.floor(x), math.floor(y)
        fx, fy = x - x0, y - y0
        u, v = fx * fx * fx * (fx * (fx * 6 - 15) + 10), fy * fy * fy * (fy * (fy * 6 - 15) + 10)

        def dot(ix: int, iy: int) -> float:
            g = self._grad(ix, iy)
            return g[0] * (x - ix) + g[1] * (y - iy)

        a = dot(x0, y0) + u * (dot(x0 + 1, y0) - dot(x0, y0))
        b = dot(x0, y0 + 1) + u * (dot(x0 + 1, y0 + 1) - dot(x0, y0 + 1))
        return (a + v * (b - a)) * 1.414

    def simplex(self, x: float, y: float) -> float:
        s = (x + y) * _F2
        i, j = math.floor(x + s), math.floor(y + s)
        t = (i + j) * _G2
        x0, y0 = x - (i - t), y - (j - t)
        i1, j1 = (1, 0) if x0 > y0 else (0, 1)
        x1, y1 = x0 - i1 + _G2, y0 - j1 + _G2
        x2, y2 = x0 - 1 + 2 * _G2, y0 - 1 + 2 * _G2
        total = 0.0
        for dx, dy, gi, gj in ((x0, y0, i, j), (x1, y1, i + i1, j + j1), (x2, y2, i + 1, j + 1)):
            r = 0.5 - dx * dx - dy * dy
            if r > 0:
                g = self._grad(gi, gj)
                total += r**4 * (g[0] * dx + g[1] * dy)
        return total * 70.0

    def value(self, x: float, y: float) -> float:
        x0, y0 = math.floor(x), math.floor(y)
        fx, fy = x - x0, y - y0
        u, v = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

        def val(ix: int, iy: int) -> float:
            return self.values[self.perm[(self.perm[ix & 255] + iy) & 255]]

        a = val(x0, y0) + u * (val(x0 + 1, y0) - val(x0, y0))
        b = val(x0, y0 + 1) + u * (val(x0 + 1, y0 + 1) - val(x0, y0 + 1))
        return a + v * (b - a)

    def worley(self, x: float, y: float) -> float:
        x0, y0 = math.floor(x), math.floor(y)
        best = 9.0
        for ix in range(x0 - 1, x0 + 2):
            for iy in range(y0 - 1, y0 + 2):
                h = self.perm[(self.perm[ix & 255] + iy) & 255]
                px, py = ix + (self.values[h] + 1) / 2, iy + (self.values[(h + 101) & 255] + 1) / 2
                best = min(best, (px - x) ** 2 + (py - y) ** 2)
        return 1.0 - 2.0 * min(1.0, math.sqrt(best))

    def sample(self, algorithm: str, x: float, y: float) -> float:
        if algorithm == "ridged":
            return 1.0 - 2.0 * abs(self.simplex(x, y))
        return getattr(self, algorithm)(x, y)


def fbm(spec: HeightNoise, x: float, z: float, noise: Noise2D) -> float:
    total, amp, freq = spec.offset, spec.amplitude / 2, spec.frequency
    for octave in range(spec.octaves):
        total += amp * noise.sample(spec.algorithm, x * freq + octave * 17.31, z * freq - octave * 9.73)
        amp *= spec.gain
        freq *= spec.lacunarity
    return total


# Geometry helpers ---------------------------------------------------------------


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def point_in_polygon(x: float, z: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]
        xj, zj = poly[j]
        if (zi > z) != (zj > z) and x < (xj - xi) * (z - zi) / (zj - zi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def segment_distance(x: float, z: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    """Distance from (x, z) to segment a–b, and the position along it (0..1)."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dz * dz
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (z - a[1]) * dz) / length2))
    px, pz = a[0] + t * dx, a[1] + t * dz
    return math.hypot(x - px, z - pz), t


def distance_outside(x: float, z: float, area: Any) -> float:
    """0 inside the area, otherwise the distance to its edge."""
    if isinstance(area, Circle):
        return max(0.0, math.hypot(x - area.center[0], z - area.center[1]) - area.radius)
    poly = [tuple(p) for p in area]
    if point_in_polygon(x, z, poly):
        return 0.0
    return min(segment_distance(x, z, poly[i], poly[(i + 1) % len(poly)])[0] for i in range(len(poly)))


def catmull_rom(
    points: list[tuple[float, float, float]], closed: bool, step: float = 0.5
) -> list[tuple[float, float, float]]:
    """Samples a smooth curve through the points, about every `step` metres."""
    pts = list(points)
    if len(pts) < 2:
        return pts
    if closed:
        pts = [pts[-1]] + pts + pts[:2]
    else:
        pts = [pts[0]] + pts + [pts[-1]]
    out: list[tuple[float, float, float]] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        length = math.dist(p1, p2)
        n = max(1, int(length / step))
        for k in range(n):
            t = k / n
            t2, t3 = t * t, t * t * t
            out.append(
                tuple(
                    0.5
                    * (
                        2 * p1[c]
                        + (-p0[c] + p2[c]) * t
                        + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2
                        + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3
                    )
                    for c in range(3)
                )
            )
    out.append(tuple(pts[-2]))
    return out


def polyline(points: list[tuple[float, float, float]], closed: bool, smooth: bool) -> list[tuple[float, float, float]]:
    if smooth:
        return catmull_rom(points, closed)
    pts = list(points) + ([points[0]] if closed else [])
    out: list[tuple[float, float, float]] = []
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.dist(a, b) / 0.5))
        out += [tuple(a[c] + (b[c] - a[c]) * k / n for c in range(3)) for k in range(n)]
    out.append(tuple(pts[-1]))
    return out


# Heightfields -------------------------------------------------------------------


@dataclass
class Heightfield:
    """Heights on a regular grid in the terrain node's local space (origin at the centre)."""

    size: tuple[float, float]
    rows: int
    cols: int
    heights: list[float]
    layers: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def x_at(self, col: int) -> float:
        return -self.size[0] / 2 + self.size[0] * col / (self.cols - 1)

    def z_at(self, row: int) -> float:
        return -self.size[1] / 2 + self.size[1] * row / (self.rows - 1)

    def h(self, row: int, col: int) -> float:
        return self.heights[row * self.cols + col]

    def height(self, x: float, z: float) -> Optional[float]:
        """Bilinear height at local (x, z), or None outside the grid."""
        fx = (x + self.size[0] / 2) / self.size[0] * (self.cols - 1)
        fz = (z + self.size[1] / 2) / self.size[1] * (self.rows - 1)
        if fx < -1e-6 or fz < -1e-6 or fx > self.cols - 1 + 1e-6 or fz > self.rows - 1 + 1e-6:
            return None
        c0, r0 = min(int(fx), self.cols - 2), min(int(fz), self.rows - 2)
        tx, tz = fx - c0, fz - r0
        a = self.h(r0, c0) + (self.h(r0, c0 + 1) - self.h(r0, c0)) * tx
        b = self.h(r0 + 1, c0) + (self.h(r0 + 1, c0 + 1) - self.h(r0 + 1, c0)) * tx
        return a + (b - a) * tz

    def slope_deg(self, x: float, z: float) -> float:
        d = min(self.size[0] / (self.cols - 1), self.size[1] / (self.rows - 1))
        hx = (self.height(x + d, z) or 0.0) - (self.height(x - d, z) or 0.0)
        hz = (self.height(x, z + d) or 0.0) - (self.height(x, z - d) or 0.0)
        return math.degrees(math.atan(math.hypot(hx, hz) / (2 * d)))


def evaluate_terrain(
    node: Any,
    path_points: dict[str, list[tuple[float, float, float]]],
    zone_polygons: dict[str, list[tuple[float, float]]],
) -> Heightfield:
    """Heights and painted layers for a TerrainNode.

    path_points and zone_polygons are in the terrain's local space, keyed by node ID.
    """
    rows = cols = node.resolution
    hf = Heightfield(size=tuple(node.size), rows=rows, cols=cols, heights=[0.0] * (rows * cols))
    src = node.height
    noise = Noise2D(src.seed) if isinstance(src, HeightNoise) else None
    for r in range(rows):
        z = hf.z_at(r)
        for c in range(cols):
            x = hf.x_at(c)
            if isinstance(src, HeightFlat):
                value = src.y
            elif isinstance(src, HeightNoise):
                value = fbm(src, x, z, noise)
            elif isinstance(src, HeightGrid):
                gr = round(r / (rows - 1) * (src.rows - 1))
                gc = round(c / (cols - 1) * (src.cols - 1))
                value = src.data[gr * src.cols + gc]
            else:
                if isinstance(src, HeightImage):
                    hf.warnings.append("image heightmaps are not read yet; using flat ground")
                value = 0.0
            hf.heights[r * cols + c] = value

    for mod in node.modifiers:
        _apply_modifier(hf, mod, path_points)

    hf.layers = _paint_layers(hf, node.layers, zone_polygons)
    if node.holes:
        hf.warnings.append("terrain holes are not cut yet")
    hf.warnings = sorted(set(hf.warnings))
    return hf


def _apply_modifier(hf: Heightfield, mod: Any, path_points: dict[str, list[tuple[float, float, float]]]) -> None:
    rows, cols = hf.rows, hf.cols
    if isinstance(mod, ModFlatten):
        for r in range(rows):
            for c in range(cols):
                d = distance_outside(hf.x_at(c), hf.z_at(r), mod.area)
                i = r * cols + c
                if d == 0:
                    hf.heights[i] = mod.height
                elif mod.falloff > 0 and d < mod.falloff:
                    t = smoothstep(1 - d / mod.falloff)
                    hf.heights[i] += (mod.height - hf.heights[i]) * t
    elif isinstance(mod, ModBump):
        for r in range(rows):
            for c in range(cols):
                d = math.hypot(hf.x_at(c) - mod.center[0], hf.z_at(r) - mod.center[1]) / mod.radius
                if d >= 1:
                    continue
                if mod.shape == "cone":
                    k = 1 - d
                elif mod.shape == "plateau":
                    k = 1.0 if d < 0.6 else 1 - smoothstep((d - 0.6) / 0.4)
                else:
                    k = 1 - smoothstep(d)
                hf.heights[r * cols + c] += mod.amount * k
    elif isinstance(mod, ModSmooth):
        for _ in range(mod.iterations):
            src = hf.heights[:]
            for r in range(rows):
                for c in range(cols):
                    if mod.area is not None and distance_outside(hf.x_at(c), hf.z_at(r), mod.area) > 0:
                        continue
                    total, n = 0.0, 0
                    for rr in range(max(0, r - 1), min(rows, r + 2)):
                        for cc in range(max(0, c - 1), min(cols, c + 2)):
                            total += src[rr * cols + cc]
                            n += 1
                    hf.heights[r * cols + c] = total / n
    elif isinstance(mod, ModCarvePath):
        centre = path_points.get(mod.path)
        if not centre:
            hf.warnings.append(f"carve_path: path '{mod.path}' not found")
            return
        # Ground height along the path, smoothed so the carved strip has an even grade.
        ref = [hf.height(p[0], p[2]) for p in centre]
        ref = [v if v is not None else 0.0 for v in ref]
        window = 6
        ref = [
            sum(ref[max(0, i - window) : i + window + 1]) / len(ref[max(0, i - window) : i + window + 1])
            for i in range(len(ref))
        ]
        reach = mod.width / 2 + mod.falloff
        xs = [p[0] for p in centre]
        zs = [p[2] for p in centre]
        lo_x, hi_x, lo_z, hi_z = min(xs) - reach, max(xs) + reach, min(zs) - reach, max(zs) + reach
        segments = list(zip(centre, centre[1:]))
        for r in range(rows):
            z = hf.z_at(r)
            if z < lo_z or z > hi_z:
                continue
            for c in range(cols):
                x = hf.x_at(c)
                if x < lo_x or x > hi_x:
                    continue
                best, target = 1e9, 0.0
                for k, (a, b) in enumerate(segments):
                    d, t = segment_distance(x, z, (a[0], a[2]), (b[0], b[2]))
                    if d < best:
                        best, target = d, ref[k] + (ref[k + 1] - ref[k]) * t
                if best > reach:
                    continue
                goal = target - mod.depth
                i = r * cols + c
                if best <= mod.width / 2:
                    hf.heights[i] = goal
                else:
                    hf.heights[i] += (goal - hf.heights[i]) * smoothstep(
                        1 - (best - mod.width / 2) / max(mod.falloff, 1e-6)
                    )
    elif isinstance(mod, ModTerrace):
        for r in range(rows):
            for c in range(cols):
                if mod.area is not None and distance_outside(hf.x_at(c), hf.z_at(r), mod.area) > 0:
                    continue
                i = r * cols + c
                level = hf.heights[i] / mod.step_height
                k = math.floor(level)
                f = level - k
                edge = mod.sharpness * 0.9
                f = 0.0 if f < edge else smoothstep((f - edge) / (1 - edge))
                hf.heights[i] = (k + f) * mod.step_height
    elif isinstance(mod, ModCrater):
        for r in range(rows):
            for c in range(cols):
                d = math.hypot(hf.x_at(c) - mod.center[0], hf.z_at(r) - mod.center[1]) / mod.radius
                if d < 1:
                    hf.heights[r * cols + c] -= mod.depth * (1 - d * d)
                rim = math.exp(-(((d - 1) / 0.25) ** 2))
                hf.heights[r * cols + c] += mod.rim_height * rim
    elif isinstance(mod, ModErosion):
        hf.warnings.append("erosion is not simulated yet")


def _paint_layers(hf: Heightfield, layers: list[Any], zones: dict[str, list[tuple[float, float]]]) -> list[int]:
    """Index of the last matching layer at every vertex (0 when none match)."""
    if not layers:
        return []
    out = [0] * (hf.rows * hf.cols)
    for r in range(hf.rows):
        z = hf.z_at(r)
        for c in range(hf.cols):
            x = hf.x_at(c)
            h = hf.h(r, c)
            slope = None
            chosen = 0
            for k, layer in enumerate(layers):
                rule = layer.rule
                if rule.height_min is not None and h < rule.height_min:
                    continue
                if rule.height_max is not None and h > rule.height_max:
                    continue
                if rule.slope_min_deg is not None or rule.slope_max_deg is not None:
                    if slope is None:
                        slope = hf.slope_deg(x, z)
                    if rule.slope_min_deg is not None and slope < rule.slope_min_deg:
                        continue
                    if rule.slope_max_deg is not None and slope > rule.slope_max_deg:
                        continue
                if rule.zone is not None:
                    poly = zones.get(rule.zone)
                    if not poly or not point_in_polygon(x, z, poly):
                        continue
                chosen = k
            out[r * hf.cols + c] = chosen
    return out
