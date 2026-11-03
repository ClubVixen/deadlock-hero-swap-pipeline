"""Step 1: import an SFM character (and its outfit) into one .blend with flexes as shape keys.

Run:  blender -b --python 01_import_sfm.py -- <config.json> <out.blend>

The compiled .mdl is imported with SourceIO, because studiomdl has already resolved every
bodygroup onto one skeleton (feral manes/horns/tails are authored around different bone
positions, and assembling the raw DMX files by hand misplaces them). Flexes come in as
shape keys. Individual parts can be swapped for a source DMX (Blender Source Tools), e.g.
the SFW body, and the outfit is bone-merged onto the character skeleton.
"""
import json
import os
import sys

import addon_utils
import bpy
from mathutils import Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
cfg_path, out_blend = argv[0], argv[1]
with open(cfg_path) as f:
    cfg = json.load(f)
root = os.path.abspath(os.path.join(os.path.dirname(cfg_path), "..", ".."))

bpy.ops.wm.read_factory_settings(use_empty=True)
# factory settings unload addons, so enable them afterwards
addon_utils.enable("SourceIO", default_set=True)
addon_utils.enable("io_scene_valvesource", default_set=True)


def import_mdl(path):
    before = set(bpy.data.objects)
    bpy.ops.sourceio.mdl(filepath=path, directory=os.path.dirname(path) + os.sep,
                         files=[{"name": os.path.basename(path)}], import_textures=False,
                         create_flex_drivers=False, import_animations=False, import_physics=False,
                         discover_resources=True, bodygroup_grouping=True)
    new = [o for o in bpy.data.objects if o not in before]
    arm = next(o for o in new if o.type == "ARMATURE")
    return arm, [o for o in new if o.type == "MESH"]


def source_name(ob):
    # SourceIO names each bodygroup mesh after its source DMX path
    return ob.name.split(".dmx")[0].replace("\\", "/").lower() + ".dmx"


def keep_only(meshes, wanted):
    wanted = {w.lower() for w in wanted}
    kept = []
    for ob in meshes:
        if source_name(ob) in wanted:
            kept.append(ob)
        else:
            bpy.data.objects.remove(ob)
    return kept


def bonemerge(ob, src_arm, dst_arm):
    """Re-parent a skinned mesh from one skeleton to another with matching bone names,
    carrying vertices (and shape keys) by dst_bone @ src_bone^-1 so they follow the target rest pose."""
    def rigid(m):
        # drop scale so the mesh keeps its own size around each bone
        loc, rot, _ = m.decompose()
        return Matrix.Translation(loc) @ rot.to_matrix().to_4x4()

    fix = {}
    for b in src_arm.data.bones:
        d = dst_arm.data.bones.get(b.name)
        if d:
            fix[b.name] = rigid(dst_arm.matrix_world @ d.matrix_local) @ rigid(src_arm.matrix_world @ b.matrix_local).inverted()
    me = ob.data
    to_world = ob.matrix_world.copy()
    groups = {g.index: fix.get(g.name) for g in ob.vertex_groups}
    per_vert = []
    for v in me.vertices:
        ws = [(groups[g.group], g.weight) for g in v.groups if groups.get(g.group) is not None and g.weight > 0]
        tot = sum(w for _, w in ws)
        if tot <= 0:
            per_vert.append(to_world)
            continue
        M = Matrix(((0, 0, 0, 0),) * 4)
        for m, w in ws:
            M += m * (w / tot)
        per_vert.append(M @ to_world)
    for kb in (me.shape_keys.key_blocks if me.shape_keys else []):
        for i, d in enumerate(kb.data):
            d.co = per_vert[i] @ d.co
    for i, v in enumerate(me.vertices):
        v.co = per_vert[i] @ v.co
    me.update()
    ob.parent = None
    ob.matrix_basis = Matrix.Identity(4)
    ob.parent = dst_arm
    ob.matrix_parent_inverse = dst_arm.matrix_world.inverted()
    for m in ob.modifiers:
        if m.type == "ARMATURE":
            m.object = dst_arm


