"""Measures GLB files and prints AssetDef 'dims' for them, so sizes come from the models, not guesses.

    python scripts/measure_assets.py viewer/assets/kenney/furniture --scale 1.9

    python scripts/measure_assets.py viewer/assets/kenney/nature --height tree-pinetalla=9 --height tent-detailedopen=2.2

--scale converts the kit's units to metres. Kenney's Furniture Kit is about half
real size: its doorway is 1.01 units tall, so 1.9 makes it 1.92 m. --height
instead picks a uniform scale per model from a real-world height, for kits whose
models aren't at one consistent scale (the Nature Kit). Either way each model is
scaled uniformly, so the loader fitting it to these dims never distorts it.
Needs trimesh: pip install trimesh.
"""

import argparse
import json
import pathlib

import trimesh


def measure(path: pathlib.Path, scale: float) -> dict:
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
    return {"dims": [round(float(v), 3) for v in size], "material_slots": slots}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=pathlib.Path)
    parser.add_argument("--scale", type=float, default=1.0, help="Kit units to metres.")
    parser.add_argument(
        "--height", action="append", default=[], metavar="NAME=METRES", help="Real height of one model."
    )
    args = parser.parse_args()
    heights = {name: float(value) for name, value in (h.split("=", 1) for h in args.height)}
    result = {}
    for path in sorted(args.folder.glob("*.glb")):
        if heights and path.stem not in heights:
            continue
        scale = args.scale
        if path.stem in heights:
            scale = heights[path.stem] / measure(path, 1.0)["dims"][1]
        result[path.stem] = {**measure(path, scale), "scale": round(scale, 4)}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
