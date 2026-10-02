"""Plants bugs in clean worlds and writes repair tasks, one JSON object per line.

python scripts/make_tasks.py examples/bedroom.json examples/horror_room.json --count 200 --out tasks.jsonl

Each line has the broken world, the planted bugs with their oracle fixes, any decoys,
and the prompt a repair model would read.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.inject import make_tasks  # noqa: E402
from world_ir.repair_text import repair_prompt  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worlds", nargs="+", type=pathlib.Path)
    parser.add_argument("--count", type=int, default=50, help="Tasks per world.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    written = 0
    with args.out.open("w") as f:
        for path in args.worlds:
            world = World.model_validate_json(path.read_text())
            for task in make_tasks(world, args.count, seed=args.seed):
                record = json.loads(task.model_dump_json(by_alias=True, exclude_none=True))
                record["prompt"] = repair_prompt(task.world)
                f.write(json.dumps(record) + "\n")
                written += 1
    print(f"wrote {written} tasks to {args.out}")


if __name__ == "__main__":
    main()
