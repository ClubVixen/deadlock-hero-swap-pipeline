"""Mesh/skeleton operations shared by the Hero Swap Tools operators."""
import fnmatch
import json

import bpy
from mathutils import Matrix, Quaternion, Vector


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


# --- eyes -------------------------------------------------------------------------------------

def _eye_params(ob):
    """{eyeball material name: (loc, rotation matrix, iris size, z offset)} from SourceIO's eye_N_* props."""
    params = {}
    i = 0
    while f"eye_{i}_loc" in ob:
        empty = bpy.data.objects.get(f"eye_{i}")
        mat = empty.get("eye_material") if empty else None
        if mat:
            # the shader multiplies by the transposed (inverse) rotation of this w, x, y, z quaternion
            rot = Quaternion(ob[f"eye_{i}_rot"]).to_matrix().transposed()
            size = ob[f"eye_{i}_scale"][0] * ob[f"eye_{i}_iris_scale"]
            params[mat.name] = (Vector(ob[f"eye_{i}_loc"]), rot, size, ob[f"eye_{i}_z_offset"])
        i += 1
    return params


def _pixels(img):
    import numpy as np
    px = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(img.size[1], img.size[0], 4)


def bake_source_eyes(ob, out_dir, positions_from=None):
    """Replace SourceIO's projected EyeRefract eyes with plain UV-mapped materials.

    The iris is projected the way the eyeball shader does it, written into the eyeball faces'
    UVs, and composited over the sclera into a PNG per eye. Source 2 has no EyeRefract, and the
    projection breaks once the head is moved, so run this before the fit; positions_from can
    instead name an unfitted copy of the same mesh to project from. Returns the new materials.
    """
    import os
    import numpy as np
    src = positions_from or ob
    params = _eye_params(src)
    if not params:
        return []
    me, uv = ob.data, ob.data.uv_layers.active.data
    co = [src.matrix_world @ v.co for v in src.data.vertices]
    made = []
    for idx, slot in enumerate(ob.material_slots):
        if not (slot.material and slot.material.name in params):
            continue
        mat = slot.material
        loc, rot, size, z_off = params[mat.name]
        spin = Matrix.Rotation(z_off, 3, "Z")
        for poly in me.polygons:
            if poly.material_index != idx:
                continue
            for li in poly.loop_indices:
                v = rot @ ((co[me.loops[li].vertex_index] - loc) / size)
                v = spin @ Vector((v.y, -v.x, v.z))
                uv[li].uv = (min(max(v.y + 0.5, 0.001), 0.999), min(max(v.z + 0.5, 0.001), 0.999))
        nodes = mat.node_tree.nodes
        iris = nodes["$Iris"].image
        base = nodes["$Basetexture"].image
        sclera = _pixels(base)[..., :3].mean(axis=(0, 1)) if base else np.ones(3, dtype=np.float32)
        px = _pixels(iris)
        a = px[..., 3:4]
        px[..., :3] = sclera + (px[..., :3] - sclera) * a
        px[..., 3] = 1.0
        name = f"{mat.name}_uv"
        img = bpy.data.images.get(name) or bpy.data.images.new(name, iris.size[0], iris.size[1])
        img.pixels.foreach_set(px.ravel())
        os.makedirs(out_dir, exist_ok=True)
        img.filepath_raw = os.path.join(out_dir, name + ".png")
        img.file_format = "PNG"
        img.save()
        new = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        new.use_nodes = True
        tex = new.node_tree.nodes.get("Image Texture") or new.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf = new.node_tree.nodes["Principled BSDF"]
        new.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        slot.material = new
        made.append(new)
    return made


def rotate_chain(arm, root_name, R):
    """Apply the world-space matrix R to a bone chain and the vertices weighted to it (blended by weight)."""
    root = arm.data.bones[root_name]
    chain = {root.name} | {b.name for b in root.children_recursive}
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    to_arm, to_world = arm.matrix_world.inverted(), arm.matrix_world
    for eb in arm.data.edit_bones:
        if eb.name in chain:
            eb.head, eb.tail = to_arm @ (R @ (to_world @ eb.head)), to_arm @ (R @ (to_world @ eb.tail))
    bpy.ops.object.mode_set(mode="OBJECT")

    moved = 0
    for ob in bpy.data.objects:
        if ob.type != "MESH" or skinned_armature(ob) != arm:
            continue
        idx = {g.index for g in ob.vertex_groups if g.name in chain}
        if not idx:
            continue
        mw, mwi = ob.matrix_world, ob.matrix_world.inverted()
        w = [sum(g.weight for g in v.groups if g.group in idx) for v in ob.data.vertices]
        layers = [kb.data for kb in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []
        for data in [ob.data.vertices] + layers:
            for i, d in enumerate(data):
                if w[i] > 0:
                    p = mw @ d.co
                    d.co = mwi @ p.lerp(R @ p, min(w[i], 1.0))
        ob.data.update()
        moved += sum(1 for x in w if x > 0)
    return moved


def aim_chain(arm, root_name, pivot_name="pelvis", direction=Vector((-1, 0, 0))):
    """Swing a bone chain that came out sideways (e.g. a tail) about world Z around the pivot bone,
    so the chain's root sits in the given direction from it. Returns (degrees, vertices moved)."""
    import math
    pivot = arm.matrix_world @ arm.data.bones[pivot_name].head_local
    cur = arm.matrix_world @ arm.data.bones[root_name].head_local - pivot
    angle = math.atan2(direction.y, direction.x) - math.atan2(cur.y, cur.x)
    R = Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, "Z") @ Matrix.Translation(-pivot)
    return math.degrees(angle), rotate_chain(arm, root_name, R)


