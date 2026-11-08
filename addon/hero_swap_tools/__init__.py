"""Hero Swap Tools: put any SFM or Blender character onto a Deadlock hero, from the 3D Viewport sidebar."""
import json
import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import build, core, rigmap

bl_info = {
    "name": "Hero Swap Tools",
    "author": "ClubVixen",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Hero Swap",
    "description": "Fit any SFM or Blender character (and outfits made for other bodies) onto a Deadlock hero, export ModelDoc DMX and build the mod locally",
    "category": "Rigging",
}

HERE = os.path.dirname(__file__)
DEFAULT_BONEMAP = os.path.join(HERE, "bonemap_va_anthro_to_deadlock.json")
DEFAULT_JIGGLE = os.path.join(HERE, "jiggle_presets.json")


# --- settings ----------------------------------------------------------------------------------

class HeroSwapPrefs(bpy.types.AddonPreferences):
    bl_idname = __package__
    deadlock_dir: StringProperty(name="Deadlock", subtype="DIR_PATH",
                                 default=r"C:\Program Files (x86)\Steam\steamapps\common\Deadlock",
                                 description="The Deadlock install folder (has game/citadel)")
    csdk_dir: StringProperty(name="Reduced CSDK", subtype="DIR_PATH",
                             description="The Reduced CSDK 12 folder (has game/bin/win64/resourcecompiler.exe)")
    cs2_dir: StringProperty(name="CS2", subtype="DIR_PATH",
                            default=r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive",
                            description="Counter-Strike 2 with Workshop Tools installed, used to compile the AnimGraph 2 model")
    vrf_cli: StringProperty(name="Source 2 Viewer CLI", subtype="FILE_PATH",
                            description="Source2Viewer-CLI.exe, used to extract the hero")
    work_dir: StringProperty(name="Work Folder", subtype="DIR_PATH",
                             description="Where hero extractions, staging folders and built VPKs go")

    def draw(self, context):
        for p in ("deadlock_dir", "csdk_dir", "cs2_dir", "vrf_cli", "work_dir"):
            self.layout.prop(self, p)


def prefs():
    return bpy.context.preferences.addons[__package__].preferences


def is_armature(_self, ob):
    return ob.type == "ARMATURE"


def is_mesh(_self, ob):
    return ob.type == "MESH"


class BoneMapRow(bpy.types.PropertyGroup):
    role: StringProperty(name="Role")
    src: StringProperty(name="Source Bone")
    dst: StringProperty(name="Target Bone")


_HERO_ITEMS = []


def hero_items(_self, context):
    global _HERO_ITEMS
    if not _HERO_ITEMS:
        try:
            p = prefs()
            heroes = build.list_heroes(bpy.path.abspath(p.vrf_cli), bpy.path.abspath(p.deadlock_dir),
                                       os.path.join(bpy.path.abspath(p.work_dir), "ref"))
            _HERO_ITEMS = [(m, f"{n or k}  ({os.path.basename(m)[:-5]})", f"{k}: {m}") for k, m, n in heroes]
        except Exception:
            _HERO_ITEMS = []
    return _HERO_ITEMS or [("", "(set the tool paths, then Refresh)", "")]


def hero_changed(self, _context):
    if self.hero_choice:
        self.model_dir = os.path.dirname(self.hero_choice).replace("\\", "/")
        self.hero = os.path.basename(self.hero_choice)[:-5]


