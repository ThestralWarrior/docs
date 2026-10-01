"""Lowers world files into scenes the Three.js loader reads.

python scripts/lower.py examples/bedroom.json -o viewer/scenes/bedroom.json
python scripts/lower.py examples/*.json --out-dir viewer/scenes
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.lower import lower  # noqa: E402


def scene_json(scene) -> str:
    """One line per top-level field and per item, so diffs show which items changed."""
    data = json.loads(scene.model_dump_json(exclude_none=True))
    parts = []
    for key, value in data.items():
        if key == "items":
            body = ",\n".join("  " + json.dumps(item, separators=(",", ":")) for item in value)
            parts.append(f' "items": [\n{body}\n ]')
        else:
            parts.append(f" {json.dumps(key)}: {json.dumps(value, separators=(',', ':'))}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worlds", nargs="+", type=pathlib.Path)
    parser.add_argument("-o", "--out", type=pathlib.Path, help="Output file, for a single world.")
    parser.add_argument("--out-dir", type=pathlib.Path, help="Output folder; files keep their names.")
    args = parser.parse_args()
    if args.out and len(args.worlds) > 1:
        parser.error("use --out-dir with more than one world")
    for path in args.worlds:
        world = World.model_validate_json(path.read_text())
        scene = lower(world)
        out = args.out or (args.out_dir or path.parent) / path.name.replace(".json", ".lowered.json")
        if args.out_dir:
            out = args.out_dir / path.name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(scene_json(scene))
        skipped = ", ".join(f"{u.node} ({u.kind})" for u in scene.unsupported)
        print(f"{path} → {out}: {len(scene.items)} items" + (f"; skipped {skipped}" if skipped else ""))


if __name__ == "__main__":
    main()
