"""Step 3: export the fitted character as a ModelDoc DMX (Binary 9, Model 22 ModelDoc).

Run:  blender -b <02.blend> --python 03_export_dmx.py -- <config.json> <export_dir>

Writes <export_dir>/<hero mesh name>.dmx, e.g. unicorn_body.dmx, so ModelDoc can swap it in
for the hero's own body mesh. Shape keys are written as flexes (ModelDoc morphs).
"""
import json
import os
import sys

import addon_utils
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
cfg = json.load(open(argv[0]))
export_dir = os.path.abspath(argv[1])
os.makedirs(export_dir, exist_ok=True)

addon_utils.enable("io_scene_valvesource", default_set=True)

hero = cfg["hero"]["codename"]
mesh_name = cfg["hero"].get("body_mesh", hero + "_body")

arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")

# the guide: no $cloth bones in the exported skeleton (ours aren't weighted to them anyway)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
for eb in [b for b in arm.data.edit_bones if b.name.startswith("_cloth")]:
    arm.data.edit_bones.remove(eb)
bpy.ops.object.mode_set(mode="OBJECT")
arm.data.pose_position = "REST"

# one collection named after the hero mesh it replaces; reference meshes are not exported
body = bpy.data.collections[cfg["character"]["name"]]
body.name = mesh_name
for c in bpy.data.collections:
    c.vs.mute = c != body
for o in body.objects:
    if o.type == "MESH":
        for slot in o.material_slots:
            if slot.material is None:
                slot.material = bpy.data.materials.new(o.name)
        if o.data.shape_keys:
            o.data.shape_keys.use_relative = True

vs = bpy.context.scene.vs
vs.export_format = "DMX"
vs.dmx_encoding = "9"
vs.dmx_format = "22_modeldoc"
vs.export_path = export_dir + os.sep
vs.up_axis = "Z"

bpy.ops.export_scene.smd(collection=body.name)
print("exported", [f for f in os.listdir(export_dir) if f.endswith(".dmx")])