def flip_chain(arm, root_name, pivot_name="pelvis"):
    """Turn a chain upside down: 180 deg about the horizontal line from the pivot bone through the
    chain's root, for a tail that curls the wrong way after aim_chain."""
    import math
    pivot = arm.matrix_world @ arm.data.bones[root_name].head_local
    axis = pivot - arm.matrix_world @ arm.data.bones[pivot_name].head_local
    axis.z = 0
    axis = axis.normalized() if axis.length > 1e-6 else Vector((-1, 0, 0))
    R = Matrix.Translation(pivot) @ Matrix.Rotation(math.pi, 4, axis) @ Matrix.Translation(-pivot)
    return rotate_chain(arm, root_name, R)


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


def weight_target(src_arm, bonemap, hero_names):
    """name -> hero bone for a source vertex group. Bones the map doesn't name (toes, hooves, face,
    hair) go to their nearest named ancestor's hero bone, so they move with the limb they hang off.
    The "*" rule is only the last resort."""
    rules, fallback = [], "head"
    for pat, dst in bonemap["groups"].items():
        if pat == "*":
            fallback = dst
            continue
        for p, d in _pairs(pat, dst):
            if d in hero_names:
                rules.append((p, d))
    bones = src_arm.data.bones

    def target(name):
        b = bones.get(name)
        while name:
            d = next((d for p, d in rules if fnmatch.fnmatchcase(name, p)), None)
            if d:
                return d
            b = b.parent if b else None
            name = b.name if b else None
        return fallback
    return target


def retarget_weights(ob, target, src=None):
    """Rename ob's vertex groups to hero bones via target(name), merging and keeping the top 4 per vertex.
    src (an unfitted copy with the same topology) supplies the original groups when ob's are already renamed."""
    src = src or ob
    old = {g.index: target(g.name) for g in src.vertex_groups}
    weights = []
    for v in src.data.vertices:
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