class HeroSwapSettings(bpy.types.PropertyGroup):
    # hero
    hero_choice: EnumProperty(name="Hero", items=hero_items, update=hero_changed,
                              description="Deadlock hero to replace (read from the game's heroes.vdata)")
    hero: StringProperty(name="Hero Model", description="The hero's model name, e.g. abrams, pocket, unicorn (Celeste)")
    model_dir: StringProperty(name="Model Folder", default="models/heroes_wip/abrams")
    hero_rig: PointerProperty(name="Hero Skeleton", type=bpy.types.Object, poll=is_armature,
                              description="The Deadlock hero skeleton (imported from the hero's body DMX)")
    # character
    character_rig: PointerProperty(name="Character Rig", type=bpy.types.Object, poll=is_armature,
                                   description="The character's armature")
    source_materials: StringProperty(name="Materials Folder", subtype="DIR_PATH",
                                     description="The model's materials folder (has the .vmt/.vtf files), for Load Source Textures")
    delete_materials: StringProperty(name="Delete Materials", default="genitals",
                                     description="Comma-separated material names whose faces are deleted (NSFW parts, hidden extras)")
    eye_texture_dir: StringProperty(name="Eye Textures", subtype="DIR_PATH",
                                    description="Where the baked eye PNGs go (default: the export materials folder)")
    # outfit
    shape_mesh: PointerProperty(name="Shape Mesh", type=bpy.types.Object, poll=is_mesh,
                               description="Body or outfit to adjust independently; defaults to Body Mesh")
    shape_filter: StringProperty(name="Shape Filter", default="breast",
                                 description="Show matching source shape sliders; empty shows all")
    alignment_reference: PointerProperty(name="Alignment Skin", type=bpy.types.Object, poll=is_mesh,
                                        description="Complete skin mesh for matching weighted clothing regions; choose the arms for gloves")
    alignment_patterns: StringProperty(name="Bone Patterns", default="*Hand*,*Palm*,*Finger*,*Forearm*",
                                       description="Comma-separated bone-name patterns to align by weighted region centers")
    alignment_strength: FloatProperty(name="Alignment Strength", default=1.0, min=0.0, max=1.0)
    alignment_scale: FloatProperty(name="Region Scale", default=1.0, min=0.1, max=3.0,
                                   description="Resize each selected weighted region around its center; 1 keeps its size")
    outfit_rig: PointerProperty(name="Outfit Rig", type=bpy.types.Object, poll=is_armature,
                                description="The outfit's own armature, when it was made for a different body/rig")
    outfit_base_body: PointerProperty(name="Original Body", type=bpy.types.Object, poll=is_mesh,
                                      description="Optional: the body the outfit was made for, bone-merged onto the Outfit Rig. Gives the best fit: every outfit point keeps its height above the skin")
    body_mesh: PointerProperty(name="Body Mesh", type=bpy.types.Object, poll=is_mesh,
                               description="The character's skin mesh (shape keys, weights and the surface the outfit sits on)")
    outfit_reach: FloatProperty(name="Reach", default=0.0, min=0.0,
                                description="How far from the skin outfit points count as worn on it (0 = 4% of the body height)")
    outfit_gap: FloatProperty(name="Gap", default=0.0, min=0.0,
                              description="Smallest height of the outfit above the skin (0 = 0.5% of the body height)")
    copy_weights: BoolProperty(name="Copy Body Weights", default=True,
                               description="Outfit points near the skin take the skin's weights, so tight clothes bend with it")
    outfit_prefix: StringProperty(name="Outfit Prefix", default="",
                                  description="Name prefix of the outfit pieces, used when none are selected (Fit Outfit to Character sets it)")
    skin_cover: FloatProperty(name="Cover Distance", default=0.0, min=0.0,
                              description="How far outside the skin the outfit may sit and still count as covering it (0 = 2% of the body height)")
    skin_swaps: StringProperty(name="Skin Swaps", default="nmm_outfit_sleeves=nmm_outfit_sleeves_textured",
                               description="Comma-separated old=new material names")
    create_missing: BoolProperty(name="Create Missing Keys", default=True,
                                 description="Copy body shape keys the outfit lacks, from the nearest body surface")
    max_distance: FloatProperty(name="Max Distance", default=0.0, min=0.0,
                                description="Ignore outfit vertices farther than this from the body (0 = no limit)")
    # bone map
    bonemap: StringProperty(name="Bone Map", subtype="FILE_PATH", default="",
                            description="Bone map JSON. Empty: Auto-detect writes one next to the .blend")
    rows: CollectionProperty(type=BoneMapRow)
    map_side: EnumProperty(name="Show", items=[("SRC", "Character bones", "The character's bone for each joint"),
                                              ("DST", "Hero bones", "The hero's bone for each joint")], default="SRC")
    row_index: IntProperty()
    # fit
    preserve_proportions: BoolProperty(name="Preserve Source Proportions (Experimental)", default=False,
                                      description="Keep source bone lengths on a separate deformation rig driven by hero rotations. Requires in-game validation of contacts and physics")
    preserve_torso: BoolProperty(name="Preserve Torso Proportions", default=False,
                                description="Experimental: keep the pelvis-to-neck shape with one rigid offset. Align limbs to the hero; does not retarget the game's animation clips")
    keep_head: BoolProperty(name="Keep Head Proportions", default=True,
                            description="Keep the character's own neck length above the shoulders instead of putting the neck on the hero's neck joint (heroes with low, hunched heads like Abrams would sink the head into the torso)")
    keep_jiggle: BoolProperty(name="Keep Jiggle Bones", default=True,
                              description="Keep hair/tail/chest/butt/ear chains as bones on the hero skeleton so they can get physics")
    jiggle_presets: StringProperty(name="Jiggle Presets", subtype="FILE_PATH", default=DEFAULT_JIGGLE,
                                   description="JSON with the bone patterns to keep and their ModelDoc jiggle settings")
    tail_root: StringProperty(name="Tail Root", default="",
                              description="First bone of a chain that came out sideways after the fit (filled in by the fit)")
    # export + build
    export_collection: PointerProperty(name="Export Collection", type=bpy.types.Collection,
                                       description="The fitted meshes (made by the fit, named <hero>_swap)")
    export_dir: StringProperty(name="Export Folder", subtype="DIR_PATH",
                               description="Empty: <CSDK>/content/citadel_addons/<addon>/<model folder>/dmx")
    addon_name: StringProperty(name="Addon Name", default="", description="Name of the mod's addon folders, e.g. abrams_miruko")
    skin_name: StringProperty(name="Material Folder", default="swap", description="Sub-folder for this skin's materials")
    pak: IntProperty(name="Pak Number", default=30, min=1, max=99,
                     description="Installed as pakNN_dir.vpk; a lower number loads first and wins over other mods for the same hero")
    remove_meshes: StringProperty(name="Replace Meshes", default="",
                                  description="Comma-separated hero render meshes to replace (empty: everything but weapons and ability props)")
    install: BoolProperty(name="Install into Deadlock", default=True)
    step: EnumProperty(name="Step", items=[(k, n, d) for k, n, d in [
        ("1", "1", "Hero"), ("2", "2", "Character"), ("3", "3", "Outfit"), ("4", "4", "Bone map"),
        ("5", "5", "Fit"), ("6", "6", "Export + Build"), ("ALL", "All", "Show every step")]], default="ALL",
        description="Which step to show")


def settings(context):
    return context.scene.hero_swap


def selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"]


def addon_name(s):
    return s.addon_name or (f"{s.hero}_swap" if s.hero else "hero_swap")


def content_dir(s):
    return os.path.join(bpy.path.abspath(prefs().csdk_dir), "content", "citadel_addons", addon_name(s))


def export_dir(s):
    if s.export_dir:
        return bpy.path.abspath(s.export_dir)
    return os.path.join(content_dir(s), *s.model_dir.split("/"), "dmx")


def body_height(arm):
    zs = [(arm.matrix_world @ b.head_local).z for b in arm.data.bones]
    return (max(zs) - min(zs)) if zs else 1.0


def save_blend():
    if bpy.data.filepath:
        bpy.ops.wm.save_mainfile()


# --- 0. hero -----------------------------------------------------------------------------------

class HEROSWAP_OT_refresh_heroes(bpy.types.Operator):
    """Read the hero list from the game (scripts/heroes.vdata)"""
    bl_idname = "heroswap.refresh_heroes"
    bl_label = "Refresh Hero List"

    def execute(self, context):
        global _HERO_ITEMS
        _HERO_ITEMS = []
        hero_items(None, context)
        self.report({"INFO"}, f"{len(_HERO_ITEMS)} heroes")
        return {"FINISHED"}


