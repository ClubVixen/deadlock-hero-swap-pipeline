# Deadlock hero swap pipeline

Scripted Blender steps for taking a Source 1 (SFM) character model and fitting it onto a
Deadlock hero's skeleton, ready for ModelDoc. Every step runs headless, so a new character
only needs a new config file.

No game files or third-party model assets are included. Bring your own model, and only use
models you made or have permission to use.

## Tools

- Blender 5.2
- [Blender Source Tools](https://github.com/Artfunkel/BlenderSourceTools) (DMX import/export)
- [SourceIO](https://github.com/REDxEYE/SourceIO) (imports compiled `.mdl` with flexes as shape keys)
- [Source 2 Viewer CLI](https://github.com/ValveResourceFormat/ValveResourceFormat/releases) (extracts the hero from `pak01_dir.vpk`)
- Reduced CSDK 12 and CS2 Workshop Tools (ModelDoc compile and AnimGraph2 linking)

Blender Source Tools needs a one-line fix on Blender 5, because new driver F-curves no longer
come with a default modifier. In `io_scene_valvesource/flex.py`, `addDrivers`, change
`fcurve.modifiers.remove(fcurve.modifiers[0])` to
`if len(fcurve.modifiers): fcurve.modifiers.remove(fcurve.modifiers[0])`.

## Steps

Copy `config/example_hero_swap.json` and fill it in. Then:

1. **Extract the hero.**
   `Source2Viewer-CLI -i <Deadlock>/game/citadel/pak01_dir.vpk -f models/heroes_wip/<hero>/<hero>.vmdl_c -d -o work/hero_src`
   This writes the `.vmdl` and one DMX per hero mesh, such as `<hero>_<hero>_body.dmx`.

2. **Import the character.**
   `blender -b --python blender/01_import_sfm.py -- <config.json> work/01_import.blend`
   This imports the compiled `.mdl` through SourceIO, so studiomdl's bodygroup placement is kept
   and flexes come in as shape keys. It keeps only the parts listed in the config, swaps or adds
   source DMX parts, and bone-merges the outfit onto the character skeleton.

3. **Fit to the hero.**
   `blender -b work/01_import.blend --python blender/02_fit_to_hero.py -- <config.json> config/bonemap_va_anthro_to_deadlock.json work/hero_src/.../<hero>_<hero>_body.dmx work/02_fit.blend`
   This applies the outfit skin, turns the character to face +X and scales it to the hero's
   height. Each mapped bone segment is then moved onto the matching hero segment, which takes
   the character from T-pose into the hero's bind pose; every shape key is moved the same way.
   Finally the vertex groups are renamed to hero bones, limited to 4 weights, and the meshes
   are parented to the hero skeleton. The hero's own bones are never changed.

4. **Export the DMX.**
   `blender -b work/02_fit.blend --python blender/03_export_dmx.py -- <config.json> <CSDK>/content/citadel_addons/<addon>/models/heroes_wip/<hero>/dmx`
   This writes `<hero>_body.dmx` as Binary 9, Model 22 (ModelDoc), with shape keys as flexes
   and without `_cloth` bones.

5. **Finish in ModelDoc.** Follow the divineskins hero swap guide, parts 2 to 4: swap the
   render mesh, rebuild bodygroups, materials, ragdoll and LODs, then link AnimGraph2 in CS2
   Workshop Tools and pack the VPK.

## Bone maps

`config/bonemap_va_anthro_to_deadlock.json` maps the ReVAmped/VA anthro rig onto the Deadlock
hero skeleton. For another source rig, write a new map:

- `segments` are joint pairs that drive the fit.
- `terminals` are end bones that only move and turn.
- `groups` renames vertex groups, with `fnmatch` patterns. `{Side}` and `{S}` expand to Left/L and Right/R.
