"""Generates clean furnished worlds and reports how varied they are.

    python scripts/gen_worlds.py --count 300 --out generated/
    python scripts/gen_worlds.py --count 50 --rooms kitchen,bathroom --suites 0 --out kitchens/
    python scripts/gen_worlds.py --count 2000 --stats-only

Each world validates clean (no errors, warnings or questions) and is written as
<world id>.json. --suites is the share of multi-room flats. Then feed them to
make_tasks.py to plant bugs:

    python scripts/make_tasks.py generated/*.json --count 20 --out tasks.jsonl
"""

import argparse
import collections
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from world_ir.furniture import library  # noqa: E402
from world_ir.nodes import AssetNode, RoomNode  # noqa: E402
from world_ir.worldgen import ROOM_KINDS, generate, generate_suite  # noqa: E402


def stats(results: list, seconds: float) -> str:
    rooms, shapes, themes, sources, allows = (collections.Counter() for _ in range(5))
    pieces, categories, relations = collections.Counter(), collections.Counter(), collections.Counter()
    objects, doors, windows, areas, placed = [], [], [], [], 0
    prompts = set()
    for r in results:
        w = r.world
        prompts.add(w.brief.prompt)
        n = 0
        for node, _ in w.walk():
            if isinstance(node, RoomNode):
                rooms[node.room_type] += 1
                shapes["L-shaped" if len(node.outline) > 4 else "rectangle"] += 1
                doors.append(sum(o.type in ("door", "archway") for o in node.openings))
                windows.append(sum(o.type == "window" for o in node.openings))
                xs, zs = [p[0] for p in node.outline], [p[1] for p in node.outline]
                areas.append((max(xs) - min(xs)) * (max(zs) - min(zs)))
                if node.xform.yaw or any(node.xform.pos):
                    placed += 1
            if isinstance(node, AssetNode):
                n += 1
                pieces[w.assets[node.asset].extras["model"]] += 1
                categories[node.semantic.category if node.semantic else w.assets[node.asset].category] += 1
            for intent in node.intent:
                sources[intent.source] += 1
                allows.update(intent.allows)
        objects.append(n)
        for rel in w.relations:
            relations[rel.rel] += 1
        themes[
            next(
                (
                    m
                    for m in w.brief.mood
                    if m
                    in (
                        "abandoned",
                        "haunted",
                        "horror",
                        "creepy",
                        "messy",
                        "cluttered",
                        "surreal",
                        "dreamlike",
                        "zero_gravity",
                    )
                ),
                "none",
            )
        ] += 1
    flats = sum(1 for r in results if len(r.world.nodes) > 1)
    lines = [
        f"{len(results)} clean worlds in {seconds:.1f} s ({seconds / max(1, len(results)) * 1000:.0f} ms each); "
        f"{flats} multi-room flats; {sum(r.attempts > 1 for r in results)} needed a redraw; "
        f"{sum(len(r.dropped) for r in results)} pieces or relations dropped by validation",
        f"rooms: {sum(rooms.values())} across {len(rooms)} types: "
        + ", ".join(f"{k} {v}" for k, v in rooms.most_common()),
        f"shapes: {dict(shapes)}; moved or turned off the origin: {placed}; floor area {min(areas):.1f}–{max(areas):.1f} m²",
        f"openings per room: doors {min(doors)}–{max(doors)}, windows {min(windows)}–{max(windows)}",
        f"objects per world: {min(objects)}–{max(objects)}, mean {statistics.mean(objects):.1f}; "
        f"{len(pieces)} of {len(library())} furniture models used, {len(categories)} categories",
        f"relations: {sum(relations.values())} of {len(relations)} kinds: "
        + ", ".join(f"{k} {v}" for k, v in relations.most_common()),
        "themes: " + ", ".join(f"{k} {v}" for k, v in themes.most_common()),
        f"deliberate oddities: {sum(sources.values())} intents ({dict(sources)}), allowing {dict(allows)}",
        f"distinct prompts: {len(prompts)}",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=0, help="First seed; world i uses seed + i.")
    parser.add_argument("--rooms", help=f"Comma-separated room types. Default: all of {', '.join(ROOM_KINDS)}.")
    parser.add_argument("--suites", type=float, default=0.2, help="Share of multi-room flats, 0 to 1.")
    parser.add_argument("--theme", help="Force one theme (licence) on every world, e.g. haunted.")
    parser.add_argument("--out", type=pathlib.Path, help="Folder for the world files.")
    parser.add_argument("--stats-only", action="store_true", help="Generate and report, write nothing.")
    args = parser.parse_args()
    if not args.out and not args.stats_only:
        parser.error("give --out, or --stats-only")
    kinds = args.rooms.split(",") if args.rooms else ROOM_KINDS
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
    results, t0 = [], time.time()
    for i in range(args.count):
        seed = args.seed + i
        if args.suites and (seed * 2654435761 % 1000) / 1000 < args.suites:
            r = generate_suite(seed, theme=args.theme)
        else:
            r = generate(seed, kinds[i % len(kinds)], theme=args.theme)
        results.append(r)
        if args.out:
            data = json.loads(r.world.model_dump_json(by_alias=True, exclude_none=True))
            (args.out / f"{r.world.id}.json").write_text(json.dumps(data) + "\n")
    print(stats(results, time.time() - t0))
    if args.out:
        print(f"wrote {len(results)} worlds to {args.out}/")


if __name__ == "__main__":
    main()
