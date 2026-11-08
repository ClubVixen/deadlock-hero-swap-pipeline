"""Local build of a Deadlock hero swap: hero extraction, .vmdl rewrite, compile, VPK pack and install.

Plain Python (no bpy), so it runs from the Blender add-on and from tools/build_mod.py alike.
Nothing here uses the network: every step calls the local Source 2 Viewer CLI, the Reduced
CSDK and CS2 Workshop Tools resource compilers.

Config keys (all paths absolute, or relative to work_root):
    hero               model folder name, e.g. "abrams"     (models/heroes_wip/abrams/abrams.vmdl)
    model_dir          optional, default "models/heroes_wip/<hero>"
    addon              addon name, e.g. "abrams_miruko"
    deadlock           Deadlock install folder (has game/citadel)
    csdk               Reduced CSDK folder (has game/bin/win64/resourcecompiler.exe)
    cs2                Counter-Strike Global Offensive folder (CS2 Workshop Tools installed)
    vrf_cli            Source2Viewer-CLI.exe
    work_root          where the hero extraction and VPK staging go
    mesh_name          name of the new render mesh, default "<hero>_swap"
    dmx                the exported ModelDoc DMX
    remove_meshes      hero render meshes to drop (default: guessed, see guess_body_meshes)
    jiggle             JiggleBoneList text (optional)
    pak                pak number for the installed addon, e.g. 31 -> pak31_dir.vpk
"""
import json
import os
import re
import shutil
import struct
import subprocess
import zlib

from . import kv3

KEEP_WORDS = ("gun", "weapon", "book", "briefcase", "frog", "baton", "blade", "sword", "staff", "shield",
              "bow", "arrow", "knife", "dagger", "hammer", "card", "bat", "wand", "prop", "fx")
BODY_WORDS = ("body", "head", "face", "hair", "coat", "jacket", "scarf", "model", "cloak", "cape", "skirt",
              "teeth", "eyes", "glow", "hornglow", "lower", "upper")


def log(msg, sink=None):
    print(msg)
    if sink:
        sink(msg)


def _run(cmd, sink=None, cwd=None):
    log("> " + " ".join(f'"{c}"' if " " in c else c for c in cmd), sink)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-15:])
    if tail:
        log(tail, sink)
    return p.returncode, out


# --- heroes ------------------------------------------------------------------------------------

def list_heroes(vrf_cli, deadlock, cache_dir):
    """[(hero key, model path)] read from scripts/heroes.vdata in the game's pak01."""
    path = os.path.join(cache_dir, "heroes.vdata")
    if not os.path.isfile(path):
        os.makedirs(cache_dir, exist_ok=True)
        _run([vrf_cli, "-i", os.path.join(deadlock, "game", "citadel", "pak01_dir.vpk"), "-f", "scripts/heroes.vdata_c",
              "-d", "-o", path])
    text = open(path, encoding="utf-8", errors="replace").read()
    names = hero_names(deadlock)
    out = []
    for m in re.finditer(r'^\t(hero_\w+)\s*=\s*\n\t\{(.*?)^\t\}', text, re.M | re.S):
        mm = re.search(r'm_strModelName\s*=\s*resource_name:"([^"]+)"', m.group(2))
        if mm:
            out.append((m.group(1), mm.group(1), names.get(m.group(1), "")))
    return sorted(out, key=lambda x: (not x[2], (x[2] or x[0]).lower()))


def hero_names(deadlock, language="english"):
    """{hero key: display name}, e.g. hero_atlas -> Abrams, from the game's localization files."""
    path = os.path.join(deadlock, "game", "citadel", "resource", "localization", "citadel_gc_hero_names",
                        f"citadel_gc_hero_names_{language}.txt")
    if not os.path.isfile(path):
        return {}
    text = open(path, encoding="utf-8", errors="replace").read()
    return {k: v for k, v in re.findall(r'"(hero_\w+?):n"\s+"([^"]*)"', text) if not k.endswith("_search")}


