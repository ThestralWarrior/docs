"""Scene patches: the difference between two lowered scenes, so the viewer can update in place.

After an edit or a repair the world is lowered again, but only a few items change.
A patch lists just those: items added, removed or changed, and any new or changed
materials and assets. The viewer animates it (things move, grow in, shrink out,
change colour) instead of reloading. Environment and behaviour changes are rare
and touch everything, so for those the patch asks for a reload.
"""

from typing import Any, Literal, Optional

from pydantic import Field

from .common import IRModel, Vec3, cfg
from .lowered import Item, LAsset, LBehavior, LMaterial, LoweredScene


class ScenePatch(IRModel):
    """What changed between two lowered scenes of the same world."""

    model_config = cfg("v1")

    format: Literal["patch-1"] = "patch-1"
    added: list[Item] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list, description="IDs of items that are gone.")
    changed: list[Item] = Field(default_factory=list, description="Items with the same ID and new contents.")
    materials: dict[str, LMaterial] = Field(default_factory=dict, description="New or changed materials.")
    assets: dict[str, LAsset] = Field(default_factory=dict, description="New or changed assets.")
    bounds: Optional[tuple[Vec3, Vec3]] = None
    behaviors: Optional[list[LBehavior]] = Field(None, description="Set when behaviours changed.")
    reload: bool = Field(False, description="True when the environment changed: the viewer should reload.")

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed or self.materials or self.assets or self.reload)


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json", exclude_none=True)


def diff_scenes(before: LoweredScene, after: LoweredScene) -> ScenePatch:
    """The patch that turns `before` into `after`."""
    old = {i.id: i for i in before.items}
    new = {i.id: i for i in after.items}
    added = [new[k] for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [new[k] for k in new if k in old and _dump(new[k]) != _dump(old[k])]
    materials = {
        k: m for k, m in after.materials.items() if k not in before.materials or _dump(m) != _dump(before.materials[k])
    }
    assets = {k: a for k, a in after.assets.items() if k not in before.assets or _dump(a) != _dump(before.assets[k])}
    behaviors = after.behaviors if [_dump(b) for b in after.behaviors] != [_dump(b) for b in before.behaviors] else None
    return ScenePatch(
        added=added,
        removed=removed,
        changed=changed,
        materials=materials,
        assets=assets,
        bounds=after.bounds,
        behaviors=behaviors,
        reload=_dump(before.environment) != _dump(after.environment),
    )


def apply_patch(scene: LoweredScene, patch: ScenePatch) -> LoweredScene:
    """The scene after a patch. Changed items keep their place; added ones go at the end."""
    changed = {i.id: i for i in patch.changed}
    gone = set(patch.removed)
    items = [changed.get(i.id, i) for i in scene.items if i.id not in gone] + list(patch.added)
    return scene.model_copy(
        update={
            "items": items,
            "materials": {**scene.materials, **patch.materials},
            "assets": {**scene.assets, **patch.assets},
            "bounds": patch.bounds,
            "behaviors": patch.behaviors if patch.behaviors is not None else scene.behaviors,
        }
    )
