"""Sets the height of every terrain-supported node in a world file from its terrain.

python scripts/snap.py examples/cabin_clearing.json            # rewrite in place
python scripts/snap.py examples/cabin_clearing.json -o out.json
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.snap import snap_to_ground  # noqa: E402


def compact_json(data: object) -> str:
    """Indented JSON with short lists of numbers or strings kept on one line."""
    text = json.dumps(data, indent=2)
    join = lambda m: "[" + ", ".join(part.strip() for part in m.group(1).split(",")) + "]"  # noqa: E731
    number = r"-?[0-9.eE+-]+"
    text = re.sub(rf"\[\s+({number}(?:,\s+{number})*)\s+\]", join, text)
    word = r'"[^",\[\]{{}}]*"'
    return re.sub(rf"\[\s+({word}(?:,\s+{word})*)\s+\]", join, text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("world", type=pathlib.Path)
    parser.add_argument("-o", "--out", type=pathlib.Path)
    args = parser.parse_args()
    raw = json.loads(args.world.read_text())
    snapped, moved = snap_to_ground(World.model_validate(raw))
    # Write back only the changed heights, so the file keeps its hand-written layout.
    heights = {node.id: node.xform.pos[1] for node, _ in snapped.walk()}

    def patch(node: dict) -> None:
        if node.get("id") in heights:
            pos = node.setdefault("xform", {}).setdefault("pos", [0, 0, 0])
            if pos[1] != heights[node["id"]]:
                pos[1] = heights[node["id"]]
        for child in node.get("children", []):
            patch(child)

    for root in raw["nodes"]:
        patch(root)
    out = args.out or args.world
    out.write_text(compact_json(raw) + "\n")
    print(f"{args.world} → {out}: moved {len(moved)} nodes")
    for line in moved:
        print("  ", line)


if __name__ == "__main__":
    main()