class HEROSWAP_OT_import_hero(bpy.types.Operator):
    """Extract the hero from Deadlock with Source 2 Viewer and import its skeleton (body meshes come in hidden, for reference)"""
    bl_idname = "heroswap.import_hero"
    bl_label = "Import Hero Skeleton"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s, p = settings(context), prefs()
        if not s.hero:
            self.report({"ERROR"}, "Pick a hero first")
            return {"CANCELLED"}
        work = bpy.path.abspath(p.work_dir)
        try:
            vmdl = build.extract_hero(bpy.path.abspath(p.vrf_cli), bpy.path.abspath(p.deadlock_dir),
                                      f"{s.model_dir}/{s.hero}.vmdl", os.path.join(work, f"{s.hero}_src"))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        dmx = build.body_dmx(vmdl)
        if not dmx:
            self.report({"ERROR"}, f"No body DMX found next to {vmdl}")
            return {"CANCELLED"}
        if not s.remove_meshes:
            s.remove_meshes = ", ".join(build.guess_body_meshes([n for n, _ in build.render_meshes(vmdl)]))
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = None
        before = set(bpy.data.objects)
        bpy.ops.import_scene.smd(filepath=dmx, doAnim=False, makeCamera=False, append="NEW_ARMATURE", upAxis="Z")
        new = [o for o in bpy.data.objects if o not in before]
        arm = next((o for o in new if o.type == "ARMATURE"), None)
        if arm is None:
            self.report({"ERROR"}, "Source Tools did not make an armature")
            return {"CANCELLED"}
        col = bpy.data.collections.get(f"hero_{s.hero}") or bpy.data.collections.new(f"hero_{s.hero}")
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
                if c != context.scene.collection and not c.all_objects and c.name in bpy.data.collections:
                    bpy.data.collections.remove(c)
            col.objects.link(o)
            if o.type == "MESH":
                o.hide_set(True)
                o.hide_render = True
                if o.name.startswith("smd_bone_vis"):
                    o.hide_viewport = True
        arm.name = f"{s.hero}_skeleton"
        s.hero_rig = arm
        if not s.addon_name:
            s.addon_name = addon_name(s)
        self.report({"INFO"}, f"Imported {arm.name} ({len(arm.data.bones)} bones) from {os.path.basename(dmx)}")
        return {"FINISHED"}


# --- 1. character ------------------------------------------------------------------------------

class HEROSWAP_OT_cleanup(bpy.types.Operator):
    """Make the Character Rig's meshes ready to fit: fix empty Armature modifiers and bone parenting, delete geometry hidden by Mask modifiers, drop vertex groups that aren't bones"""
    bl_idname = "heroswap.cleanup"
    bl_label = "Clean Up Character Meshes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not s.character_rig:
            self.report({"ERROR"}, "Pick the Character Rig first")
            return {"CANCELLED"}
        sel = selected_meshes(context)
        rep = core.cleanup_meshes(s.character_rig, sel or None)
        notes = sum(len(v) for v in rep.values())
        self.report({"INFO"}, f"Cleaned {len(rep)} meshes ({notes} fixes)")
        return {"FINISHED"}


class HEROSWAP_OT_load_textures(bpy.types.Operator):
    """Give the selected meshes' grey (untextured) Source materials their textures: finds each .vmt and its .vtf files in the Materials Folder, ignoring upper/lower case"""
    bl_idname = "heroswap.load_textures"
    bl_label = "Load Source Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        root = bpy.path.abspath(s.source_materials)
        if not os.path.isdir(root):
            self.report({"ERROR"}, "Pick the model's Materials Folder first")
            return {"CANCELLED"}
        objs = selected_meshes(context) or (core.rig_meshes(s.character_rig) if s.character_rig else [])
        done = core.load_source_materials(objs, root)
        self.report({"INFO"}, f"Textured {len(done)} materials: {', '.join(done)}")
        return {"FINISHED"}


class HEROSWAP_OT_delete_materials(bpy.types.Operator):
    """Delete the faces of the selected meshes that use the Delete Materials (e.g. NSFW parts)"""
    bl_idname = "heroswap.delete_materials"
    bl_label = "Delete Faces with These Materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        names = [n.strip() for n in settings(context).delete_materials.split(",") if n.strip()]
        n = core.delete_material_faces(selected_meshes(context), names)
        self.report({"INFO"}, f"Deleted {n} faces")
        return {"FINISHED"}


class HEROSWAP_OT_bake_eyes(bpy.types.Operator):
    """Turn the selected meshes' projected Source eyes into UV-mapped textures that survive the fit and export. Run it before the fit"""
    bl_idname = "heroswap.bake_eyes"
    bl_label = "Convert Source Eyes to Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        out = bpy.path.abspath(s.eye_texture_dir) or os.path.join(content_dir(s), "materials", *s.model_dir.split("/"), s.skin_name)
        made = [m.name for o in selected_meshes(context) for m in core.bake_source_eyes(o, out)]
        if not made:
            self.report({"ERROR"}, "No SourceIO eyes (eye_N_* properties) on the selected meshes")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Made {', '.join(made)}")
        return {"FINISHED"}


# --- 2. outfit ---------------------------------------------------------------------------------

class HEROSWAP_OT_bonemerge(bpy.types.Operator):
    """Move the selected skinned meshes (an outfit made for this rig) onto the Character Rig, matching bones by name"""
    bl_idname = "heroswap.bonemerge"
    bl_label = "Bone-merge Selected onto Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        target = s.outfit_rig if context.scene.hero_swap_merge_to_outfit else s.character_rig
        if not target:
            self.report({"ERROR"}, "Pick the rig to merge onto first")
            return {"CANCELLED"}
        done = [o.name for o in selected_meshes(context) if core.bonemerge(o, target)]
        self.report({"INFO"}, f"Bone-merged {len(done)} mesh(es) onto {target.name}")
        return {"FINISHED"}


