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


def _split_rigid(M):
    """Split an affine 4x4 into a rotation quaternion, translation and the leftover linear stretch
    R(P - I) from the polar decomposition L = R P, so M v = R v + t + R(P - I) v."""
    import numpy as np
    L = np.array(M.to_3x3())
    U, _, Vt = np.linalg.svd(L)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    rest = L - R
    return Matrix(R.tolist()).to_quaternion(), M.to_translation(), Matrix(rest.tolist())


def deform_dq(ob, bone_matrix):
    """Like deform_by_bones, but blends the rotations as dual quaternions so joints that turn a lot
    (a T-pose shoulder going to an A-pose) keep their volume instead of bulging or caving in.
    Stretch along bones is still blended linearly."""
    me = ob.data
    parts = {}
    for g in ob.vertex_groups:
        M = bone_matrix(g.name)
        if M is not None:
            parts[g.index] = _split_rigid(M)
    per_vert = []
    for v in me.vertices:
        ws = [(parts[g.group], g.weight) for g in v.groups if g.group in parts and g.weight > 0]
        tot = sum(w for _, w in ws)
        if tot <= 0:
            per_vert.append(Matrix.Identity(4))
            continue
        q0 = ws[0][0][0]
        qr = Quaternion((0, 0, 0, 0))
        qd = Quaternion((0, 0, 0, 0))
        lin = Matrix(((0, 0, 0), (0, 0, 0), (0, 0, 0)))
        for (q, tr, rest), w in ws:
            w /= tot
            if q.dot(q0) < 0:
                q = -q
            qr = qr + q * w
            qd = qd + (Quaternion((0, tr.x, tr.y, tr.z)) @ q) * (0.5 * w)
            lin = lin + rest * w
        n = qr.magnitude
        qr = qr * (1 / n)
        qd = qd * (1 / n)
        tq = (qd @ qr.conjugated()) * 2
        M = (qr.to_matrix() + lin).to_4x4()
        M.translation = Vector((tq.x, tq.y, tq.z))
        per_vert.append(M)
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
    # the two rigs may be in different units (SourceIO imports in metres, Source Tools in inches):
    # measure the shared bones' spread on both and scale the mesh to match
    shared = [b.name for b in src_arm.data.bones if b.name in dst_arm.data.bones]
    ratio = 1.0
    if len(shared) >= 2:
        def spread(arm):
            pts = [arm.matrix_world @ arm.data.bones[n].head_local for n in shared]
            c = sum(pts, Vector()) / len(pts)
            return sum((p - c).length for p in pts) / len(pts)
        s0, s1 = spread(src_arm), spread(dst_arm)
        if s0 > 1e-6 and s1 > 1e-6 and abs(s1 / s0 - 1) > 0.05:
            ratio = s1 / s0
    S = Matrix.Scale(ratio, 4)
    fix = {}
    for b in src_arm.data.bones:
        d = dst_arm.data.bones.get(b.name)
        if d:
            fix[b.name] = rigid(dst_arm.matrix_world @ d.matrix_local) @ S @ rigid(src_arm.matrix_world @ b.matrix_local).inverted()
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
        by_name = {n.name.lower(): n for n in nodes}
        iris = by_name["$iris"].image
        base = by_name["$basetexture"].image if "$basetexture" in by_name else None
        sclera = _pixels(base)[..., :3].mean(axis=(0, 1)) if base else np.ones(3, dtype=np.float32)
        px = _pixels(iris)
        a = px[..., 3:4]
        if float(a.max()) - float(a.min()) > 0.05:  # a real iris mask: composite over the sclera
            px[..., :3] = sclera + (px[..., :3] - sclera) * a
        # no mask (alpha flat, e.g. all 0): the iris image already has its own sclera around it
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
    A bone with no named ancestor (an unparented tail or prop bone) goes to the "*" rule, or with
    "fallback": "nearest" to the named bone closest to it. Groups that aren't bones of the source
    rig (left over from an older rig) return None and are dropped."""
    rules, fallback = [], bonemap.get("fallback", "head")
    for pat, dst in bonemap["groups"].items():
        if pat == "*":
            fallback = dst
            continue
        for p, d in _pairs(pat, dst):
            if d in hero_names:
                rules.append((p, d))
    bones = src_arm.data.bones
    exact = {p: d for p, d in rules if not any(c in p for c in "*?[")}
    globs = [(p, d) for p, d in rules if p not in exact]

    def rule(name):
        return exact.get(name) or next((d for p, d in globs if fnmatch.fnmatchcase(name, p)), None)

    named = [(b, rule(b.name)) for b in bones if rule(b.name)]

    def nearest(b):
        if not named:
            return None
        p = b.head_local
        return min(named, key=lambda bd: (bd[0].head_local - p).length)[1]

    def target(name):
        b = bones.get(name)
        if b is None:
            return None
        first = b
        while b:
            d = rule(b.name)
            if d:
                return d
            b = b.parent
        if fallback == "nearest" or fallback not in hero_names:
            return nearest(first)
        return fallback
    return target


def retarget_weights(ob, target, src=None):
    """Rename ob's vertex groups to hero bones via target(name), merging and keeping the top 4 per vertex.
    src (an unfitted copy with the same topology) supplies the original groups when ob's are already renamed."""
    src = src or ob
    old = {g.index: target(g.name) for g in src.vertex_groups}
    old = {i: d for i, d in old.items() if d}
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


def laplacian_offsets(co, edges):
    """Each vertex minus the mean of its edge neighbours (how far it pokes out of its surroundings)."""
    import numpy as np
    n = len(co)
    acc = np.zeros((n, 3))
    cnt = np.zeros(n)
    np.add.at(acc, edges[:, 0], co[edges[:, 1]])
    np.add.at(acc, edges[:, 1], co[edges[:, 0]])
    np.add.at(cnt, edges[:, 0], 1)
    np.add.at(cnt, edges[:, 1], 1)
    return co - acc / np.maximum(cnt, 1)[:, None]


