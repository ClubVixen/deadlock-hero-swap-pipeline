"""Step 2: fit the imported SFM character onto a Deadlock hero skeleton.

Run:  blender -b <01.blend> --python 02_fit_to_hero.py -- <config.json> <bonemap.json> <hero_body.dmx> <out.blend>

What it does, without touching the hero's bones:
  1. applies the chosen outfit skin (material swap)
  2. imports the hero body DMX (skeleton + reference meshes)
  3. turns the character to face +X and scales it to the hero's height
  4. deforms every mesh (basis + every shape key) so each source bone segment lands on
     the matching hero segment: this moves the T-pose into the hero's bind pose and
     lines up the joints
  5. renames/merges vertex groups to hero bone names, limits to 4 weights, and
     re-parents everything to the hero armature
"""
import fnmatch
import json
import os
import sys

import addon_utils
import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index("--") + 1:]
cfg_path, map_path, hero_dmx, out_blend = argv[:4]
cfg = json.load(open(cfg_path))
bonemap = json.load(open(map_path))

addon_utils.enable("io_scene_valvesource", default_set=True)


def expand(pattern):
    if "{S}" in pattern or "{Side}" in pattern:
        return [(pattern.replace("{Side}", side).replace("{S}", s), s, side) for s, side in (("L", "Left"), ("R", "Right"))]
    return [(pattern, None, None)]


def expand_pair(src, dst):
    out = []
    for s, side in (("L", "Left"), ("R", "Right")) if ("{S}" in src + dst or "{Side}" in src + dst) else ((None, None),):
        f = lambda p: p.replace("{Side}", side).replace("{S}", s) if s else p
        out.append((f(src), f(dst)))
    return out


# --- 1. outfit skin -------------------------------------------------------------------
outfit = cfg.get("outfit")
if outfit:
    base, chosen = outfit["skins"][0], outfit["skins"][outfit["skin"]]
    swap = {a: b for a, b in zip(base, chosen) if a != b}
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            if slot.material and slot.material.name in swap:
                new = swap[slot.material.name]
                slot.material = bpy.data.materials.get(new) or bpy.data.materials.new(new)
    print("skin", outfit["skin"], "swaps", swap)

src_arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
char_meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == src_arm]
for o in list(bpy.data.objects):
    if o.name.startswith("smd_bone_vis"):
        bpy.data.objects.remove(o)
# BST stores flex stereo data as a vertex group; it is not a bone and must not become weights
for o in char_meshes:
    g = o.vertex_groups.get("DMX Stereo Balance")
    if g:
        o.vertex_groups.remove(g)

# --- 2. hero skeleton ------------------------------------------------------------------
before = set(bpy.data.objects)
bpy.ops.import_scene.smd(filepath=os.path.abspath(hero_dmx), doAnim=False, makeCamera=False, append="NEW_ARMATURE")
hero_new = [o for o in bpy.data.objects if o not in before]
hero_arm = next(o for o in hero_new if o.type == "ARMATURE")
hero_ref = bpy.data.collections.new("hero_reference")
bpy.context.scene.collection.children.link(hero_ref)
for o in hero_new:
    for c in o.users_collection:
        c.objects.unlink(o)
    if o.type == "MESH" and not o.name.startswith("smd_bone_vis"):
        hero_ref.objects.link(o)
        o.hide_render = True
    elif o.type == "ARMATURE":
        bpy.context.scene.collection.objects.link(o)
    else:
        bpy.data.objects.remove(o)
hero_arm.name = cfg["hero"]["codename"] + "_skeleton"

hero_head = {b.name: hero_arm.matrix_world @ b.head_local for b in hero_arm.data.bones}

# --- 3. face +X, match height ------------------------------------------------------------
def src_heads():
    return {b.name: src_arm.matrix_world @ b.head_local for b in src_arm.data.bones}

h = src_heads()
left = h["LeftThigh"] - h["RightThigh"]                      # hips give a cleaner facing than splayed feet
fwd = left.cross(Vector((0, 0, 1)))
yaw = Vector((fwd.x, fwd.y)).angle_signed(Vector((1, 0)))
rot = Matrix.Rotation(-yaw, 4, "Z")
ground = min(v for v in (h["LeftToes2"].z, h["RightToes2"].z))
scale = (hero_head["head"].z - hero_head["ball_L"].z) / (h["Head"].z - ground)
place = Matrix.Translation(Vector((hero_head["pelvis"].x, hero_head["pelvis"].y, 0)))
src_arm.matrix_world = place @ Matrix.Scale(scale, 4) @ rot @ Matrix.Translation(Vector((-h["Pelvis"].x, -h["Pelvis"].y, -ground))) @ src_arm.matrix_world
bpy.context.view_layer.update()
print("fit: yaw %.1f deg, scale %.3f" % (yaw * 57.2958, scale))

