"""Find the body joints of any humanoid rig and build a bone map between two rigs.

Works from bone names and the hierarchy, so the same code reads a Deadlock hero skeleton,
a Valve biped (ValveBiped.Bip01_*, bip_*), the VA / ReVAmped anthro rig, Mixamo, Rigify-style
Blender rigs (upper_arm.L, shin.R) and most SFM rigs. Nothing is hard-coded per rig: each joint
is found by keywords, then checked against its neighbours (a forearm must sit between the
upper arm and the hand, and so on).
"""
import re

SIDES = ("L", "R")

# tokens that mark helper, control or end bones, never a main body joint
HELPER = {"ik", "fk", "ctrl", "control", "con", "target", "iktarget", "pole", "tip", "end", "nub",
          "offset", "twist", "notwist", "helper", "jnt", "meta", "carpal", "metacarpal", "palm",
          "attach", "aim", "scapula", "track", "properties", "mch", "org", "def_helper", "roll",
          "socket", "prop", "weapon", "gun", "muzzle", "mechanism", "lookat", "look", "switch"}

FINGERS = ("thumb", "index", "middle", "ring", "pinky")
FINGER_WORDS = {
    "thumb": {"thumb"},
    "index": {"index", "pointer", "forefinger"},
    "middle": {"middle", "mid"},
    "ring": {"ring"},
    "pinky": {"pinky", "pinkie", "little", "small"},
}


