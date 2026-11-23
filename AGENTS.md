# AGENTS.md — runbook for AI agents working this pipeline

Read this file, then the [README](README.md). The README explains the workflow to a human;
this file tells an agent what to run, in what order, what to check, and what does **not**
count as evidence.

Target job: put an arbitrary character (`<character>`) onto a Deadlock hero (`<hero>`) using
the Hero Swap Blender add-on, and produce a locally installed VPK — preserving the
character's proportions, outfit, secondary motion and transparency where asked.

## Hard rules

1. **Never fit over an already-fitted mesh.** Fitting squashes source geometry onto hero
   joint positions; it has no undo across file saves. Always work from the clean pre-fit
   `.blend`, and `Save As` a new file at each stage.
2. **Use a fresh hero skeleton for each fit.** A second proportion fit onto bones that
   already carry generated `CV_` bones raises `Refit requires a clean target skeleton`. That
   error is correct behaviour — get a clean skeleton, don't try to work around it.
3. **Proportion constraints must merge into the hero's existing single
   `AnimConstraintList`.** A second separate list compiles fine and the character still
   freezes in rest pose in-game. `tests/test_modeldoc.py` guards this.
4. **Don't touch unrelated mods.** When installing, replace only the conflicting package for
   the same hero, and compare hashes of the other installed VPKs before and after.
5. **Report measured numbers, not vibes.** The add-on prints reports (rotation, scale, kept
   jiggle bones, repaired vertices, material/mesh counts). Read and quote them.
6. **Never claim in-game validation you didn't observe.** Blender previews prove nothing
   about the Source 2 runtime. If you didn't launch Deadlock and look, say "unverified".
7. Ask before publishing, uploading, or overwriting the user's original model files.

## Environment

Blender 5.2 on Windows, plus:

| Requirement | Check |
|---|---|
| Deadlock install | `<root>/game/citadel/pak01_dir.vpk` exists |
| Reduced CSDK 12 | `<root>/game/bin/win64/resourcecompiler.exe` exists |
| CS2 + Workshop Tools | `...\Counter-Strike Global Offensive` present |
| Source 2 Viewer CLI | `Source2Viewer-CLI.exe` (the CLI, not the GUI) |
| Work folder | writable, separate from game archives |
| Blender add-ons | Blender Source Tools, SourceIO, and this repo's `addon/hero_swap_tools` |

Add-on preferences live in `bpy.context.preferences.addons['hero_swap_tools'].preferences`
(fields: `deadlock_dir`, `csdk_dir`, `cs2_dir`, `vrf_cli`, `work_dir`). Scene settings live on
the scene, not in preferences. If you change preferences programmatically, call
`bpy.ops.wm.save_userpref()`.

Blender Source Tools on Blender 5 may need the `flex.py` guard described in the README's
setup section.

## Repo map

```
addon/hero_swap_tools/   the add-on: core.py fit logic, proportions.py CV_ rig +
                         constraints, jiggle.py selection/rules, kv3.py, build.py,
                         __init__.py UI + operators, bundled presets
config/                  jiggle presets and example build/swap configs
docs/                    three deep guides (read the one matching the symptom)
tools/                   build_mod.py (rebuild from build_config.json), pack_vpk.py
tests/                   offline unit tests + Blender-side checks (see Running checks)
validation/              measurement records for the two documented builds — evidence only,
                         not instructions, and not a promise for other characters
legacy/batch-scripts/    older headless 3-script flow; do not modernise unless asked
```

## Canonical run order

Operator labels are what the sidebar shows; `id`s are what you call programmatically
(`bpy.ops.heroswap.<id>()`). Panels: `1. Hero`, `2. Character`, `3. Outfit`, `4. Bone Map`,
`5. Fit`, `6. Export + Build`; the `1 2 3 4 5 6 All` row is a panel filter.

| # | Do | Operator | Then check |
|---|---|---|---|
| 1 | Set tool paths | add-on preferences | files at the expected paths above exist |
| 2 | Import hero | `heroswap.refresh_heroes` → `heroswap.import_hero` | **Skeleton** filled; report names bone count; original meshes hidden as reference |
| 3 | Prep character | `heroswap.cleanup`, `heroswap.load_textures`, `heroswap.bake_eyes`, optional `heroswap.delete_materials` | **Rig** = character armature; no grey meshes; unused variants hidden *and* render-excluded |
| 4 | Outfit (optional) | `heroswap.sync_shape_keys` → `heroswap.bonemerge` → `heroswap.conform_shape`; other rig: `heroswap.fit_outfit` | linked shape-key counts; visually inspect front/side/top; note `Reach`/`Gap` used |
| 5 | Transparency (optional) | material **Alpha** + **Render Method: Blended** | unlinked Alpha only; procedural Alpha is *not* exported |
| 6 | Bone map | `heroswap.autodetect` → `heroswap.save_bonemap` | inspect both columns; pelvis/spine/neck/shoulder/elbow/hand/fingers/thigh/knee/foot present; missing-joint warnings read |
| 7 | Fit | `heroswap.fit` with `preserve_proportions=True`, `keep_jiggle=True` (heels: `preserve_heels=True`) | only once per checkpoint; report's scale + kept bones; waist/shoulders/hands/feet |
| 8 | Tail, if any | `heroswap.aim_tail` (`flip` only if truly inverted) | side and rear views |
| 9 | Jiggle | `heroswap.refresh_jiggle`, tune multipliers, `heroswap.write_jiggle` | read `jiggle_selection.txt`; anchors excluded; export writes it too |
| 10 | Export | `heroswap.export` | `proportion_constraints.kv3` exists next to the model; Replace list = hero body meshes only |
| 11 | Build | `heroswap.build` (leave `install=False` when Deadlock Mod Manager owns installs) | zero compile failures; `build_config.json` written next to the model |
| 12 | Install + look | Deadlock Mod Manager → **Add Local Mod** | restart game, screenshot the loaded character |