class HEROSWAP_OT_fit_outfit(bpy.types.Operator):
    """Fit an outfit made for another body and rig onto the character: pose its rig onto the character's skeleton, re-seat it on the character's skin, and copy the skin's weights"""
    bl_idname = "heroswap.fit_outfit"
    bl_label = "Fit Outfit to Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.outfit_rig and s.character_rig and s.body_mesh):
            self.report({"ERROR"}, "Pick the Outfit Rig, the Character Rig and the character's Body Mesh")
            return {"CANCELLED"}
        meshes = [o for o in core.rig_meshes(s.outfit_rig) if o != s.outfit_base_body]
        if not meshes:
            self.report({"ERROR"}, "No meshes are skinned to the Outfit Rig")
            return {"CANCELLED"}
        src = rigmap.Rig.from_armature(s.outfit_rig)
        dst = rigmap.Rig.from_armature(s.character_rig)
        bm, sj, dj = rigmap.build_bonemap(src, dst)
        sides = [dj.get("thigh_L") or dj.get("upperarm_L"), dj.get("thigh_R") or dj.get("upperarm_R")]
        bm["dst_side_bones"] = sides
        if "scale_bones" not in bm and "upperarm_L" in sj and "pelvis" in sj and "upperarm_L" in dj and "pelvis" in dj:
            bm["scale_bones"] = [[sj["pelvis"], sj["upperarm_L"]], [dj["pelvis"], dj["upperarm_L"]]]
        bm.pop("height_bones", None)
        fit_meshes = meshes + ([s.outfit_base_body] if s.outfit_base_body else [])
        core.fit_to_hero(s.outfit_rig, fit_meshes, s.character_rig, bm, clearance=False)
        h = body_height(s.character_rig)
        reach = s.outfit_reach or 0.04 * h
        gap = s.outfit_gap or 0.005 * h
        res = core.transfer_to_body(meshes, s.body_mesh, s.outfit_base_body, reach=reach, gap=gap)
        n = core.copy_body_weights(meshes, s.body_mesh, reach=reach * 0.5) if s.copy_weights else 0
        if s.outfit_base_body:
            s.outfit_base_body.hide_set(True)
            s.outfit_base_body.hide_render = True
        s.outfit_rig.hide_set(True)
        cols = s.character_rig.users_collection
        for o in meshes:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            cols[0].objects.link(o)
        s.outfit_prefix = os.path.commonprefix([o.name for o in meshes])
        moved = sum(m for m, _ in res.values())
        self.report({"INFO"}, f"Fitted {len(meshes)} outfit pieces onto {s.character_rig.name}: {moved} points re-seated, {n} took body weights")
        return {"FINISHED"}


class HEROSWAP_OT_hide_skin(bpy.types.Operator):
    """Delete the Body Mesh faces that the selected outfit pieces (or all outfit pieces on the character) fully cover, so skin can't show through tight or low-poly clothes. Openings in the clothes keep their skin"""
    bl_idname = "heroswap.hide_skin"
    bl_label = "Delete Skin Under Outfit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.body_mesh and s.character_rig):
            self.report({"ERROR"}, "Pick the Character Rig and its Body Mesh")
            return {"CANCELLED"}
        outfit = [o for o in selected_meshes(context) if o != s.body_mesh]
        if not outfit:
            outfit = [o for o in core.rig_meshes(s.character_rig) if o.name.lower().startswith(s.outfit_prefix.lower())] if s.outfit_prefix else []
        if not outfit:
            self.report({"ERROR"}, "Select the outfit pieces (or set Outfit Prefix)")
            return {"CANCELLED"}
        dist = s.skin_cover or 0.02 * body_height(s.character_rig)
        # the neck, head and everything under them (jaw, tongue, ears) are never touched
        rig = rigmap.Rig.from_armature(s.character_rig)
        neck = rigmap.find_joints(rig).get("neck")
        protect = {neck, *rig.descendants(neck)} if neck else set()
        pushed, n = core.delete_covered_skin(s.body_mesh, outfit, dist, protect=protect)
        self.report({"INFO"}, f"Pushed {pushed} skin points in under the cloth, deleted {n} fully covered skin faces")
        return {"FINISHED"}


class HEROSWAP_OT_apply_skin(bpy.types.Operator):
    """Swap materials on the selected meshes using the Skin Swaps list"""
    bl_idname = "heroswap.apply_skin"
    bl_label = "Apply Skin Swaps"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pairs = [p.split("=", 1) for p in settings(context).skin_swaps.split(",") if "=" in p]
        n = core.apply_skin(selected_meshes(context), {a.strip(): b.strip() for a, b in pairs})
        self.report({"INFO"}, f"Swapped {n} material slot(s)")
        return {"FINISHED"}


class HEROSWAP_OT_align_regions(bpy.types.Operator):
    """Align selected clothing regions to matching skin weights, then inspect the fit before weight transfer"""
    bl_idname = "heroswap.align_regions"
    bl_label = "Align Gloves / Weighted Regions"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        meshes = [o for o in selected_meshes(context) if o != s.alignment_reference]
        if not s.alignment_reference or not meshes:
            self.report({"ERROR"}, "Choose Alignment Skin and select the clothing meshes")
            return {"CANCELLED"}
        patterns = [p.strip() for p in s.alignment_patterns.split(',') if p.strip()]
        result = core.align_weighted_regions(meshes, s.alignment_reference, patterns, s.alignment_strength, s.alignment_scale)
        moved = sum(r['vertices'] for r in result.values())
        self.report({"INFO"}, f"Aligned {moved} vertices. Inspect fingers and transfer skin weights after fitting")
        return {"FINISHED"}


class HEROSWAP_OT_seat_edges(bpy.types.Operator):
    """Bring open clothing edges in the chosen bone regions against Alignment Skin and blend the adjustment into the panels"""
    bl_idname = "heroswap.seat_edges"
    bl_label = "Seat Clothing Edges Against Skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        meshes = [o for o in selected_meshes(context) if o != s.alignment_reference]
        if not s.alignment_reference or not meshes:
            self.report({"ERROR"}, "Choose Alignment Skin and select the clothing meshes")
            return {"CANCELLED"}
        patterns = [p.strip() for p in s.alignment_patterns.split(',') if p.strip()]
        gap = s.outfit_gap or max(s.alignment_reference.dimensions) * .002
        result = core.seat_clothing_edges(meshes, s.alignment_reference, patterns, gap)
        self.report({"INFO"}, f"Seated {sum(r['edge_vertices'] for r in result.values())} edge vertices; inspect front, top and posed views")
        return {"FINISHED"}


class HEROSWAP_OT_conform_shape(bpy.types.Operator):
    """Push selected clothing out of the current body shape in rest pose, preserving editable shape keys"""
    bl_idname = "heroswap.conform_shape"
    bl_label = "Conform Clothing to Current Shape"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        meshes = [o for o in selected_meshes(context) if o != s.body_mesh]
        if not s.body_mesh or not meshes:
            self.report({"ERROR"}, "Choose New Body and select the clothing")
            return {"CANCELLED"}
        h = max(s.body_mesh.dimensions)
        try:
            result = core.conform_current_shape(meshes, s.body_mesh,
                s.outfit_reach or .1 * h, s.outfit_gap or .003 * h)
        except ValueError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Adjusted {sum(result.values())} clothing vertices; inspect the fit and pose")
        return {"FINISHED"}