def move_to(ob, col):
    for c in ob.users_collection:
        c.objects.unlink(ob)
    col.objects.link(ob)


def collection(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


char = cfg["character"]
char_col = collection(char["name"])
arm, meshes = import_mdl(os.path.join(root, char["mdl"]))
meshes = keep_only(meshes, [p["bodygroup"] for p in char["parts"]])
move_to(arm, char_col)
for ob in meshes:
    move_to(ob, char_col)
print("character", arm.name, "bones", len(arm.data.bones), "kept", [o.name for o in meshes])

# parts replaced by a source DMX (e.g. the SFW body): imported with Source Tools, then bone-merged
for part in char["parts"]:
    dmx = part.get("replace_with") or part.get("add_dmx")
    if not dmx:
        continue
    gone = [o for o in meshes if source_name(o) == part["bodygroup"].lower()]
    meshes = [o for o in meshes if o not in gone]
    for ob in gone:
        bpy.data.objects.remove(ob)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.smd(filepath=os.path.join(root, char["source_root"], dmx),
                             doAnim=False, makeCamera=False, append="NEW_ARMATURE", upAxis="Z", rotMode="XYZ")
    new = [o for o in bpy.data.objects if o not in before]
    dmx_arm = next(o for o in new if o.type == "ARMATURE")
    # SourceIO imports in metres, Source Tools in inches: scale the DMX to match via a shared bone
    shared = [b.name for b in dmx_arm.data.bones if b.name in arm.data.bones]
    ref = max(shared, key=lambda n: (dmx_arm.matrix_world @ dmx_arm.data.bones[n].head_local).length)
    ratio = (arm.matrix_world @ arm.data.bones[ref].head_local).length / (dmx_arm.matrix_world @ dmx_arm.data.bones[ref].head_local).length
    dmx_arm.scale = [s * ratio for s in dmx_arm.scale]
    bpy.context.view_layer.update()
    for ob in new:
        if ob.type == "MESH" and not ob.name.startswith("smd_bone_vis"):
            g = ob.vertex_groups.get("DMX Stereo Balance")
            if g:
                ob.vertex_groups.remove(g)
            bonemerge(ob, dmx_arm, arm)
            move_to(ob, char_col)
            print("replaced", part["bodygroup"], "with", ob.name)
    for ob in new:
        if ob.type != "MESH" or ob.name.startswith("smd_bone_vis"):
            bpy.data.objects.remove(ob)

outfit = cfg.get("outfit")
if outfit:
    out_col = collection(outfit["name"])
    o_arm, o_meshes = import_mdl(os.path.join(root, outfit["mdl"]))
    for ob in o_meshes:
        bonemerge(ob, o_arm, arm)
        move_to(ob, out_col)
    bpy.data.objects.remove(o_arm)
    print("outfit", [o.name for o in o_meshes])

# short, plain object names: ModelDoc and the exporter match meshes by name
roles = {p["bodygroup"].lower(): p["role"] for p in char["parts"]}
for p in char["parts"]:
    for key in ("replace_with", "add_dmx"):
        if p.get(key):
            roles[os.path.basename(p[key]).lower()] = p["role"]
for ob in [o for o in bpy.data.objects if o.type == "MESH"]:
    src = source_name(ob)
    role = roles.get(src) or roles.get(os.path.basename(src))
    if role is None and outfit and ob.users_collection[0].name == outfit["name"]:
        role = os.path.basename(src)[:-4].lower().replace("revamped_", "")
    ob.name = role or ob.name
    ob.data.name = ob.name

for ob in bpy.data.objects:
    if ob.type == "MESH" and ob.data.shape_keys:
        print("SHAPEKEYS", ob.name, len(ob.data.shape_keys.key_blocks))

bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_blend))
