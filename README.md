# Deadlock hero swap pipeline

Tools for taking a Source 1 (SFM) character model and fitting it onto a Deadlock hero's
skeleton, ready for ModelDoc. Most of the work is done in Blender with the **Hero Swap Tools**
add-on. The same steps also come as headless scripts, for batch runs.

No game files or third-party model assets are included. Bring your own model, and only use
models you made or have permission to use.

## Setup

1. Install Blender 5.2.
2. Install these add-ons into `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons`:
   - [Blender Source Tools](https://github.com/Artfunkel/BlenderSourceTools), for DMX import and export
   - [SourceIO](https://github.com/REDxEYE/SourceIO), which imports compiled `.mdl` files with flexes as shape keys
   - `addon/hero_swap_tools` from this repo
3. Turn all three on in Edit > Preferences > Add-ons.
4. Get the [Source 2 Viewer CLI](https://github.com/ValveResourceFormat/ValveResourceFormat/releases),
   Reduced CSDK 12, and CS2 Workshop Tools. The last two are for the ModelDoc compile and the
   AnimGraph2 linking.

Blender Source Tools needs a one-line fix on Blender 5, because new driver F-curves no longer
come with a default modifier. In `io_scene_valvesource/flex.py`, `addDrivers`, change
`fcurve.modifiers.remove(fcurve.modifiers[0])` to
`if len(fcurve.modifiers): fcurve.modifiers.remove(fcurve.modifiers[0])`.

## Workflow in Blender

### 0. Get the files in

- **Hero:** extract the hero with Source 2 Viewer:
  `Source2Viewer-CLI -i <Deadlock>/game/citadel/pak01_dir.vpk -f models/heroes_wip/<hero>/<hero>.vmdl_c -d -o work/hero_src`
  This writes the `.vmdl` plus one DMX per hero mesh. Then use File > Import > Source Engine
  (Source Tools) on `<hero>_<hero>_body.dmx` to bring in the hero skeleton.
- **Character and outfit:** use File > Import > Source Engine assets > `.mdl` (SourceIO) on the
  compiled models. Their sliders come in as shape keys. Delete any bodygroups you don't want.

### 1. The Hero Swap panel

Press **N** in the 3D Viewport and open the **Hero Swap** tab. Every button can be undone with Ctrl+Z.

| Section | Setting / button | What it does |
|---|---|---|
| 1. Outfit | **Character Rig** | The character's armature. |
| | **Bone-merge Selected onto Character** | Moves the selected outfit meshes onto the Character Rig, matching bones by name, so the outfit follows the character's skeleton. Shape keys move with it. |
| | **Skin Swaps** + **Apply Skin Swaps** | Swaps materials on the selected meshes using a list of `old=new` names, for example `nmm_outfit_sleeves=nmm_outfit_sleeves_textured`. |
| | **Body Mesh** + **Match Outfit to Body Shape Keys** | Makes the selected outfit pieces follow the body's sliders (bust, hips, and so on). Outfit keys with the same name as a body key are driven by it. With **Create Missing Keys** on, body keys the outfit doesn't have are built from the nearest point on the body surface, then driven as well. **Max Distance** (0 = no limit) leaves loose parts, such as shoulder guards far from the skin, alone. |
| 2. Fit to hero | **Hero Skeleton** | The skeleton imported from the hero's body DMX. |
| | **Bone Map** | A JSON file mapping the character rig to the hero skeleton. It defaults to the VA anthro map that ships with the add-on. |
| | **Fit Character to Hero** | Turns the character to face +X, scales it to the hero's height, and poses each mapped bone segment onto the hero's (T-pose to the hero bind pose, joints lined up). Shape keys and drivers are kept. Then it renames the weights to hero bones, limits them to 4 per vertex, and parents everything to the hero skeleton. The hero's bones are never changed. |
| | **Keep Jiggle Bones** + **Jiggle Presets** | When this is on, the fit keeps chains that match `keep_patterns` in the presets file as real bones on the hero skeleton (hair, tail, chest, butt by default), instead of merging them into the head or pelvis. They hang off the nearest mapped hero bone and keep their weights. |
| | **Write Jiggle Bones for ModelDoc** | Writes a ModelDoc `JiggleBoneList` for the kept bones. Each bone gets the first preset whose pattern matches: flexible for hair and tail, base spring for chest and butt. The result goes into the `jiggle_bones.vmdl` text block and the clipboard, ready to paste into the model's `.vmdl` next to the other lists. Tune the values in ModelDoc. |
| 3. Export | **Export Collection** + **Export Folder** | Name the collection after the hero mesh it replaces, e.g. `unicorn_body`, and export to `<CSDK>/content/citadel_addons/<addon>/models/heroes_wip/<hero>/dmx`. |
| | **Export ModelDoc DMX** | Writes a Binary 9 / Model 22 (ModelDoc) DMX with Source Tools. Shape keys become flexes, and `_cloth` bones are removed. |

Suggested order: **Bone-merge**, then **Apply Skin Swaps**, then **Match Outfit to Body Shape
Keys**. Set the sliders the way you want them, then **Fit Character to Hero**, then **Export**.

### 2. Finish in ModelDoc

Follow the divineskins hero swap guide, parts 2 to 4:
1. Swap in the render mesh.
2. Rebuild the bodygroups, materials, ragdoll and LODs.
3. Link AnimGraph2 in CS2 Workshop Tools.
4. Pack the VPK into `game/citadel/addons/`.

## Headless scripts

The same steps without the GUI. They are driven by a config file; copy
`config/example_hero_swap.json` to start one.

```
blender -b --python blender/01_import_sfm.py -- <config.json> work/01_import.blend
blender -b work/01_import.blend --python blender/02_fit_to_hero.py -- <config.json> config/bonemap_va_anthro_to_deadlock.json <hero body dmx> work/02_fit.blend
blender -b work/02_fit.blend --python blender/03_export_dmx.py -- <config.json> <export folder>
```

`01_import_sfm.py` can also swap a compiled bodygroup for a source DMX (`replace_with`) or
add one the compiled model lacks (`add_dmx`).

## Bone maps

`bonemap_va_anthro_to_deadlock.json` maps the ReVAmped/VA anthro rig onto the Deadlock hero
skeleton. For another source rig, write a new map:

- `segments` are joint pairs that drive the fit.
- `terminals` are end bones that only move and turn.
- `groups` renames vertex groups, with `fnmatch` patterns. `{Side}` and `{S}` expand to Left/L and Right/R.

Parts that aren't mapped and aren't kept as jiggle bones, such as the wings, stay rigid on the
nearest mapped bone.

## Physics presets

`addon/hero_swap_tools/jiggle_presets.json` has two parts:
- `keep_patterns` lists which source bones survive the fit.
- `presets` holds the ModelDoc JiggleBone fields for each pattern. `"skip": true` keeps a bone without giving it jiggle, which is useful for a chain root such as `TailBase`.

The field names come from ModelDoc's JiggleBone node, and the starting values from the SFM
`.qci` jiggle files. ModelDoc runs jiggle bones through cloth, so check them with Simulate in
ModelDoc and adjust.