class HEROSWAP_OT_sync_shape_keys(bpy.types.Operator):
    """Drive the selected meshes' shape keys from the Body Mesh's keys of the same name, creating missing ones"""
    bl_idname = "heroswap.sync_shape_keys"
    bl_label = "Match Outfit to Body Shape Keys"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not s.body_mesh or not s.body_mesh.data.shape_keys:
            self.report({"ERROR"}, "Pick a Body Mesh that has shape keys")
            return {"CANCELLED"}
        res = core.sync_shape_keys(s.body_mesh, selected_meshes(context), s.create_missing, s.max_distance)
        driven = sum(d for d, _ in res.values())
        created = sum(c for _, c in res.values())
        self.report({"INFO"}, f"{len(res)} mesh(es): {created} keys created, {driven} keys linked to the body")
        return {"FINISHED"}


# --- 3. bone map -------------------------------------------------------------------------------

ROLE_ORDER = ["pelvis", "spine", "neck", "head", "jaw"] + [f"{r}_{s}" for s in rigmap.SIDES for r in (
    "clavicle", "upperarm", "forearm", "hand", "thigh", "calf", "foot", "toe", "toe_end", "eye")] + [
    f"{f}_{k}_{s}" for s in rigmap.SIDES for f in rigmap.FINGERS for k in range(3)]


def joints_to_rows(s, sj, dj):
    s.rows.clear()
    for role in ROLE_ORDER:
        if role == "spine":
            n = max(len(sj.get("spine", [])), len(dj.get("spine", [])))
            for i in range(n):
                r = s.rows.add()
                r.role = f"spine{i}"
                r.src = (sj.get("spine") or [])[i] if i < len(sj.get("spine", [])) else ""
                r.dst = (dj.get("spine") or [])[i] if i < len(dj.get("spine", [])) else ""
            continue
        if role in sj or role in dj:
            r = s.rows.add()
            r.role, r.src, r.dst = role, sj.get(role, ""), dj.get(role, "")


def rows_to_joints(s):
    sj, dj = {}, {}
    for r in s.rows:
        if r.role.startswith("spine"):
            if r.src:
                sj.setdefault("spine", []).append(r.src)
            if r.dst:
                dj.setdefault("spine", []).append(r.dst)
            continue
        if r.src:
            sj[r.role] = r.src
        if r.dst:
            dj[r.role] = r.dst
    return sj, dj


def default_bonemap_path(s):
    base = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else bpy.path.abspath(prefs().work_dir)
    name = (s.character_rig.name if s.character_rig else "character").replace(" ", "_")
    return os.path.join(base, f"bonemap_{name}_to_{s.hero or 'hero'}.json")


class HEROSWAP_OT_autodetect(bpy.types.Operator):
    """Find the body joints of both rigs by name and hierarchy, fill the table below, and write a bone map"""
    bl_idname = "heroswap.autodetect"
    bl_label = "Auto-detect Bone Map"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.character_rig and s.hero_rig):
            self.report({"ERROR"}, "Pick both the Character Rig and the Hero Skeleton")
            return {"CANCELLED"}
        src, dst = rigmap.Rig.from_armature(s.character_rig), rigmap.Rig.from_armature(s.hero_rig)
        sj, dj = rigmap.find_joints(src), rigmap.find_joints(dst)
        joints_to_rows(s, sj, dj)
        missing = [r.role for r in s.rows if not r.src and not r.role.startswith(("spine", "eye", "toe_end", "jaw"))]
        bpy.ops.heroswap.save_bonemap()
        self.report({"WARNING"} if missing else {"INFO"},
                    f"Found {sum(1 for r in s.rows if r.src)} joints" + (f"; not found: {', '.join(missing)}" if missing else ""))
        return {"FINISHED"}


class HEROSWAP_OT_save_bonemap(bpy.types.Operator):
    """Write the bone map JSON from the table (edit any row first)"""
    bl_idname = "heroswap.save_bonemap"
    bl_label = "Save Bone Map"

    def execute(self, context):
        s = settings(context)
        if not (s.character_rig and s.hero_rig and len(s.rows)):
            self.report({"ERROR"}, "Run Auto-detect first")
            return {"CANCELLED"}
        src, dst = rigmap.Rig.from_armature(s.character_rig), rigmap.Rig.from_armature(s.hero_rig)
        sj, dj = rows_to_joints(s)
        sj["tail_roots"] = rigmap.find_joints(src).get("tail_roots", [])
        bm, _, _ = rigmap.build_bonemap(src, dst, sj, dj)
        path = bpy.path.abspath(s.bonemap) if s.bonemap and s.bonemap != DEFAULT_BONEMAP else default_bonemap_path(s)
        with open(path, "w") as f:
            json.dump(bm, f, indent=1)
        s.bonemap = path
        self.report({"INFO"}, f"Bone map written to {path}")
        return {"FINISHED"}