Restate-able details behind those steps live in
[fitting outfits and transparency](docs/fitting-outfits-and-transparency.md),
[jiggle bones and presets](docs/jiggle-bones-and-presets.md) and
[proportions, heel shoes and weapon grip](docs/proportions-heel-shoes-and-weapon-grip.md).
Read the matching one before touching its subsystem.

## Non-obvious facts worth knowing

- **Preserve Source Proportions** builds a `CV_` deformation skeleton at uniformly scaled
  source lengths driven by hero *rotations* through ModelDoc orientation constraints. It
  does not rewrite animation clips and does not change gameplay hitboxes. The older
  **Preserve Torso Proportions** (`preserve_torso`) is a different, weaker experiment — it is
  not what produced the documented results.
- Unweighted vertices end up at the world origin in-game. The fitter repairs isolated ones
  from the nearest weighted neighbour and lumps wholly unweighted parts onto the pelvis; it
  reports the counts. Read them — nearest-neighbour weights are a repair, not skinning.
- Clothing clearance (`conform_shape`) pushes intersections **outward** only. It never
  tightens loose cloth, never creates panels, and must be re-run after the hero fit and after
  any shape-slider change. `Gap` displays as `0.00` in a narrow panel while holding a real
  value.
- External jiggle JSON **replaces** the bundled `jiggle_presets.json`; nothing merges. Rules
  are case-insensitive and first-match-wins; `"skip": true` keeps a bone in the rig without a
  spring. Saved scenes remember the preset path from when they were saved.
- The stiffness/damping/angle fields are multipliers over the selected preset and hit **all**
  selected physics bones, not just hair. Angle limits are per segment, so long chains
  accumulate travel.
- Weapon grip is still manual: copy the weapon's visual bones to a `CVG_` prefix, rebind the
  mesh, then per-bone Orient constraints plus a Point constraint weighted
  `+1 original part / +1 CV_hand / −1 original hand`, merged into the existing constraint
  list. There is no operator for it.
- Sinclair is `magician`, and other display names differ from model names. The hero list is
  read from the installed game.

## Running checks

Offline (no Blender, 5 tests):

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Blender-side (needs a Blender binary on PATH or full path):

```powershell
blender -b --factory-startup --python tests/blender_regions.py
blender -b --factory-startup --python tests/blender_shape_opacity.py
blender -b --factory-startup --python tests/heels_blender.py
```

Rebuild from a previously exported configuration:

```powershell
python tools/build_mod.py "<...>\build_config.json"
```

A green test run means the constraint/mesh assembly logic behaved. It says nothing about how
the character looks in-game.

## Definition of done

Report, with numbers:

- hero model name and character; files written at each stage (pre-fit / fitted / export / VPK)
- fit report output (rotation, scale, kept jiggle bones, repaired unweighted vertices)
- mesh, material and jiggle counts in the export; whether `proportion_constraints.kv3` was
  written and merged
- compiler exit status, VPK path and hash
- exactly which mods changed in `game/citadel/addons` (and that the rest matched by hash)
- what you personally observed in-game, and an explicit **unverified** list

## Known unverified areas

Do not present these as solved based on this repo alone:

- Baba and other very different body shapes have never been fitted
- locomotion, combat and ability coverage on the documented builds (hideout inspection only);
  the legacy `hero_testing` map crashes, and `dl_midtown` on a local server was only reached
  through team selection — a map crash is not proof the model caused it
- firing, recoil and reload weapon contact (gameplay input didn't register in that test)
- ground contact and leg IK with preserved heel shoes
- full animated-frame clipping (rest-pose vertex checks only)
- arbitrary procedural Alpha networks, and materials other than the documented `pbr.vfx` path

## Prompt template for a computer-use agent

> You are working in `deadlock-hero-swap-pipeline`. Read `AGENTS.md` first and follow its
> run order. Goal: fit `<character>` onto `<hero>` in Blender 5.2 with **Preserve Source
> Proportions** and **Keep Jiggle Bones**, keep `<outfit>` and its shape keys, and export a
> VPK. Save a new file at every stage and never fit twice on the same mesh. Quote each
> operator report back to me. Stop after the export and show me `proportion_constraints.kv3`,
> the Replace list and the mesh/material/jiggle counts before you build. After install,
> restart Deadlock and send a screenshot; list anything you did not verify.

## Things an agent should not do

- Don't join meshes just to merge rigs — the exporter takes several objects on one skeleton.
- Don't run Blender cloth simulation as evidence for Source 2 jiggle.
- Don't point the Deadlock path at `citadel` or `addons`.
- Don't hand-edit generated `.blend` data behind the add-on's back when an operator exists.
- Don't commit third-party models, `.blend` sessions or generated VPKs; they're git-ignored,
  and shared deliverables here are guide + code + presets + measurements.
- Don't describe another hero's swap as "validated" because Abrams or Sinclair compiled.
