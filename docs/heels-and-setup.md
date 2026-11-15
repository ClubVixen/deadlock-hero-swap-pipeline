# Heels, jiggle presets and game setup

## Game paths and skeletons

Open **N > Hero Swap > Tool Paths...**, or **Edit > Preferences > Add-ons > Hero
Swap Tools**. These are add-on preferences, separate from the scene's rig pickers.
Save Preferences if automatic preference saving is disabled.

| Field | Select | Expected contents |
|---|---|---|
| Deadlock | Steam's Deadlock installation root | `game/citadel/pak01_dir.vpk` |
| Reduced CSDK | Extracted Reduced CSDK 12 root | `game/bin/win64/resourcecompiler.exe` |
| CS2 | Steam's `Counter-Strike Global Offensive` root | CS2 with Workshop Tools installed |
| Source 2 Viewer CLI | `Source2Viewer-CLI.exe` | Select the CLI executable, not the graphical viewer |
| Work Folder | A writable working directory | Extractions, references and build staging; separate from game archives |

Use Steam's **Manage > Browse local files** to locate a game on another drive.
Do not point Deadlock at its `citadel` or `addons` subdirectory. After setting paths,
click **Refresh** in **1 / Hero**, choose the target, then **Import Hero Skeleton**.
The importer reads the installed game's hero model, extracts its body DMX and sets
**Hero Skeleton** to the imported armature. No separate skeleton pack is needed.
The original hero meshes are hidden references. **Character Rig** in step 2 is the
replacement character's armature, not that hero armature and not a mesh object.
Start another fit with a fresh hero skeleton, without generated `CV_` bones.

## Heels

With **Preserve Source Proportions** enabled, step 5 exposes **Preserve Heeled Shoes
(Experimental)**. Enable it before fitting rigid heels. Source foot/toe geometry
moves together during fitting; the hero ankle then drives the shoe. Toe and helper
bones inherit that motion, instead of following separate hero toe rotations.
A reversed toe-end helper is not used to bend the shoe.

1. Save the current scene, then reopen the untouched pre-fit checkpoint or reimport
   the original character. The setting cannot undo an already baked deformation.
2. Import a fresh hero skeleton and verify left/right foot mappings in step 4.
3. Enable **Preserve Source Proportions** and **Preserve Heeled Shoes**, then click
   **Fit Character to Hero** once.
4. Inspect both soles from the side and below, including heel, toe box and ankle.
5. Export and check idle, walking, running and stairs in-game. Ground contact and
   leg IK are not automatically corrected by preserving shoe geometry.

Leave this off for flexible shoes or bare feet that should bend at the toes.
It disables independent toe flex for the whole foot subtree. It does not create
missing geometry, repair original weights or guarantee floor contact in every pose.
Standard target `ankle_L/R` and `foot_L/R` mappings are detected. For nonstandard
names, add `"heel_feet": ["source_left_foot", "source_right_foot"]` to the bone-map
JSON using the actual source foot-root names. Both roots must be mapped.

## Jiggle presets

**The bundled `jiggle_presets.json` is the default. ReVamped is optional.**
An external JSON replaces the default configuration; the files are not combined.
Older scenes remember their existing path, so check it after moving a project.

- The [bundled default](../addon/hero_swap_tools/jiggle_presets.json) uses broad
  hair, tail, breast, butt and ear patterns across several naming conventions.
- The [ReVamped override](../config/jiggle_revamped_single_springs.json) is intended
  for matching ReVamped rigs and selects one breast/butt spring per side to avoid
  stacked motion. It is not required for all models.
- Use a custom JSON when names differ or for skirt chains. The bundled keep list
  does not explicitly select skirts; add patterns for the actual weighted chains.

**Keep Jiggle Bones** retains eligible bones in the normal fit. In the experimental
proportion mode, deformation-relevant bones are retained independently; the preset
still selects eligible bones for jiggle export. Neither mode creates missing chains
or paints weights. The JSON contains `keep_patterns` and physics `presets`.
Patterns are case-insensitive; the first matching physics rule wins. A `skip` rule
can keep an attachment without giving it its own spring (for example `TailBase`).
Inspect the generated jiggle list, then tune and test in-game. Large scale changes
can require different limits, stiffness and damping.
