# World IR

The single source of truth for a generated 3D world. A world file is JSON, but its contents are a typed IR (intermediate representation): a scene tree of typed nodes, registries of assets and materials, terrain, parametric generators, declared relations, and checking rules. Three.js rendering, validators, the repair model's input text and the repair actions are all passes over this one file.

The full field-by-field reference is in [`docs/REFERENCE.md`](docs/REFERENCE.md). It is generated from the code, so it always matches the schema.

## The pipeline

```
Builder agent → World IR (agent writes nodes and relations; code fills asset and material entries)
             → Lowering (Python, world_ir/lower.py)  → Lowered scene ← validators read this
             → Loader (JavaScript, viewer/src/loader.js, three.js 0.186.1 pinned) → what you see
```

The repair model edits the World IR with actions; the result is lowered again and the viewer animates the change.

- **Lowering** turns rooms into a floor, a ceiling, wall pieces cut around doors and windows, door panels, window glass and door clearance zones; evaluates terrain (seeded noise, flatten, bump, smooth, carve-path, terrace and crater edits, material layers by height, slope and zone, a water level); lays paths on the ground; expands prefabs with per-instance overrides; scatters assets with area, spacing, slope, height and avoid rules; turns 22 generators into shapes, meshes and instances (platform, building with windows, door and roof, roof, column, ramp, railing, fence, procedural tree, rock and bush, grass, flower bed, along-path, radial array, table set, shelving, kitchen run, rug, straight stairs, wall run, array); fills in material presets; and flattens nested transforms into world matrices. Water bodies, decals, text, audio, particles and a few generators (arch, bridge, road, parking lot, curtains, shelf fill, non-straight stairs) are listed in `unsupported` and skipped, while their children are still lowered.
- **The loader** only draws what lowering produced. It normalises each GLB (front to +Z, bottom centre to the origin, fitted to the asset's `dims`), tags every object with its IR node ID, highlights nodes, animates updates between two lowered scenes, and cuts away walls between the camera and the room.
- **Versions** are pinned and recorded in every lowered scene: IR version, lowering version, and the three.js version. Three.js does not follow semver (r186 removed `PCFSoftShadowMap`, for example), so upgrade only on purpose.

![Bedroom example rendered by the loader](docs/bedroom.png)

![Cabin clearing example: terrain, pond, trail, cabin on a deck, scattered forest](docs/cabin_overview.png)

![Cabin clearing example from the campsite](docs/cabin_hero.png)

**Snapping.** An agent can say an object stands on the terrain but cannot know the ground height there. `snap_to_ground` (and `scripts/snap.py`) sets the height of every terrain-supported node from the terrain, the same way code fills in asset sizes. Scatter, fences, flower beds and paths follow the ground on their own.

## What a world file can contain

| Part | Count | Examples |
|---|---|---|
| Node kinds | 18 | group, asset, primitive, generator, prefab, room, terrain, water, path, scatter, zone, light, camera, marker, decal, text, audio, particles |
| Generators | 27 | stairs, ramp, roof, wall run, column, arch, fence, railing, bridge, building, platform, shelving, table set, kitchen run, shelf fill, rug, curtains, tree, rock, bush, grass, flower bed, road, along-path, parking lot, array, radial array |
| Terrain | 4 height sources, 7 edits, painted layers | noise, grid, image, flat; flatten, bump, smooth, carve path, terrace, crater, erosion |
| Relations | 46 in 7 groups | on, against wall, faces, near, around, clear, not blocking, walkway, count, requires, no overlap, max slope |
| Environment | sky (4 types), sun, ambient, fog (2 types), tone mapping, weather, wind | |
| Registries | assets, materials (27 presets), prefabs | |
| Repair actions | 12 in 3 tiers | move, rotate, scale; set support, reparent, swap asset, set material, set param, add node, remove node; add or remove relation |

In total the world format has 155 object types and 685 documented fields; with the lowered scene format, 169 and 765.

Every object and field carries a tier:

- **v1**: needed for the hackathon build: rooms, assets, lights, cameras, zones, the core relations and the v1 repair actions.
- **v2**: stretch: terrain, most generators, scatter, paths, prefabs, physics.
- **later**: designed so the format won't need to change, but not planned: water, audio, particles, decals, weather, behaviours.

The world can describe much more than the repair model may change. The v1 repair model only edits `xform.pos`, `xform.yaw` and `xform.scale`.

## Conventions

- Metres, degrees, kilograms. +Y up, right-handed. An asset's front faces +Z (glTF).
- An asset's origin is the bottom centre of its box, so `pos[1]` is the height of its base.
- Transforms are local to the parent node: a lamp on a nightstand moves with it.
- IDs are lowercase slugs, unique across the file. Walls are `"<room_id>:<edge>"`.
- Unknown fields are rejected. Tool-specific data goes in `extras`.

## Layout

```
world_ir/        Pydantic models: the schema itself
  common.py        shared types, Transform, Support, Semantic, Physics, Provenance
  environment.py   sky, sun, ambient, fog, weather, wind
  assets.py        AssetDef, Material, material presets
  architecture.py  Opening (doors, windows), RoofSpec
  terrain.py       height sources, terrain edits, material layers
  generators.py    the 27 parametric generators
  nodes.py         the 18 node kinds
  relations.py     the 46 relations
  world.py         World, Brief, ValidationRules, Prefab, cross-reference checks
  actions.py       repair actions, constrained_schema(), apply_v1()
  catalogue.py     reads the models for the generated docs
  lowered.py       the lowered scene format
  lower.py         lowering: World IR → lowered scene (rooms, terrain, paths, prefabs, scatter)
  lower_generators.py  generators → shapes, meshes and instances
  terrain_eval.py  noise, terrain edits, layers, ground height
  geometry.py      matrices, colour, roof and rock meshes
  snap.py          put terrain-supported nodes on the ground
examples/        minimal.json, bedroom.json, cabin_clearing.json
viewer/          three.js loader (src/loader.js), demo page, lowered scenes, Kenney CC0 furniture and nature models
schema/          generated JSON Schemas for worlds, actions and lowered scenes
docs/            generated REFERENCE.md and catalogue.json
tests/           validation, cross-reference and action tests
```

## Use

```bash
pip install -e ".[dev]"
pytest                                                    # schema, lowering and action tests
python scripts/build_docs.py                              # regenerate schema/ and docs/ after changing world_ir/
python scripts/lower.py examples/*.json --out-dir viewer/scenes   # regenerate the scenes the viewer loads
python scripts/measure_assets.py viewer/assets/kenney/furniture --scale 1.9   # asset dims from the GLBs (needs trimesh)
python scripts/measure_assets.py viewer/assets/kenney/nature --height tree-pinetalla=9   # or a real height per model
python scripts/fix_glb_metalness.py viewer/assets/kenney/nature   # some kits mark leaves and fabric as metal
python scripts/snap.py examples/cabin_clearing.json                # ground heights for terrain-supported nodes

cd viewer && npm install && npm run serve                 # then open http://localhost:8000/?scene=bedroom
```

```python
from world_ir import World, apply_v1, constrained_schema

world = World.model_validate_json(open("examples/bedroom.json").read())

# JSON Schema for one repair answer on this world. Object IDs are an enum of the
# world's unlocked nodes, so a model decoding with it cannot name a missing object.
schema = constrained_schema(world, tier="v1")

fixed = apply_v1(world, [{"action": "move", "id": "desk_chair", "to": [0.9, 0, 2.4]}])
```

Loading a world checks more than field types: every reference to a node, asset, material, prefab, opening, zone, path or wall must resolve, node IDs must be unique, openings must fit their walls, and nodes may use at most one way of giving a rotation.

## What does not go in the world file

Keep these in separate files that point at the world by ID:

- Validator results (the issue list).
- The history of actions applied, with before and after.
- Training labels, such as which bug the injector planted. The world only marks injected nodes with `source.by = "injector"`.
- Renders and screenshots.

## Adding to the IR

1. Add a model with a literal tag (`kind`, `gen`, `rel` or `action`), a docstring and a description on every field. Set its tier with `model_config = cfg("v2")`.
2. Add it to the union and to the group list at the bottom of its module.
3. Mark fields that name other objects with `json_schema_extra=ref("node")` (or `asset`, `material`, ...); the world then checks them on load.
4. Run `python scripts/build_docs.py` and `pytest`. A test fails if the generated schema is stale.
5. Implement it where it is used: the Three.js generator, the validators, or both. A kind that no pass understands should stay at tier `later`.