def tokens(name):
    """'bip_UpperArm_r' -> ['bip', 'upper', 'arm', 'r']; 'LeftFinger11' -> ['left', 'finger', '11']."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", s) if t]


def side(name):
    t = tokens(name)
    if "l" in t or "left" in t:
        return "L"
    if "r" in t or "right" in t:
        return "R"
    return None


class Rig:
    """Bone names, parents and rest positions of one armature, as plain Python data."""

    def __init__(self, names, parents, heads, deform=None):
        self.names = list(names)
        self.parent = dict(parents)
        self.head = dict(heads)
        self.deform = deform or {n: True for n in names}
        self.children = {n: [] for n in names}
        for n, p in self.parent.items():
            if p in self.children:
                self.children[p].append(n)
        self.tok = {n: tokens(n) for n in names}

    @classmethod
    def from_armature(cls, arm):
        mw = arm.matrix_world
        bones = arm.data.bones
        return cls([b.name for b in bones], {b.name: b.parent.name if b.parent else None for b in bones},
                   {b.name: tuple(mw @ b.head_local) for b in bones}, {b.name: b.use_deform for b in bones})

    def depth(self, n):
        d = 0
        while self.parent.get(n):
            n = self.parent[n]
            d += 1
        return d

    def ancestors(self, n):
        out = []
        while self.parent.get(n):
            n = self.parent[n]
            out.append(n)
        return out

    def descendants(self, n):
        out, stack = [], list(self.children.get(n, []))
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children.get(c, []))
        return out

    def is_helper(self, n):
        t = set(self.tok[n])
        return bool(t & HELPER) or not self.deform.get(n, True)

    def has(self, n, *words):
        t = self.tok[n]
        return all(w in t for w in words)

    def any_word(self, n, words):
        return bool(set(self.tok[n]) & set(words))


def _pick(rig, cands, score=None):
    """Best candidate: highest score, then closest to the root, then shortest name."""
    cands = [c for c in cands if c is not None]
    if not cands:
        return None
    return min(cands, key=lambda n: (-(score(n) if score else 0), rig.depth(n), len(n)))


def _sided(rig, s, words, extra=lambda n: True):
    return [n for n in rig.names if side(n) == s and not rig.is_helper(n) and rig.any_word(n, words) and extra(n)]


def _chain_to(rig, end):
    """Bones from the first sided ancestor below the body down to end (a limb chain)."""
    s = side(end)
    chain = [end]
    for a in rig.ancestors(end):
        if side(a) != s:
            break
        chain.append(a)
    return [c for c in reversed(chain) if not rig.is_helper(c)]


def _arm_score_fore(rig, n):
    if rig.any_word(n, {"forearm", "lowerarm", "elbow_lower"}) or (rig.has(n, "lower", "arm")) or rig.has(n, "arm", "lower"):
        return 3
    if rig.any_word(n, {"elbow"}):
        return 1
    return 0


def _arm_score_upper(rig, n):
    if rig.any_word(n, {"upperarm", "uparm", "humerus"}) or rig.has(n, "upper", "arm") or rig.has(n, "arm", "upper") or rig.has(n, "up", "arm"):
        return 3
    if rig.any_word(n, {"shoulder"}) or rig.tok[n][-1:] == ["arm"] or "arm" in rig.tok[n]:
        return 1
    return 0


def _leg_score_calf(rig, n):
    if rig.any_word(n, {"calf", "shin", "lowerleg", "leg_lower", "crus"}) or rig.has(n, "lower", "leg") or rig.has(n, "leg", "lower"):
        return 3
    if rig.any_word(n, {"knee"}):
        return 1
    return 0


def _leg_score_thigh(rig, n):
    if rig.any_word(n, {"thigh", "upleg", "upperleg", "femur"}) or rig.has(n, "upper", "leg") or rig.has(n, "leg", "upper") or rig.has(n, "up", "leg"):
        return 3
    if rig.any_word(n, {"hip"}):
        return 2
    if rig.any_word(n, {"leg"}):
        return 1
    return 0


def _assign(rig, chain, end, scorers):
    """Split a limb chain ending at `end` into the joints named by scorers (from the end upwards)."""
    out = {}
    rest = [c for c in chain if c != end]
    for role, scorer in scorers:
        if not rest:
            break
        best = max(range(len(rest)), key=lambda i: (scorer(rig, rest[i]), i))
        if scorer(rig, rest[best]) == 0:
            best = len(rest) - 1
        out[role] = rest[best]
        rest = rest[:best]
    if rest:
        out["clavicle"] = rest[-1]
    return out


def find_joints(rig):
    """{role: bone name}. Roles: pelvis, spine (list, bottom to top), neck, head, jaw, and per side
    clavicle_S, upperarm_S, forearm_S, hand_S, thigh_S, calf_S, foot_S, toe_S, toe_end_S,
    eye_S, <finger>_<k>_S, plus tail and breast/butt roots when present."""
    j = {}
    for s in SIDES:
        hands = _sided(rig, s, {"hand", "wrist"})
        hand = _pick(rig, hands, lambda n: 2 if rig.any_word(n, {"hand"}) else 1)
        if hand:
            j["hand_" + s] = hand
            arm = _assign(rig, _chain_to(rig, hand), hand, [("forearm", _arm_score_fore), ("upperarm", _arm_score_upper)])
            for role, n in arm.items():
                j[f"{role}_{s}"] = n
        else:
            fore = _pick(rig, _sided(rig, s, {"forearm", "lower", "elbow"}, lambda n: _arm_score_fore(rig, n) >= 3))
            if fore:
                j["forearm_" + s] = fore
                up = rig.parent.get(fore)
                if up and side(up) == s:
                    j["upperarm_" + s] = up

        feet = _sided(rig, s, {"foot", "ankle"})
        foot = _pick(rig, feet)
        if foot:
            j["foot_" + s] = foot
            leg = _assign(rig, _chain_to(rig, foot), foot, [("calf", _leg_score_calf), ("thigh", _leg_score_thigh)])
            leg.pop("clavicle", None)
            for role, n in leg.items():
                j[f"{role}_{s}"] = n
            toes = [c for c in rig.children[foot] if not rig.is_helper(c)]
            toe = _pick(rig, toes, lambda n: 2 if rig.any_word(n, {"toe", "toes", "ball", "toebase"}) else 0)
            if toe:
                j["toe_" + s] = toe
                ends = [c for c in rig.children[toe] if rig.any_word(c, {"end", "nub", "tip"})]
                if ends:
                    j["toe_end_" + s] = _pick(rig, ends)
        else:
            calf = _pick(rig, _sided(rig, s, {"calf", "shin", "knee", "lower", "leg"}, lambda n: _leg_score_calf(rig, n) >= 3))
            if calf:
                j["calf_" + s] = calf
                th = rig.parent.get(calf)
                if th and side(th) == s and not rig.is_helper(th):
                    j["thigh_" + s] = th
            else:
                th = _pick(rig, _sided(rig, s, {"thigh", "leg", "upleg"}, lambda n: _leg_score_thigh(rig, n) >= 3))
                if th:
                    j["thigh_" + s] = th

        if "hand_" + s in j:
            hand = j["hand_" + s]
            under = rig.descendants(hand)
            for f in FINGERS:
                chain = _finger_chain(rig, hand, under, f)
                for k, n in enumerate(chain[:3]):
                    j[f"{f}_{k}_{s}"] = n

        eyes = [n for n in rig.names if side(n) == s and "eye" in rig.tok[n]
                and set(rig.tok[n]) <= {"eye", "eyeball", "ball", "l", "r", "left", "right", "bip", "bip01", "0", "1", "valve", "biped"}]
        if eyes:
            j["eye_" + s] = _pick(rig, eyes)

    # pelvis: the bone both thighs hang off
    th = j.get("thigh_L") or j.get("thigh_R")
    pelvis = None
    if th:
        p = rig.parent.get(th)
        while p and side(p) is not None:
            p = rig.parent.get(p)
        pelvis = p
    if pelvis is None or rig.is_helper(pelvis):
        pelvis = _pick(rig, [n for n in rig.names if side(n) is None and not rig.is_helper(n)
                             and rig.any_word(n, {"pelvis", "hips", "hip", "cog"})],
                       lambda n: 2 if rig.any_word(n, {"pelvis"}) else 1) or pelvis
    if pelvis:
        j["pelvis"] = pelvis

    unsided = [n for n in rig.names if side(n) is None and not rig.is_helper(n)]
    neck = _pick(rig, [n for n in unsided if rig.any_word(n, {"neck"})])
    if neck:
        j["neck"] = neck
        head = _pick(rig, [n for n in rig.descendants(neck) if side(n) is None and not rig.is_helper(n)
                           and rig.any_word(n, {"head"})])
        if head:
            j["head"] = head
    else:
        head = _pick(rig, [n for n in unsided if rig.any_word(n, {"head"})])
        if head:
            j["head"] = head

    spine = []
    top = neck or j.get("head")
    if top:
        for a in rig.ancestors(top):
            if a == pelvis or side(a) is not None or rig.is_helper(a):
                break
            spine.append(a)
        spine.reverse()
    if not spine:
        spine = sorted([n for n in unsided if rig.any_word(n, {"spine", "chest", "torso", "abdomen", "back"})], key=rig.depth)
    if spine:
        j["spine"] = spine

    base = j.get("head") or pelvis
    if base:
        jaw = _pick(rig, [n for n in rig.descendants(base) if side(n) is None and rig.any_word(n, {"jaw", "javv", "jawbone"})
                          and not rig.any_word(n, {"line", "end", "lower"})])
        if jaw:
            j["jaw"] = jaw

    tails = [n for n in rig.names if side(n) is None and rig.any_word(n, {"tail", "tailbase"})]
    if tails:
        roots = [n for n in tails if not any(a in tails for a in rig.ancestors(n))]
        j["tail_roots"] = sorted(roots, key=rig.depth)
    return j


def _finger_kind(rig, n):
    t = rig.tok[n]
    for f, words in FINGER_WORDS.items():
        if set(t) & words:
            if f == "middle" and "mid" in t and "finger" not in t and "f" not in t:
                continue  # 'mid' alone is too vague (midToe, brow_mid)
            return f, None
    # VA / ReVAmped style: Finger11 = thumb joint 1, Finger53 = pinky joint 3
    for i, tk in enumerate(t):
        if tk == "finger" and i + 1 < len(t) and t[i + 1].isdigit():
            d = t[i + 1]
            if len(d) >= 1 and d[0] in "12345":
                return FINGERS[int(d[0]) - 1], (int(d[1]) - 1 if len(d) > 1 else None)
            if len(d) == 1 and d in "01234":
                return FINGERS[int(d)], None
    return None, None


def _finger_chain(rig, hand, under, kind):
    cands = [n for n in under if side(n) == side(hand) and not rig.any_word(n, {"end", "nub", "tip", "meta", "carpal", "metacarpal"})
             and _finger_kind(rig, n)[0] == kind]
    if not cands:
        return []
    start = min(cands, key=lambda n: (rig.depth(n), n))
    chain = [start]
    while True:
        nxt = [c for c in rig.children[chain[-1]] if c in cands]
        if not nxt:
            break
        chain.append(min(nxt, key=len))
    return chain


# --- bone map -------------------------------------------------------------------------------

LIMB_SEGMENTS = [  # (from role, to role, mode)
    ("clavicle", "upperarm", "move"),
    ("upperarm", "forearm", None),
    ("forearm", "hand", None),
    ("hand", "middle_0", None),
    ("thigh", "calf", None),
    ("calf", "foot", None),
    ("foot", "toe", None),
    ("toe", "toe_end", None),
]


def _polyline(points):
    import math
    d = [0.0]
    for a, b in zip(points, points[1:]):
        d.append(d[-1] + math.dist(a, b))
    tot = d[-1] or 1.0
    return [x / tot for x in d]


def _spine_target(names, fracs, f):
    """A joint on the target spine at fraction f, as 'a..b@t' (or a plain name at a joint)."""
    for i in range(len(names) - 1):
        a, b = fracs[i], fracs[i + 1]
        if f <= b + 1e-6:
            t = 0.0 if b - a < 1e-9 else (f - a) / (b - a)
            if t < 0.02:
                return names[i]
            if t > 0.98:
                return names[i + 1]
            return f"{names[i]}..{names[i + 1]}@{t:.3f}"
    return names[-1]


def build_bonemap(src, dst, src_joints=None, dst_joints=None):
    """Bone map (same JSON layout as bonemap_va_anthro_to_deadlock.json) from rig src onto rig dst.

    Spine and clavicles only slide (keep the character's torso shape); limbs are posed onto the
    target segments; neck, head, jaw, eyes, fingers, twist and helper bones ride with their parent.
    Bones neither rig names are weighted to the nearest mapped ancestor (or nearest mapped bone)."""
    sj = src_joints or find_joints(src)
    dj = dst_joints or find_joints(dst)
    segments, terminals, groups = [], [], {}

    # spine: spread the source joints over the target chain by length
    s_chain = sj.get("spine", []) + ([sj["neck"]] if "neck" in sj else [])
    d_chain = dj.get("spine", []) + ([dj["neck"]] if "neck" in dj else [])
    if len(s_chain) >= 2 and len(d_chain) >= 2:
        sf = _polyline([src.head[n] for n in s_chain])
        df = _polyline([dst.head[n] for n in d_chain])
        targets = [_spine_target(d_chain, df, f) for f in sf]
        for i in range(len(s_chain) - 1):
            segments.append([s_chain[i], s_chain[i + 1], targets[i], targets[i + 1], "move"])
        for i, n in enumerate(s_chain[:-1]):
            k = max(j for j in range(len(df) - 1) if df[j] <= sf[i] + 1e-6) if len(df) > 1 else 0
            groups[n] = d_chain[min(k, len(d_chain) - 2)]
    elif s_chain and d_chain:
        groups[s_chain[0]] = d_chain[0]
    for role in ("neck", "head", "jaw", "pelvis"):
        if role in sj and role in dj:
            groups[sj[role]] = dj[role]
    if "pelvis" in sj and "pelvis" in dj:
        terminals.append([sj["pelvis"], dj["pelvis"]])

    for s in SIDES:
        for a, b, mode in LIMB_SEGMENTS:
            ka, kb = f"{a}_{s}", f"{b}_{s}"
            if ka in sj and kb in sj and ka in dj and kb in dj:
                seg = [sj[ka], sj[kb], dj[ka], dj[kb]]
                if mode:
                    seg.append(mode)
                segments.append(seg)
        for role in ("clavicle", "upperarm", "forearm", "hand", "thigh", "calf", "foot", "toe", "eye"):
            k = f"{role}_{s}"
            if k in sj and k in dj:
                groups[sj[k]] = dj[k]
        # a last joint with nothing after it on either rig: only move and turn with its parent
        for role in ("toe", "hand", "foot", "calf", "forearm"):
            k = f"{role}_{s}"
            if k in sj and k in dj and not any(seg[0] == sj[k] for seg in segments):
                terminals.append([sj[k], dj[k]])
        for f in FINGERS:
            for i in range(3):
                k = f"{f}_{i}_{s}"
                if k in sj:
                    d = dj.get(k) or dj.get(f"{f}_{min(i, 2)}_{s}") or dj.get(f"hand_{s}")
                    if d:
                        groups[sj[k]] = d
        if f"toe_end_{s}" in sj and f"toe_{s}" in dj:
            groups[sj[f"toe_end_{s}"]] = dj[f"toe_{s}"]

    torso = list(sj.get("spine", [])) + [sj[f"clavicle_{s}"] for s in SIDES if f"clavicle_{s}" in sj]
    stop = [sj[r] for r in ("neck", "head") if r in sj] + list(sj.get("tail_roots", []))

    has_feet = all(f"toe_{s}" in sj or f"foot_{s}" in sj for s in SIDES) and ("toe_L" in dj or "foot_L" in dj)
    up_s, up_d = sj.get("upperarm_L"), dj.get("upperarm_L")
    out = {
        "_comment": "Made by Hero Swap Tools > Auto-detect Bone Map. Edit freely; see the README for the format.",
        "segments": segments,
        "terminals": terminals,
        "groups": groups,
        "torso": torso,
        "torso_stop": stop,
        "torso_smoothing": 5.0,
        "pelvis": [sj.get("pelvis"), dj.get("pelvis")],
        "side_bones": [sj.get("thigh_L") or sj.get("upperarm_L"), sj.get("thigh_R") or sj.get("upperarm_R")],
        "fallback": "nearest",
    }
    if "neck" in sj and "neck" in dj and all(f"upperarm_{s}" in sj and f"upperarm_{s}" in dj for s in SIDES):
        out["head_anchor"] = {"neck": sj["neck"], "dst_neck": dj["neck"],
                              "src_shoulders": [sj["upperarm_L"], sj["upperarm_R"]],
                              "dst_shoulders": [dj["upperarm_L"], dj["upperarm_R"]]}
    if has_feet and up_s and up_d:
        out["height_bones"] = [up_s, up_d, dj.get("toe_L") or dj.get("foot_L")]
        out["ground_bones"] = [sj[k] for k in ("toe_L", "toe_R", "foot_L", "foot_R") if k in sj]
    elif up_s and up_d and "pelvis" in sj and "pelvis" in dj:
        # no feet on the source (an outfit): scale by pelvis-to-shoulder height, line pelvises up
        out["scale_bones"] = [[sj["pelvis"], up_s], [dj["pelvis"], up_d]]
    return out, sj, dj