class HEROSWAP_UL_rows(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        s = settings(context)
        split = layout.split(factor=0.36, align=True)
        split.label(text=item.role)
        # one column at a time keeps the bone names readable in the narrow sidebar
        prop, rig = ("src", s.character_rig) if s.map_side == "SRC" else ("dst", s.hero_rig)
        if rig:
            split.prop_search(item, prop, rig.data, "bones", text="")
        else:
            split.prop(item, prop, text="")


# --- 4. fit ------------------------------------------------------------------------------------

class HEROSWAP_OT_fit(bpy.types.Operator):
    """Fit every mesh skinned to the Character Rig onto the Hero Skeleton (keeps shape keys)"""
    bl_idname = "heroswap.fit"
    bl_label = "Fit Character to Hero"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.character_rig and s.hero_rig):
            self.report({"ERROR"}, "Pick both the Character Rig and the Hero Skeleton")
            return {"CANCELLED"}
        # meshes hidden from renders (alternate parts, NSFW extras) are left out
        meshes = [o for o in core.rig_meshes(s.character_rig) if not o.hide_render]
        if not meshes:
            self.report({"ERROR"}, "No meshes are skinned to the Character Rig")
            return {"CANCELLED"}
        if not s.bonemap:
            bpy.ops.heroswap.autodetect()
        keep = core.load_bonemap(s.jiggle_presets).get("keep_patterns", []) if s.keep_jiggle else []
        before = {b.name for b in s.hero_rig.data.bones}
        bm = core.load_bonemap(s.bonemap)
        try:
            if s.preserve_proportions:
                from . import proportions
                report = proportions.fit(s.character_rig, meshes, s.hero_rig, bm)
                yaw, scale = 0, report['scale']
            else:
                yaw, scale = core.fit_to_hero(s.character_rig, meshes, s.hero_rig, bm, keep_patterns=keep, keep_head=s.keep_head,
                                            preserve_torso=s.preserve_torso)
        except KeyError as e:
            self.report({"ERROR"}, f"Bone not found: {e}. Check the bone map matches both rigs")
            return {"CANCELLED"}
        added = [b.name for b in s.hero_rig.data.bones if b.name not in before]
        if s.preserve_proportions:
            added = [new for old, new in report['names'].items()
                     if core.keep_bone(old, keep) and old.lower() != 'tailbase']
        s.hero_rig["hero_swap_jiggle_bones"] = added
        tails = [n for n in added if "tail" in n.lower()]
        if tails and not s.tail_root:
            s.tail_root = min(tails, key=lambda n: len(s.hero_rig.data.bones[n].parent_recursive))
        s.character_rig.hide_set(True)
        col = bpy.data.collections.get(f"{s.hero}_swap") or bpy.data.collections.new(f"{s.hero}_swap")
        if col.name not in context.scene.collection.children:
            context.scene.collection.children.link(col)
        for o in meshes:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            col.objects.link(o)
        s.export_collection = col
        self.report({"INFO"}, f"Fitted {len(meshes)} meshes (turned {yaw * 57.2958:.0f} deg, scaled x{scale:.3f}), kept {len(added)} jiggle bones")
        return {"FINISHED"}


class HEROSWAP_OT_aim_tail(bpy.types.Operator):
    """Swing the Tail Root chain around the hero pelvis so it comes out of the back (run after the fit)"""
    bl_idname = "heroswap.aim_tail"
    bl_label = "Point Tail Backward"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.hero_rig and s.tail_root in s.hero_rig.data.bones):
            self.report({"ERROR"}, "Pick the Hero Skeleton and a Tail Root bone that is on it")
            return {"CANCELLED"}
        deg, n = core.aim_chain(s.hero_rig, s.tail_root)
        self.report({"INFO"}, f"Turned the tail {deg:.0f} deg ({n} vertices)")
        return {"FINISHED"}


class HEROSWAP_OT_flip_tail(bpy.types.Operator):
    """Turn the Tail Root chain upside down, if it curls the wrong way after Point Tail Backward"""
    bl_idname = "heroswap.flip_tail"
    bl_label = "Flip Tail Upside Down"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = settings(context)
        if not (s.hero_rig and s.tail_root in s.hero_rig.data.bones):
            self.report({"ERROR"}, "Pick the Hero Skeleton and a Tail Root bone that is on it")
            return {"CANCELLED"}
        n = core.flip_chain(s.hero_rig, s.tail_root)
        self.report({"INFO"}, f"Flipped the tail ({n} vertices)")
        return {"FINISHED"}


def jiggle_text(s):
    names = list(s.hero_rig.get("hero_swap_jiggle_bones", [])) if s.hero_rig else []
    if not names:
        return None, 0
    return core.jiggle_vmdl_block(names, core.load_bonemap(s.jiggle_presets)["presets"])


class HEROSWAP_OT_write_jiggle(bpy.types.Operator):
    """Write ModelDoc JiggleBone settings for the kept bones into a text block and the clipboard"""
    bl_idname = "heroswap.write_jiggle"
    bl_label = "Write Jiggle Bones for ModelDoc"

    def execute(self, context):
        text, n = jiggle_text(settings(context))
        if not text:
            self.report({"ERROR"}, "No kept jiggle bones yet: run Fit Character to Hero with Keep Jiggle Bones on")
            return {"CANCELLED"}
        block = bpy.data.texts.get("jiggle_bones.vmdl") or bpy.data.texts.new("jiggle_bones.vmdl")
        block.from_string(text)
        context.window_manager.clipboard = text
        self.report({"INFO"}, f"{n} jiggle bones written to the 'jiggle_bones.vmdl' text and the clipboard")
        return {"FINISHED"}


# --- 5. export ---------------------------------------------------------------------------------

class HEROSWAP_OT_export(bpy.types.Operator):
    """Write the materials (PNG + Deadlock .vmat), the jiggle bones and a ModelDoc DMX (Binary 9, Model 22) into the CSDK addon"""
    bl_idname = "heroswap.export"
    bl_label = "Export Model + Materials"

    def execute(self, context):
        s = settings(context)
        if not (s.export_collection and s.hero):
            self.report({"ERROR"}, "Pick the Export Collection (the fit makes it) and the hero")
            return {"CANCELLED"}
        if not hasattr(context.scene, "vs"):
            self.report({"ERROR"}, "Enable Blender Source Tools first")
            return {"CANCELLED"}
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        objs = [o for o in s.export_collection.all_objects if o.type == "MESH"]
        model_folder = os.path.join(content_dir(s), *s.model_dir.split("/"))
        mats = core.export_materials(objs, content_dir(s), s.hero, s.skin_name, s.model_dir)
        os.makedirs(model_folder, exist_ok=True)
        constraints = s.hero_rig.get('proportion_constraints') if s.hero_rig else None
        constraint_path = os.path.join(model_folder, 'proportion_constraints.kv3')
        if constraints:
            with open(constraint_path, 'w') as f:
                f.write(constraints)
        elif os.path.isfile(constraint_path):
            os.remove(constraint_path)
        with open(os.path.join(model_folder, "materials.json"), "w") as f:
            json.dump({"materials": mats}, f, indent=1)
        text, n = jiggle_text(s)
        jig = os.path.join(model_folder, "jiggle_bones.vmdl.txt")
        if text:
            with open(jig, "w") as f:
                f.write(text)
        elif os.path.isfile(jig):
            os.remove(jig)
        if s.hero_rig:
            context.view_layer.objects.active = s.hero_rig
            bpy.ops.object.mode_set(mode="EDIT")
            for eb in [b for b in s.hero_rig.data.edit_bones if b.name.startswith("_cloth")]:
                s.hero_rig.data.edit_bones.remove(eb)
            bpy.ops.object.mode_set(mode="OBJECT")
            s.hero_rig.data.pose_position = "REST"
        for c in bpy.data.collections:
            c.vs.mute = c != s.export_collection
        vs = context.scene.vs
        vs.export_format, vs.dmx_encoding, vs.dmx_format = "DMX", "9", "22_modeldoc"
        vs.material_path = f"{s.model_dir}/materials"
        vs.export_path = export_dir(s)
        os.makedirs(vs.export_path, exist_ok=True)
        bpy.ops.export_scene.smd(collection=s.export_collection.name)
        save_blend()
        self.report({"INFO"}, f"Exported {len(objs)} meshes, {len(mats)} materials, {n} jiggle bones to {vs.export_path}")
        return {"FINISHED"}


