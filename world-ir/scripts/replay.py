"""Records repair runs for the viewer to play back.

python scripts/replay.py --demo                      # the demo replays in viewer/replays/
python scripts/replay.py broken.json -o out.json     # any world with issues

A replay holds the starting scene and one small scene patch per repair step.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.replay import make_replay  # noqa: E402


def find(nodes: list, node_id: str) -> dict:
    def search(level: list):
        for n in level:
            if n["id"] == node_id:
                return n
            hit = search(n.get("children", []))
            if hit:
                return hit
        return None

    found = search(nodes)
    if found is None:
        raise KeyError(node_id)
    return found


def nudge(data: dict, node_id: str, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, **xform) -> None:
    xf = find(data["nodes"], node_id).setdefault("xform", {})
    x, y, z = xf.get("pos", [0.0, 0.0, 0.0])
    xf["pos"] = [round(x + dx, 4), round(y + dy, 4), round(z + dz, 4)]
    xf.update(xform)


def demo_worlds() -> dict[str, tuple[str, str, World]]:
    def load(name: str) -> dict:
        return json.loads((ROOT / "examples" / f"{name}.json").read_text())

    bedroom = load("bedroom")
    nudge(bedroom, "reading_lamp", dy=0.15)
    nudge(bedroom, "desk", dx=-0.25)
    find(bedroom["nodes"], "desk_chair")["xform"]["yaw"] += 70
    nudge(bedroom, "bed", dy=-0.1)
    nudge(bedroom, "cabinet", dx=0.9)

    horror = load("horror_room")
    nudge(horror, "ceiling_chair", dy=-0.3)
    nudge(horror, "fallen_box", dx=1.0, dz=-1.0)

    colony = load("scifi_colony")
    nudge(colony, "speeder", dx=5.0)
    nudge(colony, "crew_2", dy=-0.4)
    nudge(colony, "barrels", dy=0.6)
    return {
        "bedroom_repair": ("Bedroom: five planted mistakes", "bedroom", World.model_validate(bedroom)),
        "horror_repair": (
            "Horror room: fix the mistakes, keep the horror",
            "horror_room",
            World.model_validate(horror),
        ),
        "colony_repair": ("Sci-fi colony: three planted mistakes", "scifi_colony", World.model_validate(colony)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("world", nargs="?", type=pathlib.Path)
    parser.add_argument("-o", "--out", type=pathlib.Path)
    parser.add_argument("--demo", action="store_true", help="Write the demo replays to viewer/replays/.")
    args = parser.parse_args()
    if args.demo:
        out_dir = ROOT / "viewer" / "replays"
        out_dir.mkdir(exist_ok=True)
        index = []
        for name, (title, scene, world) in demo_worlds().items():
            replay = make_replay(world, title)
            (out_dir / f"{name}.json").write_text(replay.model_dump_json(exclude_none=True) + "\n")
            index.append({"name": name, "title": title, "scene": scene, "steps": len(replay.steps)})
            print(
                f"{name}: {len(replay.steps)} steps, score {replay.start_score} → {replay.steps[-1].score if replay.steps else replay.start_score}"
            )
        (out_dir / "index.json").write_text(json.dumps(index, indent=1) + "\n")
        return
    if not args.world or not args.out:
        parser.error("give a world and --out, or --demo")
    world = World.model_validate_json(args.world.read_text())
    replay = make_replay(world, world.name or world.id)
    args.out.write_text(replay.model_dump_json(exclude_none=True) + "\n")
    print(f"{args.out}: {len(replay.steps)} steps")


if __name__ == "__main__":
    main()
