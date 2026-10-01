"""Sets metallicFactor to 0 on GLB materials that aren't metal.

    python scripts/fix_glb_metalness.py viewer/assets/kenney/nature

Some converted low-poly kits store metallicFactor 1 on every material (glTF's
default when the field is missing is also 1). Leaves, grass and fabric then
render black unless the scene has an environment map. This rewrites the GLB's
JSON chunk in place and leaves the binary chunk untouched. Materials whose name
contains 'metal' are kept as they are.
"""

import argparse
import json
import pathlib
import struct


def fix(path: pathlib.Path) -> list[str]:
    data = path.read_bytes()
    magic, version, _ = struct.unpack("<4sII", data[:12])
    if magic != b"glTF":
        raise ValueError(f"{path} is not a GLB file")
    json_length, json_type = struct.unpack("<I4s", data[12:20])
    if json_type != b"JSON":
        raise ValueError(f"{path}: first chunk is not JSON")
    doc = json.loads(data[20 : 20 + json_length])
    rest = data[20 + json_length :]
    changed = []
    for material in doc.get("materials", []):
        name = material.get("name", "")
        pbr = material.setdefault("pbrMetallicRoughness", {})
        if "metal" in name.lower() or "metallicRoughnessTexture" in pbr:
            continue
        if pbr.get("metallicFactor", 1.0) > 0:
            pbr["metallicFactor"] = 0.0
            changed.append(name)
    if not changed:
        return []
    chunk = json.dumps(doc, separators=(",", ":")).encode()
    chunk += b" " * (-len(chunk) % 4)  # JSON chunks are padded with spaces to 4 bytes
    out = struct.pack("<4sII", b"glTF", version, 12 + 8 + len(chunk) + len(rest))
    out += struct.pack("<I4s", len(chunk), b"JSON") + chunk + rest
    path.write_bytes(out)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=pathlib.Path)
    args = parser.parse_args()
    for path in sorted(args.folder.glob("*.glb")):
        changed = fix(path)
        if changed:
            print(f"{path.name}: {', '.join(changed)}")


if __name__ == "__main__":
    main()