# --- 6. build ----------------------------------------------------------------------------------

class HEROSWAP_OT_build(bpy.types.Operator):
    """Compile materials (CSDK) and the model (CS2 Workshop Tools), pack the VPK and install it, all locally"""
    bl_idname = "heroswap.build"
    bl_label = "Build + Install Mod"

    def execute(self, context):
        s, p = settings(context), prefs()
        model_folder = os.path.join(content_dir(s), *s.model_dir.split("/"))
        dmx = os.path.join(export_dir(s), f"{s.export_collection.name if s.export_collection else s.hero + '_swap'}.dmx")
        if not os.path.isfile(dmx):
            self.report({"ERROR"}, f"Export first: {dmx} is missing")
            return {"CANCELLED"}
        cfg = {
            "hero": s.hero, "model_dir": s.model_dir, "addon": addon_name(s),
            "deadlock": bpy.path.abspath(p.deadlock_dir), "csdk": bpy.path.abspath(p.csdk_dir),
            "cs2": bpy.path.abspath(p.cs2_dir), "vrf_cli": bpy.path.abspath(p.vrf_cli),
            "work_root": bpy.path.abspath(p.work_dir), "dmx": dmx,
            "materials_manifest": os.path.join(model_folder, "materials.json"),
            "jiggle": os.path.join(model_folder, "jiggle_bones.vmdl.txt"),
            "remove_meshes": [m.strip() for m in s.remove_meshes.split(",") if m.strip()],
            "pak": s.pak, "install": s.install,
        }
        constraint_path = os.path.join(model_folder, 'proportion_constraints.kv3')
        if os.path.isfile(constraint_path):
            cfg['proportion_constraints'] = constraint_path
        with open(os.path.join(model_folder, "build_config.json"), "w") as f:
            json.dump(cfg, f, indent=1)
        log = bpy.data.texts.get("hero_swap_build.log") or bpy.data.texts.new("hero_swap_build.log")
        log.clear()
        try:
            vpk = build.build(cfg, sink=lambda m: log.write(m + "\n"))
        except Exception as e:
            log.write(f"FAILED: {e}\n")
            self.report({"ERROR"}, f"Build failed: {e} (see the hero_swap_build.log text)")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Built and installed {vpk}")
        return {"FINISHED"}


# --- panels ------------------------------------------------------------------------------------

def button(layout, op, **kw):
    """layout.operator, drawn red when the guide highlights it (Scene.hero_swap_highlight)."""
    hl = [x.strip() for x in bpy.context.scene.hero_swap_highlight.split(",") if x.strip()]
    if op.bl_idname in hl:
        layout = layout.row(align=True)
        layout.alert = True
    return layout.operator(op.bl_idname, **kw)


class _Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hero Swap"


STEPS = [("1", "1", "Hero: pick the Deadlock hero and import its skeleton"),
         ("2", "2", "Character: clean up, textures, NSFW parts, eyes"),
         ("3", "3", "Outfit: bone-merge, or fit one made for another body"),
         ("4", "4", "Bone map: auto-detect and check the joints"),
         ("5", "5", "Fit: put the character on the hero skeleton"),
         ("6", "6", "Export the model and build the mod"),
         ("ALL", "All", "Show every step")]


class HEROSWAP_PT_main(_Panel, bpy.types.Panel):
    bl_label = "Hero Swap"

    def draw(self, context):
        p, s = prefs(), settings(context)
        col = self.layout.column(align=True)
        missing = [n for n in ("deadlock_dir", "csdk_dir", "cs2_dir", "vrf_cli", "work_dir") if not getattr(p, n)]
        if missing:
            col.label(text="Set the tool paths in the add-on preferences", icon="ERROR")
        col.operator("preferences.addon_show", text="Tool Paths...", icon="PREFERENCES").module = __package__
        col.separator()
        col.row(align=True).prop(s, "step", expand=True)


class _Step(_Panel):
    bl_parent_id = "HEROSWAP_PT_main"
    step = ""

    @classmethod
    def poll(cls, context):
        return settings(context).step in (cls.step, "ALL")


class HEROSWAP_PT_hero(_Step, bpy.types.Panel):
    bl_label = "1. Hero"
    step = "1"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "hero_choice", text="")
        button(row, HEROSWAP_OT_refresh_heroes, text="", icon="FILE_REFRESH")
        col.prop(s, "hero", text="Model")
        button(col, HEROSWAP_OT_import_hero, icon="IMPORT")
        col.prop(s, "hero_rig", text="Skeleton")


class HEROSWAP_PT_character(_Step, bpy.types.Panel):
    bl_label = "2. Character"
    step = "2"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        col.label(text="Import: SourceIO (.mdl), Source Tools", icon="INFO")
        col.label(text="(.dmx/.smd) or File > Append (.blend)")
        col.prop(s, "character_rig", text="Rig")
        button(col, HEROSWAP_OT_cleanup, icon="BRUSH_DATA")
        col.separator()
        col.prop(s, "source_materials", text="Materials")
        button(col, HEROSWAP_OT_load_textures, icon="TEXTURE")
        col.separator()
        col.prop(s, "delete_materials", text="Delete")
        button(col, HEROSWAP_OT_delete_materials, icon="TRASH")
        col.separator()
        col.prop(s, "eye_texture_dir", text="Eye PNGs")
        button(col, HEROSWAP_OT_bake_eyes, icon="HIDE_OFF")


