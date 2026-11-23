# legacy/batch-scripts

The older headless flow, kept so existing configs and notes still make sense:

| Script | Job |
|---|---|
| `01_import_sfm.py` | Import an SFM character and outfit into one `.blend`, flexes as shape keys |
| `02_fit_to_hero.py` | Fit that character onto a hero skeleton |
| `03_export_dmx.py` | Export the result as DMX |

Run each as `blender -b --python <script>.py -- <config.json> <out.blend>`, with a config in
the [example_hero_swap.json](../../config/example_hero_swap.json) shape (not the newer
`example_build_config.json` layout).

Nothing here is maintained. The interactive add-on in
[`addon/hero_swap_tools`](../../addon/hero_swap_tools/) is the supported path — it does the
same work with the checks, reports and presets described in the
[README](../../README.md). Use these scripts only for reproducing an older run or for a batch
job the add-on can't express yet.
