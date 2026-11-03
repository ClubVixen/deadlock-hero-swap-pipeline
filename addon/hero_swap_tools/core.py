"""Mesh/skeleton operations shared by the Hero Swap Tools operators."""
import fnmatch
import json

import bpy
from mathutils import Matrix, Vector


def rigid(m):
    """Drop scale, keep location and rotation."""
    loc, rot, _ = m.decompose()
    return Matrix.Translation(loc) @ rot.to_matrix().to_4x4()


def deform_by_bones(ob, bone_matrix, world=False):
    """Move every vertex (basis and every shape key) by the weight-blended matrix of its bones.

    bone_matrix(name) returns a 4x4 matrix or None. With world=True the matrices act on
    world-space positions and the result is written back in world space (object transform
    becomes identity under its parent).
    """
    me = ob.data
    to_world = ob.matrix_world.copy() if world else Matrix.Identity(4)
    groups = {g.index: bone_matrix(g.name) for g in ob.vertex_groups}
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


def reparent(ob, arm):
    ob.parent = None
    ob.matrix_basis = Matrix.Identity(4)
    ob.parent = arm
    ob.matrix_parent_inverse = arm.matrix_world.inverted()
    mods = [m for m in ob.modifiers if m.type == "ARMATURE"]
    if not mods:
        mods = [ob.modifiers.new("Armature", "ARMATURE")]
    for m in mods:
        m.object = arm


def skinned_armature(ob):
    for m in ob.modifiers:
        if m.type == "ARMATURE" and m.object:
            return m.object
    return ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None


def bonemerge(ob, dst_arm):
    """Move a skinned mesh (e.g. an outfit) onto another skeleton with the same bone names."""
    src_arm = skinned_armature(ob)
    if src_arm is None or src_arm == dst_arm:
        return False
    fix = {}
    for b in src_arm.data.bones:
        d = dst_arm.data.bones.get(b.name)
        if d:
            fix[b.name] = rigid(dst_arm.matrix_world @ d.matrix_local) @ rigid(src_arm.matrix_world @ b.matrix_local).inverted()
    deform_by_bones(ob, fix.get, world=True)
    reparent(ob, dst_arm)
    return True


def apply_skin(objects, swaps):
    """Swap material slots by name: {old_name: new_name}."""
    n = 0
    for ob in objects:
        for slot in getattr(ob, "material_slots", []):
            if slot.material and slot.material.name in swaps:
                new = swaps[slot.material.name]
                slot.material = bpy.data.materials.get(new) or bpy.data.materials.new(new)
                n += 1
    return n


# --- hero fit ---------------------------------------------------------------------------------

def _pairs(src, dst):
    if not any(t in src + dst for t in ("{S}", "{Side}")):
        return [(src, dst)]
    return [(src.replace("{Side}", side).replace("{S}", s), dst.replace("{Side}", side).replace("{S}", s))
            for s, side in (("L", "Left"), ("R", "Right"))]


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


def fit_to_hero(src_arm, meshes, hero_arm, bonemap, side_bones=("LeftThigh", "RightThigh"),
                height_bones=("Head", "head", "ball_L")):
    """Fit meshes skinned to src_arm onto hero_arm without touching the hero's bones.

    Turns the character to face +X, scales it to the hero's height, moves each mapped bone
    segment onto the matching hero segment (T-pose into the hero bind pose, joints lined up),
    then renames vertex groups to hero bones, limits to 4 weights and re-parents.
    """
    hero = {b.name: hero_arm.matrix_world @ b.head_local for b in hero_arm.data.bones}

    def heads():
        return {b.name: src_arm.matrix_world @ b.head_local for b in src_arm.data.bones}

    for ob in meshes:
        g = ob.vertex_groups.get("DMX Stereo Balance")
        if g:
            ob.vertex_groups.remove(g)

    h = heads()
    left = h[side_bones[0]] - h[side_bones[1]]
    fwd = left.cross(Vector((0, 0, 1)))
    yaw = Vector((fwd.x, fwd.y)).angle_signed(Vector((1, 0)))
    feet = [v.z for n, v in h.items() if "Toe" in n or "Foot" in n] or [0.0]
    ground = min(feet)
    src_head, hero_head, hero_floor = height_bones
    scale = (hero[hero_head].z - hero[hero_floor].z) / (h[src_head].z - ground)
    place = Matrix.Translation(Vector((hero["pelvis"].x, hero["pelvis"].y, 0)))
    pelvis = h.get("Pelvis", Vector())
    T = place @ Matrix.Scale(scale, 4) @ Matrix.Rotation(-yaw, 4, "Z") @ Matrix.Translation(Vector((-pelvis.x, -pelvis.y, -ground)))

    # bake the placement into the mesh data, in world space
    for ob in meshes:
        deform_by_bones(ob, lambda name: None, world=True)
        for kb in (ob.data.shape_keys.key_blocks if ob.data.shape_keys else []):
            for d in kb.data:
                d.co = T @ d.co
        for v in ob.data.vertices:
            v.co = T @ v.co
        ob.data.update()
    h = {n: T @ p for n, p in h.items()}

    maps, rots = {}, {}
    for s0p, s1p, d0p, d1p in bonemap["segments"]:
        for (s0, d0), (s1, d1) in zip(_pairs(s0p, d0p), _pairs(s1p, d1p)):
            if s0 in h and s1 in h and d0 in hero and d1 in hero:
                maps[s0] = segment_map(h[s0], h[s1], hero[d0], hero[d1])
                rots[s0] = (h[s1] - h[s0]).rotation_difference(hero[d1] - hero[d0])
    bones = src_arm.data.bones
    for sp, dp in bonemap["terminals"]:
        for s, d in _pairs(sp, dp):
            if s in h and d in hero and s not in maps:
                p = bones[s].parent
                while p and p.name not in rots:
                    p = p.parent
                R = rots[p.name].to_matrix().to_4x4() if p else Matrix.Identity(4)
                maps[s] = Matrix.Translation(hero[d]) @ R @ Matrix.Translation(-h[s])

    def bone_map(name):
        b = bones.get(name)
        while b and b.name not in maps:
            b = b.parent
        return maps[b.name] if b else Matrix.Identity(4)

    rules = []
    for pat, dst in bonemap["groups"].items():
        for p, d in _pairs(pat, dst):
            rules.append((p, d))

    def target(name):
        for p, d in rules:
            if fnmatch.fnmatchcase(name, p) and d in hero:
                return d
        return "head"

    for ob in meshes:
        deform_by_bones(ob, bone_map)
        old = {g.index: target(g.name) for g in ob.vertex_groups}
        weights = []
        for v in ob.data.vertices:
            acc = {}
            for g in v.groups:
                if g.group in old and g.weight > 0:
                    acc[old[g.group]] = acc.get(old[g.group], 0) + g.weight
            top = sorted(acc.items(), key=lambda kv: -kv[1])[:4]
            tot = sum(w for _, w in top) or 1
            weights.append([(b, w / tot) for b, w in top])
        ob.vertex_groups.clear()
        made = {}
        for i, ws in enumerate(weights):
            for b, w in ws:
                g = made.get(b) or made.setdefault(b, ob.vertex_groups.new(name=b))
                g.add([i], w, "REPLACE")
        reparent(ob, hero_arm)
    return yaw, scale


def load_bonemap(path):
    with open(bpy.path.abspath(path)) as f:
        return json.load(f)
