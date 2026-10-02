"""Measures GLB files and prints AssetDef 'dims' for them, so sizes come from the models, not guesses.

    python scripts/measure_assets.py viewer/assets/kenney/furniture --scale 1.9

    python scripts/measure_assets.py viewer/assets/kenney/nature --height tree-pinetalla=9 --height tent-detailedopen=2.2

    python scripts/measure_assets.py viewer/assets/kenney/furniture --scale 1.9 --surfaces \\
        --out world_ir/data/kenney_furniture.json

--scale converts the kit's units to metres. Kenney's Furniture Kit is about half
real size: its doorway is 1.01 units tall, so 1.9 makes it 1.92 m. --height
instead picks a uniform scale per model from a real-world height, for kits whose
models aren't at one consistent scale (the Nature Kit). Either way each model is
scaled uniformly, so the loader fitting it to these dims never distorts it.

--surfaces also finds each model's top surface: rays cast straight down on a
13 × 13 grid over the footprint, and the highest height that at least a quarter
of them hit first. A table's top, a bed's mattress, a chair's seat, a counter
beside its sink. It records that height, the share of rays that hit it and the
usable area (in model space, origin at the bottom centre), and 'back_z': the
mean z of the model's upper third, which is negative when the back is at -z
(chairs, sofas, beds), so you can check the kit faces +z.
Needs trimesh and numpy: pip install trimesh.
"""

import argparse
import json
import pathlib

import numpy as np
import trimesh

GRID = 13


def _mesh(path: pathlib.Path) -> trimesh.Trimesh:
    scene = trimesh.load(path, force="scene")
    return scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)


def top_surface(vertices: np.ndarray, faces: np.ndarray, size: np.ndarray) -> dict | None:
    """The highest flat surface a quarter of downward rays hit first. Vertices are centred, base at y 0."""
    w, _, d = size
    xs = np.linspace(-w / 2, w / 2, GRID + 2)[1:-1]
    zs = np.linspace(-d / 2, d / 2, GRID + 2)[1:-1]
    points = np.array([(x, z) for x in xs for z in zs])
    tri = vertices[faces]
    ax, az, bx, bz, cx, cz = tri[:, 0, 0], tri[:, 0, 2], tri[:, 1, 0], tri[:, 1, 2], tri[:, 2, 0], tri[:, 2, 2]
    den = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
    keep = np.abs(den) > 1e-12
    tri, ax, az, bx, bz, cx, cz, den = (a[keep] for a in (tri, ax, az, bx, bz, cx, cz, den))
    first = np.full(len(points), np.nan)
    for i, (px, pz) in enumerate(points):
        l1 = ((bz - cz) * (px - cx) + (cx - bx) * (pz - cz)) / den
        l2 = ((cz - az) * (px - cx) + (ax - cx) * (pz - cz)) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            first[i] = (l1 * tri[:, 0, 1] + l2 * tri[:, 1, 1] + l3 * tri[:, 2, 1])[inside].max()
    hit = ~np.isnan(first)
    for height in sorted({round(float(y), 2) for y in first[hit]}, reverse=True):
        near = hit & (np.abs(np.nan_to_num(first) - height) <= 0.02)
        share = near.sum() / len(points)
        if share >= 0.25:
            p = points[near]
            step = (w / (GRID + 1), d / (GRID + 1))
            area = [p[:, 0].min() - step[0] / 2, p[:, 1].min() - step[1] / 2]
            area += [p[:, 0].max() + step[0] / 2, p[:, 1].max() + step[1] / 2]
            return {"height": height, "share": round(float(share), 2), "area": [round(float(a), 3) for a in area]}
    return None


def measure(path: pathlib.Path, scale: float, surfaces: bool = False) -> dict:
    scene = trimesh.load(path, force="scene")
    lo, hi = scene.bounds
    size = (hi - lo) * scale
    slots = sorted(
        {
            geometry.visual.material.name
            for geometry in scene.geometry.values()
            if getattr(geometry.visual, "material", None) is not None and geometry.visual.material.name
        }
    )
    out = {"dims": [round(float(v), 3) for v in size], "material_slots": slots}
    if surfaces:
        mesh = _mesh(path)
        centre = (lo + hi) / 2
        vertices = (np.asarray(mesh.vertices) - [centre[0], lo[1], centre[2]]) * scale
        out["top"] = top_surface(vertices, np.asarray(mesh.faces), size)
        upper = vertices[vertices[:, 1] > 0.7 * size[1]]
        out["back_z"] = round(float(upper[:, 2].mean()), 3) if len(upper) else None
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=pathlib.Path)
    parser.add_argument("--scale", type=float, default=1.0, help="Kit units to metres.")
    parser.add_argument(
        "--height", action="append", default=[], metavar="NAME=METRES", help="Real height of one model."
    )
    parser.add_argument("--surfaces", action="store_true", help="Also measure top surfaces and facing.")
    parser.add_argument("--out", type=pathlib.Path, help="Write the JSON here instead of printing it.")
    args = parser.parse_args()
    heights = {name: float(value) for name, value in (h.split("=", 1) for h in args.height)}
    result = {}
    for path in sorted(args.folder.glob("*.glb")):
        if heights and path.stem not in heights:
            continue
        scale = args.scale
        if path.stem in heights:
            scale = heights[path.stem] / measure(path, 1.0)["dims"][1]
        result[path.stem] = {**measure(path, scale, args.surfaces), "scale": round(scale, 4)}
    rows = [f" {json.dumps(name)}: {json.dumps(row, separators=(', ', ': '))}" for name, row in result.items()]
    text = "{\n" + ",\n".join(rows) + "\n}"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
