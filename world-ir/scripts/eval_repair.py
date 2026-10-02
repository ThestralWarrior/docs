"""Scores a repair policy on injected tasks, with the validators as the judge.

python scripts/eval_repair.py examples/*.json --count 40 --policy greedy
python scripts/eval_repair.py --tasks tasks.jsonl --policy oracle

Policies: noop (does nothing), oracle (the injector's own undo), greedy (the
validators' suggested fixes). A trained model gets plugged in the same way.
"""

import argparse
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir import World  # noqa: E402
from world_ir.baseline import greedy_repair  # noqa: E402
from world_ir.inject import Task, make_tasks  # noqa: E402
from world_ir.actions import apply_v1  # noqa: E402
from world_ir.reward import score_repair  # noqa: E402
from world_ir.validate import validate  # noqa: E402

POLICIES = {
    "noop": lambda task: [],
    "oracle": lambda task: [a for bug in task.bugs for a in bug.oracle],
    "greedy": lambda task: greedy_repair(task.world)[1],
}


def evaluate(tasks: list[Task], policy: str) -> dict:
    rows = []
    for task in tasks:
        actions = POLICIES[policy](task)
        before = validate(task.world)
        score = score_repair(task.world, actions, before)
        kept = 0
        if task.decoys and score.valid:
            after = validate(apply_v1(task.world, actions))
            still_odd = {i.node for i in after.issues if i.waived_by}
            kept = sum(1 for d in task.decoys if d in still_odd)
        rows.append(
            {
                "reward": score.reward,
                "fix_rate": score.fixed / max(1, score.errors_before),
                "new": score.new,
                "clean": score.clean,
                "decoys": len(task.decoys),
                "decoys_kept": kept if task.decoys and score.valid else 0,
                "undid": score.undid_deliberate,
            }
        )
    decoys = sum(r["decoys"] for r in rows)
    return {
        "policy": policy,
        "tasks": len(rows),
        "mean_reward": round(statistics.mean(r["reward"] for r in rows), 3),
        "errors_fixed": f"{statistics.mean(r['fix_rate'] for r in rows):.0%}",
        "worlds_clean": f"{sum(r['clean'] for r in rows) / len(rows):.0%}",
        "new_errors_per_task": round(statistics.mean(r["new"] for r in rows), 2),
        "decoys_kept_odd": f"{sum(r['decoys_kept'] for r in rows)}/{decoys}",
        "deliberate_undone": sum(r["undid"] for r in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("worlds", nargs="*", type=pathlib.Path)
    parser.add_argument("--tasks", type=pathlib.Path, help="A tasks.jsonl file from make_tasks.py.")
    parser.add_argument("--count", type=int, default=30, help="Tasks per world, when making them here.")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--policy", choices=sorted(POLICIES), action="append")
    args = parser.parse_args()
    if args.tasks:
        tasks = [Task.model_validate(json.loads(line)) for line in args.tasks.read_text().splitlines()]
    else:
        tasks = [
            t for p in args.worlds for t in make_tasks(World.model_validate_json(p.read_text()), args.count, args.seed)
        ]
    for policy in args.policy or ["noop", "greedy", "oracle"]:
        print(json.dumps(evaluate(tasks, policy)))


if __name__ == "__main__":
    main()