def smooth_torso(meshes, base, torso, sigma=2.5):
    """Remove bumps the fit put into the torso. The fit's displacement (fitted minus unfitted, placed)
    is smoothed in space with a Gaussian of width sigma, sampled from the torso vertices of every mesh,
    and each torso vertex takes the smoothed value (blended by how much it belongs to the torso, so limbs
    and head stay put). Being spatial, layered pieces (outfit, decals, the neck of the head mesh) get the
    same field as the skin under them. Returns the body's bump change (worst, 95th percentile) before and
    after: how far each vertex pokes out of its neighbours compared with the unfitted model."""
    import numpy as np
    from mathutils.kdtree import KDTree

    cur, bas = {}, {}
    for ob in meshes:
        c = np.empty(len(ob.data.vertices) * 3)
        ob.data.vertices.foreach_get("co", c)
        cur[ob] = c.reshape(-1, 3)
        bas[ob] = np.array(base[ob]).reshape(-1, 3)

    # voxel-downsampled samples of the torso displacement
    cell = sigma * 0.5
    acc = {}
    for ob in meshes:
        f = torso[ob]
        for p, d, w in zip(bas[ob][f > 0.5], (cur[ob] - bas[ob])[f > 0.5], f[f > 0.5]):
            k = tuple(np.floor(p / cell).astype(int))
            a = acc.setdefault(k, [np.zeros(3), np.zeros(3), 0.0])
            a[0] += p * w
            a[1] += d * w
            a[2] += w
    if not acc:
        return None
    sp = np.array([a[0] / a[2] for a in acc.values()])
    sd = np.array([a[1] / a[2] for a in acc.values()])
    sw = np.array([a[2] for a in acc.values()])
    kd = KDTree(len(sp))
    for i, p in enumerate(sp):
        kd.insert(p, i)
    kd.balance()

    def field(p):
        hits = kd.find_range(p, sigma * 3)
        if not hits:
            return None
        idx = np.array([i for _, i, _ in hits])
        dist = np.array([dd for _, _, dd in hits])
        w = np.exp(-(dist / sigma) ** 2 / 2) * sw[idx]
        return (sd[idx] * w[:, None]).sum(0) / w.sum()

    body = max(meshes, key=lambda o: float(np.sum(torso[o])))
    edges = np.empty(len(body.data.edges) * 2, dtype=np.int64)
    body.data.edges.foreach_get("vertices", edges)
    edges = edges.reshape(-1, 2)
    region = torso[body] > 0.05

    def bump(c):
        d = np.linalg.norm(laplacian_offsets(c, edges) - laplacian_offsets(bas[body], edges), axis=1)[region]
        return (round(float(d.max()), 4), round(float(np.percentile(d, 95)), 4)) if len(d) else (0.0, 0.0)

    before = bump(cur[body])
    after = None
    for ob in meshes:
        f = np.clip(torso[ob], 0, 1)
        delta = np.zeros_like(cur[ob])
        for i in np.nonzero(f > 0.01)[0]:
            fd = field(bas[ob][i])
            if fd is not None:
                delta[i] = f[i] * ((bas[ob][i] + fd) - cur[ob][i])
        if ob is body:
            after = bump(cur[ob] + delta)
        if not delta.any():
            continue
        for data in [ob.data.vertices] + ([kb.data for kb in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []):
            for i, d in enumerate(data):
                if delta[i].any():
                    d.co = d.co + Vector(delta[i])
        ob.data.update()
    return before, after


def restore_clearance(meshes, base, body, reach=1.0):
    """Push layered pieces (outfit, tail root) back out where the fit sank them into the body:
    every vertex within reach of the body before the fit gets back at least its old signed height
    above the skin (pieces tucked in on purpose stay tucked in, but no deeper)."""
    from mathutils.bvhtree import BVHTree

    def tree(coords):
        return BVHTree.FromPolygons([Vector(c) for c in coords], [p.vertices[:] for p in body.data.polygons])

    before = tree(base[body])
    after = tree([v.co for v in body.data.vertices])
    pushed = 0
    for ob in meshes:
        if ob is body:
            continue
        moves = {}
        for i, (b, v) in enumerate(zip(base[ob], ob.data.vertices)):
            hit = before.find_nearest(Vector(b), reach)
            if hit[0] is None:
                continue
            old = (Vector(b) - hit[0]).dot(hit[1])  # height above the skin before (negative: tucked in)
            now_hit = after.find_nearest(v.co)
            if now_hit[0] is None:
                continue
            now = (v.co - now_hit[0]).dot(now_hit[1])
            if now < old:
                moves[i] = now_hit[1] * (old - now)
        for data in [ob.data.vertices] + ([kb.data for kb in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []):
            for i, m in moves.items():
                data[i].co = data[i].co + m
        ob.data.update()
        pushed += len(moves)
    return pushed


def keep_bone(name, patterns):
    """Whether a source bone matches a keep pattern (case-insensitive fnmatch)."""
    low = name.lower()
    return any(fnmatch.fnmatchcase(low, p.lower()) for p in patterns)


def fit_to_hero(src_arm, meshes, hero_arm, bonemap, side_bones=("LeftThigh", "RightThigh"),
                height_bones=("Head", "head", "ball_L"), keep_patterns=(), clearance=True):
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

    def facing(pos, pair):
        left = pos[pair[0]] - pos[pair[1]]
        fwd = left.cross(Vector((0, 0, 1)))
        return Vector((fwd.x, fwd.y)).angle_signed(Vector((1, 0)))

    h = heads()
    src_pelvis_name, hero_pelvis_name = bonemap.get("pelvis", ["Pelvis", "pelvis"])
    yaw = facing(h, bonemap.get("side_bones", side_bones))
    # the target's own facing (Deadlock heroes face +X; another character rig may not)
    dst_sides = bonemap.get("dst_side_bones")
    dst_yaw = facing(hero, dst_sides) if dst_sides else 0.0
    turn = Matrix.Rotation(dst_yaw, 4, "Z") @ Matrix.Rotation(-yaw, 4, "Z")
    pelvis = h.get(src_pelvis_name, Vector())
    if "scale_bones" in bonemap:
        # no feet to stand on (e.g. an outfit rig): match pelvis-to-shoulder height, pelvis on pelvis
        (sa, sb), (da, db) = bonemap["scale_bones"]
        scale = abs(hero[db].z - hero[da].z) / max(abs(h[sb].z - h[sa].z), 1e-6)
        T = Matrix.Translation(hero[hero_pelvis_name]) @ Matrix.Scale(scale, 4) @ turn @ Matrix.Translation(-pelvis)
    else:
        ground_names = bonemap.get("ground_bones") or [n for n in h if "Toe" in n or "Foot" in n]
        feet = [h[n].z for n in ground_names if n in h] or [0.0]
        ground = min(feet)
        src_head, hero_head, hero_floor = bonemap.get("height_bones", height_bones)
        scale = (hero[hero_head].z - hero[hero_floor].z) / (h[src_head].z - ground)
        place = Matrix.Translation(Vector((hero[hero_pelvis_name].x, hero[hero_pelvis_name].y, 0)))
        T = place @ Matrix.Scale(scale, 4) @ turn @ Matrix.Translation(Vector((-pelvis.x, -pelvis.y, -ground)))

    # bake the placement into the mesh data, in world space
    for ob in meshes:
        deform_by_bones(ob, lambda name: None, world=True)
        for kb in (ob.data.shape_keys.key_blocks if ob.data.shape_keys else []):
            for d in kb.data:
                d.co = T @ d.co
        for v in ob.data.vertices:
            v.co = T @ v.co
        ob.data.update()
    base = {ob: [v.co.copy() for v in ob.data.vertices] for ob in meshes}
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

    for seg in bonemap["segments"]:
        s0p, s1p, d0p, d1p = seg[:4]
        move_only = len(seg) > 4 and seg[4] == "move"
        for (s0, d0), (s1, d1) in zip(_pairs(s0p, d0p), _pairs(s1p, d1p)):
            j0, j1 = joint(d0), joint(d1)
            if s0 in h and s1 in h and j0 is not None and j1 is not None:
                if move_only:  # torso: slide the joint onto the hero's, keep the character's own shape
                    maps[s0] = Matrix.Translation(j0 - h[s0])
                    rots[s0] = Quaternion()
                else:
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

    mapped_target = weight_target(src_arm, bonemap, hero)
    nearest_key = {}

    def bone_map(name):
        b = bones.get(name)
        if b is None:
            return None  # a stale vertex group, not a bone of this rig
        first = b
        while b and b.name not in maps:
            b = b.parent
        if b:
            return maps[b.name]
        # an unparented bone (tail, prop): ride with the closest mapped bone
        if first.name not in nearest_key:
            p = first.head_local
            mapped = [x for x in bones if x.name in maps]
            nearest_key[first.name] = min(mapped, key=lambda x: (x.head_local - p).length).name if mapped else None
        k = nearest_key[first.name]
        return maps[k] if k else Matrix.Identity(4)

    kept = [b for b in bones if keep_bone(b.name, keep_patterns)]
    kept_names = {b.name for b in kept}
    # Source 2 only takes letters, digits and _ in bone names (AC_hair_A.R -> AC_hair_A_R)
    import re
    safe = {n: re.sub(r"[^A-Za-z0-9_]", "_", n) for n in kept_names}

    def target(name):
        return safe[name] if name in kept_names else mapped_target(name)

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
            eb = ebs.get(safe[b.name]) or ebs.new(safe[b.name])
            eb.head, eb.tail = head, tail
            parent = b.parent.name if b.parent else None
            if parent in kept_names:
                eb.parent = ebs.get(safe[parent])
            else:
                eb.parent = ebs.get(mapped_target(parent or b.name) or "")
        bpy.ops.object.mode_set(mode="OBJECT")

    stop = set(bonemap.get("torso_stop", []))

    def map_key(name):
        """The mapped bone a vertex group rides on; None for anything under a torso_stop bone (neck, head)."""
        b = bones.get(name)
        while b and b.name not in maps:
            if b.name in stop:
                return None
            b = b.parent
        return b.name if b and b.name not in stop else None

    torso_keys = set(bonemap.get("torso", []))
    torso = {}
    for ob in meshes:
        deform_dq(ob, bone_map)
        is_torso = {g.index: map_key(g.name) in torso_keys for g in ob.vertex_groups}
        fr = []
        for v in ob.data.vertices:
            tot = sum(g.weight for g in v.groups)
            fr.append(sum(g.weight for g in v.groups if is_torso.get(g.group)) / tot if tot > 0 else 0.0)
        torso[ob] = fr
    import numpy as np
    torso = {ob: np.array(fr) for ob, fr in torso.items()}
    bumps = smooth_torso(meshes, base, torso, bonemap.get("torso_smoothing", 2.5)) if torso_keys else None
    fit_to_hero.last_pushed = 0
    if torso_keys and clearance:
        body = max(meshes, key=lambda o: float(np.sum(torso[o])))
        fit_to_hero.last_pushed = restore_clearance(meshes, base, body)
    for ob in meshes:
        retarget_weights(ob, target)
        reparent(ob, hero_arm)
    fit_to_hero.last_bumps = bumps
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
        preset = next((p for p in presets if fnmatch.fnmatchcase(name.lower(), p["pattern"].lower())), None)
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


# --- generic model clean-up -------------------------------------------------------------------

def rig_meshes(arm):
    return [o for o in bpy.data.objects if o.type == "MESH" and skinned_armature(o) == arm]


def load_source_materials(objects, materials_root, force=False):
    """Rebuild materials SourceIO left untextured. SourceIO looks textures up case-sensitively, so a
    mod whose folders are capitalised differently from the model's paths (Fursonas vs fursonas,
    1_Body.vtf vs 1_body) comes in grey. This finds each material's .vmt and its $basetexture /
    $bumpmap .vtf files case-insensitively under materials_root and wires them into the material
    (base colour, normal map, alpha). Returns {material: [textures loaded]}."""
    import os
    import re
    index_vmt, index_vtf = {}, {}
    for dirpath, _, files in os.walk(materials_root):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, materials_root).replace("\\", "/").lower()
            if f.lower().endswith(".vmt"):
                index_vmt.setdefault(f.lower()[:-4], []).append(full)
            elif f.lower().endswith(".vtf"):
                index_vtf[rel[:-4]] = full
                index_vtf.setdefault(os.path.basename(rel)[:-4], full)

    def vtf(path):
        key = path.replace("\\", "/").lower().strip("/")
        if key.endswith(".vtf"):
            key = key[:-4]
        if key.startswith("materials/"):
            key = key[len("materials/"):]
        full = index_vtf.get(key) or index_vtf.get(os.path.basename(key))
        if not full:
            return None
        name = os.path.splitext(os.path.basename(full))[0]
        img = bpy.data.images.get(name)
        if img is None or img.size[0] == 0:
            before = set(bpy.data.images)
            bpy.ops.sourceio.vtf(filepath=full, directory=os.path.dirname(full) + os.sep, files=[{"name": os.path.basename(full)}])
            new = [i for i in bpy.data.images if i not in before]
            img = new[0] if new else bpy.data.images.get(name)
        return img

    done = {}
    mats = []
    for ob in objects:
        for s in ob.material_slots:
            if s.material and s.material not in mats:
                mats.append(s.material)
    for mat in mats:
        info = material_images(mat)
        if info["color"] is not None and not info["color"].name.startswith("missing") and not force:
            continue
        cands = index_vmt.get(mat.name.lower().split(".")[0])
        if not cands:
            continue
        text = open(cands[0], encoding="utf-8", errors="replace").read()
        text = re.sub(r"//[^\n]*", "", text)

        def param(key):
            m = re.search(r'"?\$' + key + r'"?\s+"?([^"\n]+?)"?\s*$', text, re.I | re.M)
            return m.group(1).strip() if m else None
        base = param("basetexture")
        bump = param("bumpmap") or param("normalmap")
        iris = param("iris")
        if iris and not base:  # EyeRefract: keep the iris for Convert Source Eyes to Textures
            img = vtf(iris)
            if img:
                mat.use_nodes = True
                node = mat.node_tree.nodes.get("$Iris") or mat.node_tree.nodes.new("ShaderNodeTexImage")
                node.name = node.label = "$Iris"
                node.image = img
                done[mat.name] = [img.name]
            continue
        if not base:
            continue
        col = vtf(base)
        nrm = vtf(bump) if bump else None
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        nt.links.new(bsdf.outputs[0], out.inputs[0])
        loaded = []
        if col:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.name = t.label = "$basetexture"
            t.image = col
            nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
            if (param("translucent") or "0") != "0" or (param("alphatest") or "0") != "0":
                nt.links.new(t.outputs["Alpha"], bsdf.inputs["Alpha"])
            loaded.append(col.name)
        if nrm:
            t = nt.nodes.new("ShaderNodeTexImage")
            t.name = t.label = "$bumpmap"
            t.image = nrm
            t.image.colorspace_settings.name = "Non-Color"
            nm = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(t.outputs["Color"], nm.inputs["Color"])
            nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
            loaded.append(nrm.name)
        if (param("translucent") or "0") != "0":
            mat["$translucent"] = 1
        if (param("alphatest") or "0") != "0":
            mat["$alphatest"] = 1
        done[mat.name] = loaded
    return done


def plain_mesh(ob):
    """ob.data as a plain bpy Mesh: SourceIO swaps the Python class of its meshes to FastMesh, which bmesh refuses."""
    me = ob.data
    if type(me) is not bpy.types.Mesh:
        try:
            me.__class__ = bpy.types.Mesh
        except TypeError:
            pass
    return me


def cleanup_meshes(arm, meshes=None):
    """Make a Blender-made character safe to fit and export:
    - drops Armature modifiers with no rig set (and points the others at arm),
    - turns meshes parented to a single bone into meshes weighted 100% to that bone,
    - deletes geometry that Mask modifiers hide (shape keys are kept) and removes the masks,
    - removes vertex groups that aren't bones of arm.
    Returns a short report per mesh."""
    import bmesh
    report = {}
    bones = set(arm.data.bones.keys())
    for ob in meshes or [o for o in bpy.data.objects if o.type == "MESH" and (o.parent == arm or skinned_armature(o) == arm)]:
        notes = []
        mw = ob.matrix_world.copy()
        if ob.parent == arm and ob.parent_type == "BONE" and ob.parent_bone in bones:
            g = ob.vertex_groups.get(ob.parent_bone) or ob.vertex_groups.new(name=ob.parent_bone)
            g.add(range(len(ob.data.vertices)), 1.0, "REPLACE")
            bone = ob.parent_bone
            ob.parent_type = "OBJECT"
            ob.parent_bone = ""
            ob.matrix_world = mw
            notes.append(f"was parented to bone {bone}")
        arms = [m for m in ob.modifiers if m.type == "ARMATURE"]
        for m in arms:
            if m.object is None and len(arms) > 1:
                ob.modifiers.remove(m)
        arms = [m for m in ob.modifiers if m.type == "ARMATURE"]
        if not arms:
            arms = [ob.modifiers.new("Armature", "ARMATURE")]
        for m in arms:
            m.object = arm
        if ob.parent != arm:
            ob.parent = arm
            ob.matrix_parent_inverse = arm.matrix_world.inverted()
            ob.matrix_world = mw
        for m in [m for m in ob.modifiers if m.type == "MASK" and m.mode == "VERTEX_GROUP" and m.show_viewport]:
            g = ob.vertex_groups.get(m.vertex_group)
            if g is None:
                ob.modifiers.remove(m)
                continue
            gi, thr, inv = g.index, m.threshold, m.invert_vertex_group
            bm = bmesh.new()
            bm.from_mesh(plain_mesh(ob))
            dl = bm.verts.layers.deform.verify()
            gone = [v for v in bm.verts if ((v[dl].get(gi, 0.0) > thr) == inv)]
            bmesh.ops.delete(bm, geom=gone, context="VERTS")
            bm.to_mesh(plain_mesh(ob))
            bm.free()
            ob.modifiers.remove(m)
            notes.append(f"mask {g.name}: removed {len(gone)} hidden vertices")
        stale = [g for g in ob.vertex_groups if g.name not in bones]
        for g in stale:
            ob.vertex_groups.remove(g)
        if stale:
            notes.append(f"removed {len(stale)} vertex groups that aren't bones")
        ob.data.update()
        report[ob.name] = notes
    return report


def delete_material_faces(objects, names):
    """Delete the faces that use any of the named materials (case-insensitive), e.g. NSFW parts."""
    import bmesh
    names = {n.lower() for n in names}
    total = 0
    for ob in objects:
        idx = {i for i, s in enumerate(ob.material_slots) if s.material and s.material.name.lower() in names}
        if not idx:
            continue
        bm = bmesh.new()
        bm.from_mesh(plain_mesh(ob))
        faces = [f for f in bm.faces if f.material_index in idx]
        bmesh.ops.delete(bm, geom=faces, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(plain_mesh(ob))
        bm.free()
        ob.data.update()
        total += len(faces)
    return total


# --- outfits made for another body ------------------------------------------------------------

def _world_tris(ob):
    from mathutils.bvhtree import BVHTree
    mw = ob.matrix_world
    me = ob.data
    me.calc_loop_triangles()
    verts = [mw @ v.co for v in me.vertices]
    tris = [tuple(t.vertices) for t in me.loop_triangles]
    return verts, tris, BVHTree.FromPolygons(verts, tris)


def _edges(ob):
    import numpy as np
    e = np.empty(len(ob.data.edges) * 2, dtype=np.int64)
    ob.data.edges.foreach_get("vertices", e)
    return e.reshape(-1, 2)


def _smooth_field(d, edges, keep, iters):
    """Average a per-vertex vector field over mesh edges; rows where keep is True stay fixed."""
    import numpy as np
    n = len(d)
    for _ in range(iters):
        acc = np.zeros_like(d)
        cnt = np.zeros(n)
        np.add.at(acc, edges[:, 0], d[edges[:, 1]])
        np.add.at(acc, edges[:, 1], d[edges[:, 0]])
        np.add.at(cnt, edges[:, 0], 1)
        np.add.at(cnt, edges[:, 1], 1)
        avg = np.where(cnt[:, None] > 0, acc / np.maximum(cnt, 1)[:, None], d)
        d = np.where(keep[:, None], d, 0.5 * d + 0.5 * avg)
    return d


def morph_body_onto(base_body, body, smooth=30, rounds=3):
    """World positions of base_body's vertices moved onto body's surface: nearest points, smoothed
    over base_body's edges between re-projections so the morph never folds (a small chest grows
    smoothly into a big one instead of bunching up)."""
    import numpy as np
    _, _, tree = _world_tris(body)
    mw = base_body.matrix_world
    co = np.array([tuple(mw @ v.co) for v in base_body.data.vertices])
    edges = _edges(base_body)
    cur = co.copy()
    for r in range(rounds):
        tgt = np.array([tuple(tree.find_nearest(Vector(p))[0]) for p in cur])
        d = tgt - cur
        d = _smooth_field(d, edges, np.zeros(len(d), dtype=bool), smooth if r < rounds - 1 else max(2, smooth // 6))
        cur = cur + d
    return co, cur


def surface_deform_moves(meshes, base_body, morphed):
    """Carry meshes along with base_body as its vertices move to morphed (world positions), using
    Blender's Surface Deform modifier on temporary copies. Returns {mesh: world-space moves}."""
    import numpy as np
    col = bpy.context.scene.collection
    tgt_me = bpy.data.meshes.new("__hs_sd_target")
    mw = base_body.matrix_world
    src = base_body.data
    tgt_me.from_pydata([tuple(mw @ v.co) for v in src.vertices], [], [tuple(p.vertices) for p in src.polygons])
    tgt = bpy.data.objects.new("__hs_sd_target", tgt_me)
    col.objects.link(tgt)
    tgt.shape_key_add(name="Basis")
    key = tgt.shape_key_add(name="morph")
    key.data.foreach_set("co", np.asarray(morphed, dtype=np.float64).ravel())
    out, temps = {}, []
    try:
        for ob in meshes:
            omw = ob.matrix_world
            me = bpy.data.meshes.new("__hs_sd_piece")
            me.from_pydata([tuple(omw @ v.co) for v in ob.data.vertices], [e.vertices[:] for e in ob.data.edges],
                           [tuple(p.vertices) for p in ob.data.polygons])
            tmp = bpy.data.objects.new("__hs_sd_piece", me)
            col.objects.link(tmp)
            temps.append(tmp)
            mod = tmp.modifiers.new("sd", "SURFACE_DEFORM")
            mod.target = tgt
            mod.falloff = 4.0
            key.value = 0.0
            bpy.context.view_layer.update()
            with bpy.context.temp_override(object=tmp, active_object=tmp, selected_objects=[tmp]):
                bpy.ops.object.surfacedeform_bind(modifier=mod.name)
            key.value = 1.0
            dg = bpy.context.evaluated_depsgraph_get()
            dg.update()
            ev = tmp.evaluated_get(dg)
            em = ev.to_mesh()
            new = np.array([tuple(v.co) for v in em.vertices])
            ev.to_mesh_clear()
            old = np.array([tuple(v.co) for v in me.vertices])
            out[ob] = new - old if len(new) == len(old) and mod.is_bound else None
    finally:
        for t in temps:
            m = t.data
            bpy.data.objects.remove(t)
            bpy.data.meshes.remove(m)
        bpy.data.objects.remove(tgt)
        bpy.data.meshes.remove(tgt_me)
    return out


def transfer_to_body(meshes, body, base_body=None, reach=3.0, gap=0.08, smooth=8):
    """Move outfit pieces from the body they were made for onto another body.

    With base_body (the outfit's original body, already moved with the outfit onto the new
    skeleton), each outfit vertex keeps its height above the original skin, measured at the
    nearest skin point, and is re-placed at the same height over the nearest point of the new
    body. Without it, pieces are only pushed out where they sink into the new body. Parts out of
    reach of the skin (shoulder guards, gems) follow the moves of the nearby parts. Shape keys
    move with the basis. Returns {mesh: (moved, pushed out)}."""
    import numpy as np
    from mathutils.kdtree import KDTree

    _, _, tree = _world_tris(body)
    tree0 = _world_tris(base_body)[2] if base_body else None
    # preferred: morph the original body onto the new one and carry the outfit with Surface Deform,
    # which keeps every garment's own shape over the skin under it
    sd = {}
    if base_body is not None:
        try:
            _, morphed = morph_body_onto(base_body, body)
            sd = surface_deform_moves(meshes, base_body, morphed)
        except Exception as e:  # fall back to the per-point transfer
            print("Hero Swap: Surface Deform transfer failed, using nearest points:", e)
            sd = {}
    result, fields = {}, {}
    for ob in meshes:
        mw, mwi = ob.matrix_world, ob.matrix_world.inverted()
        co = np.array([tuple(mw @ v.co) for v in ob.data.vertices])
        d = np.zeros_like(co)
        known = np.zeros(len(co), dtype=bool)
        heights = np.full(len(co), np.nan)
        moves = sd.get(ob)
        if moves is not None:
            d[:] = moves
            known[:] = True
            for i, p in enumerate(co):
                hit = tree0.find_nearest(Vector(p), reach)
                if hit[0] is not None:
                    heights[i] = (Vector(p) - hit[0]).dot(hit[1])
            fields[ob] = (co, d, known, heights, mw, mwi)
            continue
        for i, p in enumerate(co):
            v = Vector(p)
            if tree0 is not None:
                p0, n0, _, dist = tree0.find_nearest(v, reach)
                if p0 is None:
                    continue
                h = (v - p0).dot(n0)
                side = v - p0 - n0 * h
                p1, n1, _, _ = tree.find_nearest(p0)
                if p1 is None:
                    continue
                new_h = h if h < 0 else max(h, gap)
                d[i] = tuple(p1 + side + n1 * new_h - v)
                known[i] = True
                heights[i] = h
        fields[ob] = (co, d, known, heights, mw, mwi)

    # vertices out of reach: follow the known moves around them (all pieces together)
    pts = [(co[i], d[i]) for co, d, known, *_ in fields.values() for i in np.nonzero(known)[0]]
    if pts and tree0 is not None:
        kd = KDTree(len(pts))
        for i, (p, _) in enumerate(pts):
            kd.insert(p, i)
        kd.balance()
        for ob, (co, d, known, *_r) in fields.items():
            for i in np.nonzero(~known)[0]:
                hits = kd.find_n(co[i], 8)
                w = np.array([1.0 / max(dd, 1e-4) ** 2 for _, _, dd in hits])
                dd = np.array([pts[j][1] for _, j, _ in hits])
                d[i] = (dd * w[:, None]).sum(0) / w.sum()

    for ob, (co, d, known, heights, mw, mwi) in fields.items():
        edges = _edges(ob)
        if smooth and len(edges) and sd.get(ob) is None:
            d = _smooth_field(d, edges, np.zeros(len(d), dtype=bool), smooth)
        # push out anything still inside the new body (pieces tucked in on purpose stay in)
        pushed = 0
        for _round in range(2):
            push = np.zeros_like(d)
            for i, p in enumerate(co + d):
                if not np.isnan(heights[i]) and heights[i] < 0:
                    continue
                v = Vector(p)
                q, n, _, dist = tree.find_nearest(v, reach)
                if q is None:
                    continue
                s = (v - q).dot(n)
                if s < gap:
                    push[i] = tuple(n * (gap - s))
            moved = np.linalg.norm(push, axis=1) > 1e-6
            pushed = max(pushed, int(moved.sum()))
            if not moved.any():
                break
            if len(edges):
                push = _smooth_field(push, edges, moved, 2)
            d = d + push
        lin = mwi.to_3x3()
        local = np.array([tuple(lin @ Vector(x)) for x in d])
        for data in [ob.data.vertices] + ([kb.data for kb in ob.data.shape_keys.key_blocks] if ob.data.shape_keys else []):
            for i, x in enumerate(local):
                if x.any():
                    data[i].co = data[i].co + Vector(x)
        ob.data.update()
        result[ob.name] = (int((np.linalg.norm(d, axis=1) > 1e-6).sum()), pushed)
    return result


def is_see_through(ob):
    return any(s.material and material_images(s.material)["translucent"] for s in ob.material_slots)


def delete_covered_skin(body, outfit, max_dist, gap=None):
    """Keep skin from showing through clothes.
    1. Skin points that poke out past the cloth, or sit closer under it than gap, are pushed in to
       gap under it (a low-poly bra over a rounder, denser chest cuts through it between its points).
    2. Body faces the outfit fully covers are deleted: a ray outwards from every corner and the
       centre must hit the cloth within max_dist. Skin under openings stays.
    See-through pieces (translucent sleeves, lace) don't count as cover. Shape keys are kept.
    Returns (points pushed in, faces deleted)."""
    import bmesh
    from mathutils.bvhtree import BVHTree
    gap = max_dist * 0.25 if gap is None else gap
    outfit = [o for o in outfit if not is_see_through(o)]
    verts, tris = [], []
    for ob in outfit:
        mw = ob.matrix_world
        ob.data.calc_loop_triangles()
        base = len(verts)
        verts += [mw @ v.co for v in ob.data.vertices]
        tris += [tuple(base + i for i in t.vertices) for t in ob.data.loop_triangles]
    if not tris:
        return 0
    tree = BVHTree.FromPolygons(verts, tris)
    mw = body.matrix_world
    nm = mw.inverted().transposed().to_3x3()
    eps = max_dist * 0.01

    def covered(p, n):
        # cloth in front of the skin, or skin poking out just past the cloth
        n = n.normalized()
        if tree.ray_cast(p + n * eps, n, max_dist)[0] is not None:
            return True
        return tree.ray_cast(p + n * eps, -n, max_dist)[0] is not None

    me = plain_mesh(body)
    # 1. push poking / too-close skin in under the cloth
    mwi3 = mw.inverted().to_3x3()
    moves = {}
    for v in me.vertices:
        p, n = mw @ v.co, (nm @ v.normal).normalized()
        back = tree.ray_cast(p + n * max_dist, -n, max_dist * 2)  # the outermost cloth along the normal
        if back[0] is not None:
            depth = (p - back[0]).dot(n)  # > 0: skin is outside the cloth
            if depth > -gap:
                moves[v.index] = mwi3 @ (-n * (depth + gap))
    for data in [me.vertices] + ([kb.data for kb in me.shape_keys.key_blocks] if me.shape_keys else []):
        for i, m in moves.items():
            data[i].co = data[i].co + m
    me.update()
    vcov = [covered(mw @ v.co, nm @ v.normal) for v in me.vertices]
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    gone = []
    for f in bm.faces:
        if all(vcov[v.index] for v in f.verts) and covered(mw @ f.calc_center_median(), nm @ f.normal):
            gone.append(f)
    bmesh.ops.delete(bm, geom=gone, context="FACES_ONLY")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(me)
    bm.free()
    me.update()
    return len(moves), len(gone)


def copy_body_weights(meshes, body, reach=2.0, blend=1.0):
    """Give outfit vertices near the skin the body's weights at the nearest skin point (blended
    across the triangle), so tight clothes bend exactly like the skin under them. Vertices
    farther than reach keep their own weights. blend < 1 mixes with the outfit's own weights."""
    from mathutils.geometry import barycentric_transform
    verts, tris, tree = _world_tris(body)
    names = {g.index: g.name for g in body.vertex_groups}
    bw = [{names[g.group]: g.weight for g in v.groups if g.group in names and g.weight > 0} for v in body.data.vertices]
    changed = 0
    for ob in meshes:
        mw = ob.matrix_world
        own = {g.index: g.name for g in ob.vertex_groups}
        new_weights = []
        for v in ob.data.vertices:
            p = mw @ v.co
            loc, _n, ti, dist = tree.find_nearest(p, reach)
            mine = {own[g.group]: g.weight for g in v.groups if g.group in own and g.weight > 0}
            if ti is None:
                new_weights.append(None)
                continue
            a, b, c = tris[ti]
            bc = barycentric_transform(loc, verts[a], verts[b], verts[c], Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1)))
            acc = {}
            for idx, w in zip((a, b, c), (bc.x, bc.y, bc.z)):
                for name, x in bw[idx].items():
                    acc[name] = acc.get(name, 0) + max(w, 0) * x
            tot = sum(acc.values()) or 1
            acc = {k: blend * x / tot for k, x in acc.items()}
            mt = sum(mine.values()) or 1
            for k, x in mine.items():
                acc[k] = acc.get(k, 0) + (1 - blend) * x / mt
            top = sorted(acc.items(), key=lambda kv: -kv[1])[:4]
            t2 = sum(x for _, x in top) or 1
            new_weights.append([(k, x / t2) for k, x in top])
        for i, ws in enumerate(new_weights):
            if ws is None:
                continue
            for g in ob.vertex_groups:
                g.remove([i])
            for name, w in ws:
                g = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
                g.add([i], w, "REPLACE")
            changed += 1
    return changed


# --- materials for Source 2 -------------------------------------------------------------------

def safe_name(name, used):
    import re
    base = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "material"
    n, k = base, 1
    while n in used:
        k += 1
        n = f"{base}_{k}"
    used.add(n)
    return n


def _upstream_image(sock, depth=0):
    """The first image texture feeding a node socket (through mix/colour nodes)."""
    if not sock or not sock.is_linked or depth > 8:
        return None
    node = sock.links[0].from_node
    if node.type == "TEX_IMAGE" and node.image:
        return node.image
    for inp in node.inputs:
        img = _upstream_image(inp, depth + 1)
        if img:
            return img
    return None


def material_images(mat):
    """{'color', 'normal', 'roughness', 'metal'} images and flags for a Blender or SourceIO material."""
    out = {"color": None, "normal": None, "roughness": None, "metal": None, "source1": False,
           "translucent": False, "alphatest": False}
    if not mat or not mat.use_nodes:
        return out
    nodes = mat.node_tree.nodes
    by_name = {n.name.lower(): n for n in nodes}
    if "$basetexture" in by_name:  # SourceIO
        out["source1"] = True
        out["color"] = getattr(by_name["$basetexture"], "image", None)
        if "$bumpmap" in by_name:
            out["normal"] = getattr(by_name["$bumpmap"], "image", None)
        flags = {k.lower() for k in mat.keys()} if hasattr(mat, "keys") else set()
        vmt = {}
        if "vmt_parameters" in mat:  # SourceIO keeps the .vmt parameters on the material
            p = mat["vmt_parameters"]
            vmt = {k.lower(): str(v) for k, v in (p.to_dict() if hasattr(p, "to_dict") else dict(p)).items()}
        on = lambda key: vmt.get(key, "0").strip() not in ("0", "", "false")
        out["translucent"] = "$translucent" in by_name or "$translucent" in flags or on("$translucent")
        out["alphatest"] = "$alphatest" in by_name or "$alphatest" in flags or on("$alphatest")
        import re
        m = re.search(r"scale\s+([\d.]+)\s+([\d.]+)", vmt.get("$basetexturetransform", ""))
        if m and (float(m.group(1)), float(m.group(2))) != (1.0, 1.0):
            out["uv_scale"] = (float(m.group(1)), float(m.group(2)))
    bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        out["color"] = out["color"] or _upstream_image(bsdf.inputs.get("Base Color"))
        nrm = bsdf.inputs.get("Normal")
        out["normal"] = out["normal"] or _upstream_image(nrm)
        out["roughness"] = _upstream_image(bsdf.inputs.get("Roughness"))
        out["metal"] = _upstream_image(bsdf.inputs.get("Metallic"))
        out["rough_value"] = float(bsdf.inputs["Roughness"].default_value) if "Roughness" in bsdf.inputs else 0.6
        out["metal_value"] = float(bsdf.inputs["Metallic"].default_value) if "Metallic" in bsdf.inputs else 0.0
        if "Base Color" in bsdf.inputs:
            out["base_value"] = tuple(bsdf.inputs["Base Color"].default_value[:3])
        alpha = bsdf.inputs.get("Alpha")
        if alpha and alpha.is_linked:
            out["alphatest"] = True
    if getattr(mat, "blend_method", "OPAQUE") == "BLEND" or getattr(mat, "surface_render_method", "") == "BLENDED":
        out["translucent"] = True
    if out["color"] is None:
        out["color"] = next((n.image for n in nodes if n.type == "TEX_IMAGE" and n.image), None)
    return out


def save_png(img, path, flip_green=False):
    """Write any Blender image (file, packed, or SourceIO's in-memory VTF) to a PNG."""
    import os
    import numpy as np
    w, h = img.size
    if w == 0 or h == 0:
        return False
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    if flip_green:
        px = px.reshape(-1, 4)
        px[:, 1] = 1.0 - px[:, 1]
        px = px.ravel()
    out = bpy.data.images.new("__hs_export", w, h, alpha=True)
    try:
        out.colorspace_settings.name = img.colorspace_settings.name
    except TypeError:
        pass
    out.pixels.foreach_set(px)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    bpy.data.images.remove(out)
    return True


VMAT = '''"Layer0"
{{
	"shader"	"pbr.vfx"
	"F_USE_NPR_LIGHTING"	"1"
	"F_USE_STATUS_EFFECTS_PROXY"	"1"
{flags}	"TextureColor1"	"{color}"
	"TextureNormal1"	"{normal}"
	"TextureRoughness1"	"{rough}"
	"TextureMetalness1"	"{metal}"
}}
'''


def export_materials(objects, addon_content, hero, skin="swap", model_dir=None):
    """Rename every material on objects to a short unique lowercase name, write its textures as PNGs
    and a Deadlock .vmat into <addon_content>/materials/<model_dir>/<skin>/, and return the remap list
    (DMX material path -> vmat) that build.py writes into the .vmdl. Source 1 normal maps get their
    green channel flipped. Metal, gem and gold materials get a metal/roughness guess."""
    import os
    model_dir = model_dir or f"models/heroes_wip/{hero}"
    rel = f"materials/{model_dir}/{skin}"
    folder = os.path.join(addon_content, *rel.split("/"))
    os.makedirs(folder, exist_ok=True)
    mats = []
    for ob in objects:
        if not any(s.material for s in ob.material_slots):
            # no material at all: give it a plain one, or it compiles as an error material
            m = bpy.data.materials.get(f"{ob.name}_mat") or bpy.data.materials.new(f"{ob.name}_mat")
            if ob.material_slots:
                for s in ob.material_slots:
                    s.material = m
            else:
                ob.data.materials.append(m)
        in_use = {p.material_index for p in ob.data.polygons}
        for i, s in enumerate(ob.material_slots):
            if s.material and s.material not in mats and i in in_use:
                mats.append(s.material)
    used, out = set(), []
    # two passes, so renaming one material never collides with another's current name
    final = {}
    for mat in mats:
        final[mat] = safe_name(mat.name.split(".")[0] if mat.name[-4:-3] == "." and mat.name[-3:].isdigit() else mat.name, used)
    for i, mat in enumerate(mats):
        mat.name = f"__hs_tmp_{i}"
    for mat in mats:
        mat.name = final[mat]
    for mat in mats:
        info = material_images(mat)
        name = mat.name
        files = {}
        for key, suffix, flip in (("color", "color", False), ("normal", "normal", info["source1"]),
                                  ("roughness", "rough", False), ("metal", "metal", False)):
            img = info.get(key)
            if img is not None:
                path = os.path.join(folder, f"{name}_{suffix}.png")
                if save_png(img, path, flip_green=flip):
                    files[key] = f"{rel}/{name}_{suffix}.png"
        low = name.lower()
        shiny = any(w in low for w in ("metal", "gold", "gem", "ring", "armor", "armour", "buckle", "chain"))
        rough = info.get("rough_value", 0.3 if shiny else 0.7)
        metal = info.get("metal_value", 0.8 if shiny else 0.0)
        if "metal_value" not in info and shiny:
            rough, metal = 0.3, 0.8
        flags = ""
        if info["translucent"]:
            flags += '\t"F_TRANSLUCENT"\t"1"\n'
        elif info["alphatest"]:
            flags += '\t"F_ALPHA_TEST"\t"1"\n'
        if info.get("uv_scale"):
            flags += '\t"g_vTexCoordScale"\t"[{:.3f} {:.3f}]"\n'.format(*info["uv_scale"])
        base = info.get("base_value") or tuple(mat.diffuse_color[:3])
        text = VMAT.format(
            flags=flags,
            color=files.get("color") or "[{:.6f} {:.6f} {:.6f} 0.000000]".format(*base),
            normal=files.get("normal", "materials/default/default_normal.tga"),
            rough=files.get("roughness") or f"[{rough:.6f} {rough:.6f} {rough:.6f} 0.000000]",
            metal=files.get("metal") or f"[{metal:.6f} {metal:.6f} {metal:.6f} 0.000000]")
        with open(os.path.join(folder, name + ".vmat"), "w", newline="\n") as f:
            f.write(text)
        out.append({"name": name, "from": f"{model_dir}/materials/{name}.vmat", "to": f"{rel}/{name}.vmat",
                    "textures": files})
    return out