def fit_to_hero(src_arm, meshes, hero_arm, bonemap, side_bones=("LeftThigh", "RightThigh"),
                height_bones=("Head", "head", "ball_L"), keep_patterns=()):
    """Fit meshes skinned to src_arm onto hero_arm without touching the hero's bones.

    Turns the character to face +X, scales it to the hero's height, moves each mapped bone
    segment onto the matching hero segment (T-pose into the hero bind pose, joints lined up),
    then renames vertex groups to hero bones, limits to 4 weights and re-parents.
    Source bones matching keep_patterns (hair/tail/chest chains) are added to the hero
    skeleton as new bones instead of being merged, so they can be made jiggle bones.
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
    def joint(name):
        """A hero joint, or "a..b@t": the point t of the way from joint a to joint b, for when the
        source has more joints than the hero along the same stretch (e.g. five spine bones vs four)."""
        if ".." not in name:
            return hero.get(name)
        a, rest = name.split("..")
        b, f = rest.split("@")
        return hero[a].lerp(hero[b], float(f)) if a in hero and b in hero else None

    for s0p, s1p, d0p, d1p in bonemap["segments"]:
        for (s0, d0), (s1, d1) in zip(_pairs(s0p, d0p), _pairs(s1p, d1p)):
            j0, j1 = joint(d0), joint(d1)
            if s0 in h and s1 in h and j0 is not None and j1 is not None:
                maps[s0] = segment_map(h[s0], h[s1], j0, j1)
                rots[s0] = (h[s1] - h[s0]).rotation_difference(j1 - j0)
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

    mapped_target = weight_target(src_arm, bonemap, hero)

    kept = [b for b in bones if any(fnmatch.fnmatchcase(b.name, p) for p in keep_patterns)]
    kept_names = {b.name for b in kept}

    def target(name):
        return name if name in kept_names else mapped_target(name)

    # kept bones (jiggle chains) are added to the hero skeleton, carried by the same map as their vertices
    if kept:
        bpy.context.view_layer.objects.active = hero_arm
        bpy.ops.object.mode_set(mode="EDIT")
        to_arm = hero_arm.matrix_world.inverted()
        ebs = hero_arm.data.edit_bones
        for b in kept:  # bones are listed parents first
            M = bone_map(b.name)
            head = to_arm @ (M @ (T @ (src_arm.matrix_world @ b.head_local)))
            tail = to_arm @ (M @ (T @ (src_arm.matrix_world @ b.tail_local)))
            kids = [c for c in b.children if c.name in kept_names]
            if kids:  # point the bone at its first kept child, so jiggle chains have real lengths
                tail = to_arm @ (bone_map(kids[0].name) @ (T @ (src_arm.matrix_world @ kids[0].head_local)))
            if (tail - head).length < 1e-3:
                tail = head + Vector((0, 0, 1))
            eb = ebs.get(b.name) or ebs.new(b.name)
            eb.head, eb.tail = head, tail
            parent = b.parent.name if b.parent else None
            eb.parent = ebs.get(parent if parent in kept_names else mapped_target(parent or ""))
        bpy.ops.object.mode_set(mode="OBJECT")

    for ob in meshes:
        deform_by_bones(ob, bone_map)
        retarget_weights(ob, target)
        reparent(ob, hero_arm)
    return yaw, scale


def _key_coords(ob, kb):
    import numpy as np
    co = np.empty(len(ob.data.vertices) * 3, dtype=np.float64)
    kb.data.foreach_get("co", co)
    return co.reshape(-1, 3)


def sync_shape_keys(body, meshes, create_missing=True, max_distance=0.0):
    """Make meshes (e.g. outfit pieces) follow the body's shape keys.

    Keys the mesh already has with a body key's name are driven by that body key. With
    create_missing, body keys the mesh lacks are made by carrying the body's deltas from the
    nearest point on the body surface (interpolated across the triangle), then driven too.
    max_distance > 0 skips vertices farther than that from the body. Returns {mesh: (driven, created)}.
    """
    import numpy as np
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import barycentric_transform

    keys = body.data.shape_keys
    if not keys or len(keys.key_blocks) < 2:
        return {}
    basis_kb = keys.reference_key
    bw = body.matrix_world
    body_basis = _key_coords(body, basis_kb)
    body_world = [bw @ Vector(c) for c in body_basis]
    me = body.data
    me.calc_loop_triangles()
    tris = [tuple(t.vertices) for t in me.loop_triangles]
    tree = BVHTree.FromPolygons(body_world, tris)
    body_deltas = {kb.name: _key_coords(body, kb) - body_basis for kb in keys.key_blocks if kb != basis_kb}
    bw3 = bw.to_3x3()

    result = {}
    for ob in meshes:
        if ob == body:
            continue
        if not ob.data.shape_keys:
            ob.shape_key_add(name="Basis", from_mix=False)
        okeys = ob.data.shape_keys
        created = 0
        missing = [n for n in body_deltas if n not in okeys.key_blocks] if create_missing else []
        if missing:
            ow = ob.matrix_world
            to_local = ow.to_3x3().inverted() @ bw3
            basis = _key_coords(ob, okeys.reference_key)
            idx = np.zeros((len(basis), 3), dtype=np.int64)
            wts = np.zeros((len(basis), 3))
            for i, c in enumerate(basis):
                p = ow @ Vector(c)
                loc, _n, ti, dist = tree.find_nearest(p)
                if ti is None or (max_distance > 0 and dist > max_distance):
                    continue
                a, b, cc = tris[ti]
                bc = barycentric_transform(loc, body_world[a], body_world[b], body_world[cc],
                                           Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
                idx[i] = (a, b, cc)
                wts[i] = (bc.x, bc.y, bc.z)
            M = np.array(to_local)
            for name in missing:
                d = body_deltas[name]
                delta = (d[idx] * wts[..., None]).sum(axis=1) @ M.T
                if not np.any(np.abs(delta) > 1e-7):
                    continue
                kb = ob.shape_key_add(name=name, from_mix=False)
                kb.data.foreach_set("co", (basis + delta).ravel())
                created += 1
        driven = 0
        for kb in okeys.key_blocks:
            if kb == okeys.reference_key or kb.name not in body_deltas:
                continue
            kb.driver_remove("value")
            fc = kb.driver_add("value")
            fc.driver.type = "AVERAGE"
            var = fc.driver.variables.new()
            var.name = "body"
            var.targets[0].id_type = "KEY"
            var.targets[0].id = keys
            var.targets[0].data_path = f'key_blocks["{kb.name}"].value'
            kb.slider_max = keys.key_blocks[kb.name].slider_max
            kb.slider_min = keys.key_blocks[kb.name].slider_min
            driven += 1
        ob.data.update()
        result[ob.name] = (driven, created)
    return result


def jiggle_vmdl_block(bone_names, presets):
    """ModelDoc JiggleBoneList text for the given bones, one JiggleBone per bone."""
    lines = ["{", '\t_class = "JiggleBoneList"', "\tchildren = ", "\t["]
    count = 0
    for name in bone_names:
        preset = next((p for p in presets if fnmatch.fnmatchcase(name, p["pattern"])), None)
        if preset is None or preset.get("skip"):
            continue
        lines += ["\t\t{", '\t\t\t_class = "JiggleBone"', f'\t\t\tname = "jiggle_{name}"', f'\t\t\tjiggle_root_bone = "{name}"']
        for k, v in preset["fields"].items():
            lines.append(f"\t\t\t{k} = {v}")
        lines.append("\t\t},")
        count += 1
    lines += ["\t]", "}"]
    return "\n".join(lines), count


def load_bonemap(path):
    with open(bpy.path.abspath(path)) as f:
        return json.load(f)