class HEROSWAP_PT_outfit(_Step, bpy.types.Panel):
    bl_label = "3. Outfit"
    step = "3"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        box = col.box()
        box.label(text="Made for this rig (same bone names):")
        box.prop(context.scene, "hero_swap_merge_to_outfit", text="Merge onto Outfit Rig instead")
        button(box, HEROSWAP_OT_bonemerge, icon="MOD_ARMATURE")
        box = col.box()
        box.label(text="Made for another body / rig:")
        box.prop(s, "outfit_rig", text="Outfit Rig")
        box.prop(s, "outfit_base_body", text="Its Body")
        box.prop(s, "body_mesh", text="New Body")
        row = box.row(align=True)
        row.prop(s, "outfit_reach")
        row.prop(s, "outfit_gap")
        box.prop(s, "copy_weights")
        button(box, HEROSWAP_OT_fit_outfit, icon="MOD_CLOTH")
        box.prop(s, "skin_cover")
        button(box, HEROSWAP_OT_hide_skin, icon="MOD_MASK")
        col.separator()
        col.prop(s, "skin_swaps", text="Swaps")
        button(col, HEROSWAP_OT_apply_skin, icon="MATERIAL")
        row = col.row(align=True)
        row.prop(s, "create_missing", text="Create Missing")
        row.prop(s, "max_distance", text="Max")
        button(col, HEROSWAP_OT_sync_shape_keys, icon="SHAPEKEY_DATA")
        button(col, HEROSWAP_OT_conform_shape, icon="MOD_SHRINKWRAP")
        box = col.box()
        box.label(text="Adjust body or outfit independently:")
        box.prop(s, "shape_mesh")
        box.prop(s, "shape_filter")
        ob = s.shape_mesh or s.body_mesh
        if ob and ob.data.shape_keys:
            for key in ob.data.shape_keys.key_blocks[1:]:
                if s.shape_filter.lower() in key.name.lower():
                    box.prop(key, "value", text=key.name)
        else:
            box.label(text="Use a pre-fit mesh with shape keys")
        box = col.box()
        box.prop(s, "alignment_reference")
        box.prop(s, "alignment_patterns")
        box.prop(s, "alignment_strength")
        box.prop(s, "alignment_scale")
        button(box, HEROSWAP_OT_align_regions, icon="MOD_SHRINKWRAP")
        box.prop(s, "outfit_gap", text="Edge Clearance")
        button(box, HEROSWAP_OT_seat_edges, icon="MOD_SHRINKWRAP")


class HEROSWAP_PT_bonemap(_Step, bpy.types.Panel):
    bl_label = "4. Bone Map"
    step = "4"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        button(col, HEROSWAP_OT_autodetect, icon="VIEWZOOM")
        if len(s.rows):
            col.row(align=True).prop(s, "map_side", expand=True)
            col.template_list("HEROSWAP_UL_rows", "", s, "rows", s, "row_index", rows=12)
            button(col, HEROSWAP_OT_save_bonemap, icon="FILE_TICK")
        col.prop(s, "bonemap", text="File")


class HEROSWAP_PT_fit(_Step, bpy.types.Panel):
    bl_label = "5. Fit"
    step = "5"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        col.prop(s, "preserve_proportions")
        if not s.preserve_proportions:
            col.prop(s, "preserve_torso")
        col.prop(s, "keep_head")
        col.prop(s, "keep_jiggle")
        if s.keep_jiggle:
            col.prop(s, "jiggle_presets", text="Presets")
        button(col, HEROSWAP_OT_fit, icon="ARMATURE_DATA")
        col.separator()
        col.prop(s, "tail_root")
        row = col.row(align=True)
        button(row, HEROSWAP_OT_aim_tail, text="Point Backward", icon="FORWARD")
        button(row, HEROSWAP_OT_flip_tail, text="Flip", icon="ARROW_LEFTRIGHT")
        button(col, HEROSWAP_OT_write_jiggle, icon="PHYSICS")


class HEROSWAP_PT_export(_Step, bpy.types.Panel):
    bl_label = "6. Export + Build"
    step = "6"

    def draw(self, context):
        s = settings(context)
        col = self.layout.column()
        col.prop(s, "addon_name", text="Addon")
        col.prop(s, "export_collection", text="Meshes")
        col.prop(s, "skin_name", text="Skin Folder")
        col.prop(s, "export_dir", text="DMX Folder")
        button(col, HEROSWAP_OT_export, icon="EXPORT")
        col.separator()
        col.prop(s, "remove_meshes", text="Replace")
        row = col.row(align=True)
        row.prop(s, "pak", text="Pak")
        row.prop(s, "install", text="Install")
        button(col, HEROSWAP_OT_build, icon="PACKAGE")
        col.label(text="Builds locally: CSDK + CS2 Workshop Tools", icon="INFO")


classes = (HeroSwapPrefs, BoneMapRow, HeroSwapSettings,
           HEROSWAP_OT_refresh_heroes, HEROSWAP_OT_import_hero,
           HEROSWAP_OT_cleanup, HEROSWAP_OT_load_textures, HEROSWAP_OT_delete_materials, HEROSWAP_OT_bake_eyes,
           HEROSWAP_OT_bonemerge, HEROSWAP_OT_fit_outfit, HEROSWAP_OT_hide_skin, HEROSWAP_OT_apply_skin, HEROSWAP_OT_sync_shape_keys, HEROSWAP_OT_align_regions, HEROSWAP_OT_seat_edges, HEROSWAP_OT_conform_shape,
           HEROSWAP_OT_autodetect, HEROSWAP_OT_save_bonemap, HEROSWAP_UL_rows,
           HEROSWAP_OT_fit, HEROSWAP_OT_aim_tail, HEROSWAP_OT_flip_tail, HEROSWAP_OT_write_jiggle,
           HEROSWAP_OT_export, HEROSWAP_OT_build,
           HEROSWAP_PT_main, HEROSWAP_PT_hero, HEROSWAP_PT_character, HEROSWAP_PT_outfit, HEROSWAP_PT_bonemap,
           HEROSWAP_PT_fit, HEROSWAP_PT_export)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.hero_swap = PointerProperty(type=HeroSwapSettings)
    bpy.types.Scene.hero_swap_highlight = StringProperty(
        name="Highlight", default="", description="Operator ids to draw in red (for screenshots)")
    bpy.types.Scene.hero_swap_merge_to_outfit = BoolProperty(
        name="Merge onto Outfit Rig", default=False,
        description="Bone-merge the selection onto the Outfit Rig (e.g. the outfit's original body) instead of the Character Rig")


def unregister():
    del bpy.types.Scene.hero_swap_merge_to_outfit
    del bpy.types.Scene.hero_swap_highlight
    del bpy.types.Scene.hero_swap
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