def extract_hero(vrf_cli, deadlock, model_path, out_root, sink=None):
    """Decompile the hero model (vmdl + every DMX it uses) into out_root/<model folder>/."""
    rel = model_path.replace("\\", "/")
    if rel.endswith(".vmdl"):
        rel += "_c"
    dst = os.path.join(out_root, os.path.dirname(rel), os.path.basename(rel)[:-2])
    if not os.path.isfile(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        code, _ = _run([vrf_cli, "-i", os.path.join(deadlock, "game", "citadel", "pak01_dir.vpk"), "-f", rel, "-d", "-o", dst], sink)
        if code != 0 or not os.path.isfile(dst):
            raise RuntimeError(f"Source 2 Viewer could not extract {rel}")
    return dst


def render_meshes(vmdl_path):
    _, root = kv3.loads(open(vmdl_path, encoding="utf-8").read())
    return [(kv3.unq(n["name"]), kv3.unq(n["filename"])) for n in kv3.find(root, "RenderMeshFile")]


def guess_body_meshes(names):
    """The hero's own body meshes (to replace), from the LOD0 render mesh names: everything that
    isn't a weapon or ability prop."""
    out = []
    for n in names:
        if re.search(r"_lod\d+$", n):
            continue
        low = n.lower()
        if any(w in low for w in KEEP_WORDS) and not any(w in low for w in ("body",)):
            continue
        out.append(n)
    return out


def body_dmx(vmdl_path):
    """The main body DMX of a decompiled hero (to import its skeleton from)."""
    folder = os.path.dirname(vmdl_path)
    meshes = render_meshes(vmdl_path)
    body = guess_body_meshes([n for n, _ in meshes])
    for name, fn in meshes:
        if name in body:
            p = os.path.join(folder, os.path.basename(fn))
            if os.path.isfile(p):
                return p
    return None


# --- vmdl --------------------------------------------------------------------------------------

def _lod_base(name):
    return re.sub(r"_lod\d+$", "", name)


def rewrite_vmdl(src_vmdl, out_vmdl, mesh_name, dmx_rel, remove, remaps=None, jiggle_text=None):
    """Swap the hero's body meshes for ours, keep LOD 0 only, add material remaps and jiggle bones."""
    with open(src_vmdl, encoding="utf-8") as source:
        header, root = kv3.loads(source.read())
    top = root["rootNode"]["children"]
    remove = set(remove)

    def keep(name):
        return not re.search(r"_lod\d+$", name) and _lod_base(name) not in remove

    rml = kv3.find(root, "RenderMeshList")[0]
    kept = [c for c in rml["children"] if keep(kv3.unq(c["name"]))]
    ours = {"_class": kv3.q("RenderMeshFile"), "name": kv3.q(mesh_name), "filename": kv3.q(dmx_rel)}
    rml["children"] = [ours] + kept

    # A combined replacement mesh can belong to only one body group. Replacing
    # both body and face groups with it makes the compiler reject duplicate refs.
    owner = next((g for g in kv3.find(root, "BodyGroup")
                  if any(_lod_base(kv3.unq(n)) in remove
                         for c in kv3.find(g, "BodyGroupChoice") for n in c.get("meshes", []))), None)
    owner_choices = {id(c) for c in kv3.find(owner, "BodyGroupChoice")} if owner else set()
    for choice in kv3.find(root, "BodyGroupChoice"):
        names = [kv3.unq(m) for m in choice.get("meshes", [])]
        had_body = any(_lod_base(n) in remove for n in names)
        new = [n for n in names if keep(n)]
        if had_body and (owner is None or id(choice) in owner_choices) and mesh_name not in new:
            new.insert(0, mesh_name)
        choice["meshes"] = [kv3.q(n) for n in new]

    for lodlist in kv3.find(root, "LODGroupList"):
        groups = lodlist.get("children", [])
        if groups:
            first = min(groups, key=lambda g: float(kv3.unq(g.get("switch_threshold", "0"))))
            refs = [kv3.unq(m["mesh_name"]) for m in first.get("mesh_references", [])]
            refs = [n for n in refs if keep(n)]
            if mesh_name not in refs:
                refs.insert(0, mesh_name)
            first["mesh_references"] = [{"mesh_name": kv3.q(n)} for n in refs]
            lodlist["children"] = [first]

    top[:] = [c for c in top if kv3.unq(c.get("_class", "")) not in ("MaterialGroupList", "JiggleBoneList")]
    at = next((i for i, c in enumerate(top) if kv3.unq(c["_class"]) == "LODGroupList"), 3) + 1
    if remaps:
        mg = {"_class": kv3.q("MaterialGroupList"), "children": [{
            "_class": kv3.q("DefaultMaterialGroup"),
            "remaps": [{"from": kv3.q(a), "to": kv3.q(b)} for a, b in remaps],
            "use_global_default": kv3.Raw("false"),
            "global_default_material": kv3.q(""),
        }]}
        top.insert(at, mg)
        at += 1
    if jiggle_text:
        _, jig = kv3.loads("{ j = " + jiggle_text + " }")
        top.insert(at, jig["j"])
    os.makedirs(os.path.dirname(out_vmdl), exist_ok=True)
    with open(out_vmdl, "w", encoding="utf-8", newline="\n") as f:
        f.write(kv3.dumps(header, root))
    return [kv3.unq(c["name"]) for c in rml["children"]]


def dmx_materials(path):
    """Material paths named in a binary or text DMX (for compile-time stubs)."""
    data = open(path, "rb").read()
    found = set()
    for m in re.finditer(rb"[A-Za-z0-9_/\-\.]*materials/[A-Za-z0-9_/\-\.]+", data):
        s = m.group().decode(errors="ignore")
        if s.endswith(".vmat"):
            s = s[:-5]
        found.add(s)
    return sorted(found)


# --- vpk ---------------------------------------------------------------------------------------

def pack_vpk(root, out):
    """Pack a folder into a single-file VPK v2 (every file in the _dir.vpk, archive index 0x7fff)."""
    tree = {}
    for dirpath, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            path, name = rel.rsplit("/", 1) if "/" in rel else (" ", rel)
            name, ext = name.rsplit(".", 1) if "." in name else (name, " ")
            tree.setdefault(ext, {}).setdefault(path, []).append((name, full))
    entries, data, offset = bytearray(), bytearray(), 0
    for ext in sorted(tree):
        entries += ext.encode() + b"\0"
        for path in sorted(tree[ext]):
            entries += path.encode() + b"\0"
            for name, full in sorted(tree[ext][path]):
                blob = open(full, "rb").read()
                entries += name.encode() + b"\0"
                entries += struct.pack("<IHHII", zlib.crc32(blob) & 0xFFFFFFFF, 0, 0x7FFF, offset, len(blob))
                entries += struct.pack("<H", 0xFFFF)
                data += blob
                offset += len(blob)
            entries += b"\0"
        entries += b"\0"
    entries += b"\0"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(struct.pack("<IIIIIII", 0x55AA1234, 2, len(entries), len(data), 0, 0, 0))
        f.write(entries)
        f.write(data)
    return sum(len(n) for p in tree.values() for n in p.values())


# --- the whole build ---------------------------------------------------------------------------

def merge_constraints(document, constraints):
    """Keep one AnimConstraintList: the compiler ignores later duplicate lists."""
    children = document["rootNode"]["children"]
    existing = next((node for node in children
                     if kv3.unq(node.get("_class", "")) == "AnimConstraintList"), None)
    if existing is None:
        children.append(constraints)
    else:
        existing.setdefault("children", []).extend(constraints.get("children", []))


def build(cfg, sink=None):
    """Run every local step. Returns the installed VPK path."""
    hero = cfg["hero"]
    model_dir = cfg.get("model_dir") or f"models/heroes_wip/{hero}"
    addon = cfg["addon"]
    work = cfg["work_root"]
    csdk, cs2, deadlock = cfg["csdk"], cfg["cs2"], cfg["deadlock"]
    mesh_name = cfg.get("mesh_name") or f"{hero}_swap"

    # 1. the decompiled hero
    src_root = os.path.join(work, f"{hero}_src")
    src_vmdl = extract_hero(cfg["vrf_cli"], deadlock, f"{model_dir}/{hero}.vmdl", src_root, sink)
    src_dir = os.path.dirname(src_vmdl)
    names = [n for n, _ in render_meshes(src_vmdl)]
    remove = cfg.get("remove_meshes") or guess_body_meshes(names)
    log(f"hero meshes: {names}\nreplacing: {remove}", sink)

    # 2. CS2 addon content: hero files + our DMX + the edited vmdl
    cs2_content = os.path.join(cs2, "content", "csgo_addons", addon)
    cs2_game = os.path.join(cs2, "game", "csgo_addons", addon)
    os.makedirs(cs2_game, exist_ok=True)
    cs2_model = os.path.join(cs2_content, *model_dir.split("/"))
    os.makedirs(os.path.join(cs2_model, "dmx"), exist_ok=True)
    dropped = {fn for n, fn in render_meshes(src_vmdl) if re.search(r"_lod\d+$", n) or _lod_base(n) in remove}
    for f in os.listdir(src_dir):
        if f.lower().endswith(".dmx") and f"{model_dir}/{f}" not in dropped:
            dst = os.path.join(cs2_model, f)
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(os.path.join(src_dir, f)):
                shutil.copy2(os.path.join(src_dir, f), dst)
    dmx_name = os.path.basename(cfg["dmx"])
    shutil.copy2(cfg["dmx"], os.path.join(cs2_model, "dmx", dmx_name))
    dmx_rel = f"{model_dir}/dmx/{dmx_name}"

    # material remaps: DMX material path -> our compiled vmat
    manifest = json.load(open(cfg["materials_manifest"])) if cfg.get("materials_manifest") else {}
    remaps = [(m["from"], m["to"]) for m in manifest.get("materials", [])]
    jiggle = open(cfg["jiggle"], encoding="utf-8").read() if cfg.get("jiggle") and os.path.isfile(cfg["jiggle"]) else None
    out_vmdl = os.path.join(cs2_model, f"{hero}.vmdl")
    meshes = rewrite_vmdl(src_vmdl, out_vmdl, mesh_name, dmx_rel, remove, remaps, jiggle)
    if cfg.get("proportion_constraints"):
        header, document = kv3.loads(open(out_vmdl, encoding="utf-8").read())
        _, constraints = kv3.loads(open(cfg["proportion_constraints"], encoding="utf-8").read())
        merge_constraints(document, constraints)
        with open(out_vmdl, "w", encoding="utf-8") as f:
            f.write(kv3.dumps(header, document))
    log(f"wrote {out_vmdl} with render meshes {meshes}", sink)

    # stub materials so CS2 can compile (it has no Deadlock pbr shader); the game uses the real ones
    stubs = {b[:-5] if b.endswith(".vmat") else b for _, b in remaps}
    for fn in [f for n, f in render_meshes(out_vmdl)]:
        p = os.path.join(cs2_content, *fn.split("/"))
        if os.path.isfile(p) and not fn.endswith(dmx_name):
            stubs.update(dmx_materials(p))
    for s in stubs:
        p = os.path.join(cs2_content, *s.split("/")) + ".vmat"
        if not os.path.isfile(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write('"Layer0"\n{\n\t"shader"\t"csgo_complex.vfx"\n}\n')

    # 3. materials with the CSDK
    csdk_rc = os.path.join(csdk, "game", "bin", "win64", "resourcecompiler.exe")
    csdk_content = os.path.join(csdk, "content", "citadel_addons", addon)
    csdk_game_addon = os.path.join(csdk, "game", "citadel_addons", addon)
    os.makedirs(csdk_game_addon, exist_ok=True)
    vmats = [os.path.join(csdk_content, *m["to"].split("/")) for m in manifest.get("materials", [])]
    for v in vmats:
        code, out = _run([csdk_rc, "-danger_mode_ignore_schema_mismatches", "-game", os.path.join(csdk, "game", "citadel"),
                          "-addon", addon, "-i", v], sink)
        if code != 0:
            raise RuntimeError(f"CSDK could not compile {v}")

    # 4. the model with CS2 Workshop Tools (AnimGraph 2 aware)
    cs2_rc = os.path.join(cs2, "game", "bin", "win64", "resourcecompiler.exe")
    code, out = _run([cs2_rc, "-game", os.path.join(cs2, "game", "csgo"), "-addon", addon, "-i", out_vmdl], sink)
    vmdl_c = os.path.join(cs2_game, *model_dir.split("/"), f"{hero}.vmdl_c")
    if code != 0 or not os.path.isfile(vmdl_c):
        raise RuntimeError("CS2 resourcecompiler failed on the model, see the log")

    # 5. stage and pack
    stage = os.path.join(work, f"vpk_{addon}")
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    mdst = os.path.join(stage, *model_dir.split("/"))
    os.makedirs(mdst)
    shutil.copy2(vmdl_c, mdst)
    morph_dir = os.path.join(cs2_game, *model_dir.split("/"), hero)
    if os.path.isdir(morph_dir):
        for f in os.listdir(morph_dir):
            if f.endswith("_vmorf.vtex_c") and f.startswith(os.path.splitext(dmx_name)[0]):
                os.makedirs(os.path.join(mdst, hero), exist_ok=True)
                shutil.copy2(os.path.join(morph_dir, f), os.path.join(mdst, hero, f))
    csdk_mats = os.path.join(csdk_game_addon, "materials")
    if os.path.isdir(csdk_mats):
        shutil.copytree(csdk_mats, os.path.join(stage, "materials"), ignore=shutil.ignore_patterns("default"))
        dflt = os.path.join(stage, "materials", "default")
        if os.path.isdir(dflt):
            shutil.rmtree(dflt)
    pak = int(cfg.get("pak", 30))
    vpk = os.path.join(work, f"pak{pak:02d}_dir.vpk")
    n = pack_vpk(stage, vpk)
    log(f"packed {n} files into {vpk}", sink)

    addons = os.path.join(deadlock, "game", "citadel", "addons")
    if cfg.get("install", True) and os.path.isdir(addons):
        shutil.copy2(vpk, os.path.join(addons, os.path.basename(vpk)))
        log(f"installed {os.path.join(addons, os.path.basename(vpk))}", sink)
        return os.path.join(addons, os.path.basename(vpk))
    return vpk
