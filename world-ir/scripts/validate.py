"""Checks world files and prints what the repair model would read.

python scripts/validate.py examples/*.json
python scripts/validate.py examples/bedroom.json --hints     # include suggested fixes
python scripts/validate.py examples/bedroom.json --json      # the full report
python scripts/validate.py examples/bedroom.json --repair    # apply suggested fixes, print the actions

Exits with status 1 when any world has errors.
"""

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.baseline import greedy_repair  # noqa: E402
from world_ir.validate import validate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worlds", nargs="+", type=pathlib.Path)
    parser.add_argument("--hints", action="store_true", help="Show suggested fixes.")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")
    parser.add_argument("--repair", action="store_true", help="Apply suggested fixes and show what changed.")
    args = parser.parse_args()
    failed = False
    for path in args.worlds:
        world = World.model_validate_json(path.read_text())
        if args.repair:
            world, actions, report = greedy_repair(world)
            print(f"{path}: applied {len(actions)} fixes")
            for action in actions:
                print("  ", json.dumps(action))
        else:
            report = validate(world)
        if args.json:
            print(report.model_dump_json(indent=2, exclude_none=True))
        else:
            print(report.as_text(hints=args.hints))
            if report.unchecked:
                print(f"Not checked yet: {', '.join(report.unchecked)}")
        print()
        failed = failed or not report.clean
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