# bake object transforms into the data so vertex coords are world space
for o in [src_arm] + char_meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = src_arm
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
h = src_heads()

# --- 4. per-segment affine maps -----------------------------------------------------------
def segment_map(a0, a1, b0, b1):
    """Affine map taking segment a0->a1 onto b0->b1 (rotate, stretch along the axis)."""
    da, db = a1 - a0, b1 - b0
    R = da.rotation_difference(db).to_matrix().to_4x4()
    k = db.length / max(da.length, 1e-6)
    n = db.normalized()
    S = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            S[i][j] += (k - 1) * n[i] * n[j]
    return Matrix.Translation(b0) @ S @ R @ Matrix.Translation(-a0)


maps, rots = {}, {}
for src0, src1, dst0, dst1 in bonemap["segments"]:
    for (s0, d0), (s1, d1) in zip(expand_pair(src0, dst0), expand_pair(src1, dst1)):
        if s0 in h and s1 in h and d0 in hero_head and d1 in hero_head:
            maps[s0] = segment_map(h[s0], h[s1], hero_head[d0], hero_head[d1])
            rots[s0] = (h[s1] - h[s0]).rotation_difference(hero_head[d1] - hero_head[d0])
src_bones = src_arm.data.bones
for src, dst in bonemap["terminals"]:
    for s, d in expand_pair(src, dst):
        if s in h and d in hero_head and s not in maps:
            p = src_bones[s].parent
            while p and p.name not in rots:
                p = p.parent
            R = rots[p.name].to_matrix().to_4x4() if p else Matrix.Identity(4)
            maps[s] = Matrix.Translation(hero_head[d]) @ R @ Matrix.Translation(-h[s])


def bone_map(name):
    b = src_bones.get(name)
    while b and b.name not in maps:
        b = b.parent
    return maps[b.name] if b else Matrix.Identity(4)


def deform_mesh(ob):
    me = ob.data
    idx_to_map = {g.index: bone_map(g.name) for g in ob.vertex_groups}
    vert_maps = []
    for v in me.vertices:
        ws = [(idx_to_map[g.group], g.weight) for g in v.groups if g.group in idx_to_map and g.weight > 0]
        tot = sum(w for _, w in ws)
        if not ws or tot <= 0:
            vert_maps.append(Matrix.Identity(4))
            continue
        M = Matrix(((0, 0, 0, 0),) * 4)
        for m, w in ws:
            M += m * (w / tot)
        vert_maps.append(M)
    keys = me.shape_keys.key_blocks if me.shape_keys else []
    for kb in keys:
        for i, d in enumerate(kb.data):
            d.co = vert_maps[i] @ d.co
    for i, v in enumerate(me.vertices):
        v.co = vert_maps[i] @ v.co
    me.update()


for ob in char_meshes:
    deform_mesh(ob)
print("deformed", len(char_meshes), "meshes onto hero bind pose")

# --- 5. vertex groups -> hero bones ---------------------------------------------------------
rules = []
for pat, dst in bonemap["groups"].items():
    for p, s, side in expand(pat):
        rules.append((p, dst.replace("{S}", s) if s else dst))


def target_bone(name):
    for p, dst in rules:
        if fnmatch.fnmatchcase(name, p) and dst in hero_head:
            return dst
    return "head"


for ob in char_meshes:
    me = ob.data
    old = {g.index: target_bone(g.name) for g in ob.vertex_groups}
    weights = []
    for v in me.vertices:
        acc = {}
        for g in v.groups:
            if g.group in old and g.weight > 0:
                acc[old[g.group]] = acc.get(old[g.group], 0) + g.weight
        top = sorted(acc.items(), key=lambda kv: -kv[1])[:4]
        tot = sum(w for _, w in top) or 1
        weights.append([(b, w / tot) for b, w in top])
    ob.vertex_groups.clear()
    groups = {}
    for i, ws in enumerate(weights):
        for b, w in ws:
            g = groups.get(b) or groups.setdefault(b, ob.vertex_groups.new(name=b))
            g.add([i], w, "REPLACE")
    for m in ob.modifiers:
        if m.type == "ARMATURE":
            m.object = hero_arm
    ob.parent = hero_arm
    ob.matrix_parent_inverse = hero_arm.matrix_world.inverted()

bpy.data.objects.remove(src_arm)

# collection layout expected by the export step: everything for the body mesh in one collection
body_col = bpy.data.collections.get(cfg["character"]["name"])
for ob in char_meshes:
    for c in ob.users_collection:
        c.objects.unlink(ob)
    body_col.objects.link(ob)
for c in list(bpy.data.collections):
    if c.name == cfg.get("outfit", {}).get("name"):
        bpy.data.collections.remove(c)
body_col.objects.link(hero_arm)
bpy.context.scene.collection.objects.unlink(hero_arm)

bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(out_blend))
print("saved", out_blend)
