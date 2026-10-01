"""Measures GLB files and prints AssetDef 'dims' for them, so sizes come from the models, not guesses.

    python scripts/measure_assets.py viewer/assets/kenney/furniture --scale 1.9

--scale converts the kit's units to metres. Kenney's Furniture Kit is about half
real size: its doorway is 1.01 units tall, so 1.9 makes it 1.92 m. The loader
fits each model to exactly these dims, so keep one scale per kit to avoid
distorting proportions. Needs trimesh: pip install trimesh.
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
    args = parser.parse_args()
    result = {p.stem: measure(p, args.scale) for p in sorted(args.folder.glob("*.glb"))}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
